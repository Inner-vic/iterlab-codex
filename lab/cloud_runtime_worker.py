"""Standalone training-side result lifecycle. Embedded by cloud_runtime only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import time
import math
import re


class LinuxChildren:
    """Own new descendants of this standalone runtime, including new sessions.

    torchrun starts rank processes in independent sessions. Group-only killing
    cannot collect them. A Linux subreaper keeps orphaned ranks attributable to
    this runtime. Refuse pre-existing children rather than guess the origin of
    their future orphans. For the standalone, single-threaded container driver.
    """
    def __init__(self):
        import ctypes
        import threading
        if threading.active_count()!=1:raise RuntimeError('training_owner_requires_single_thread')
        self.libc=ctypes.CDLL(None,use_errno=True);previous=ctypes.c_int()
        if self.libc.prctl(37,ctypes.byref(previous),0,0,0):raise OSError(ctypes.get_errno(),'subreaper_unavailable')
        self.previous=previous.value;self.owned={};self.root=None
        if not Path('/proc',str(os.getpid()),'task',str(os.getpid()),'children').is_file():
            raise RuntimeError('training_children_index_unavailable')
        self.baseline={pid:ref[1] for pid in self.children(os.getpid()) if (ref:=self.identity(pid))}
        if self.baseline:raise RuntimeError('training_owner_has_preexisting_children')
        if self.libc.prctl(36,1,0,0,0):raise OSError(ctypes.get_errno(),'subreaper_unavailable')

    @staticmethod
    def identity(pid):
        try:
            fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
            return fields[0],fields[19]  # state and kernel start time, never argv/env
        except FileNotFoundError:return None

    @staticmethod
    def children(pid):
        try:return [int(n) for n in Path('/proc',str(pid),'task',str(pid),'children').read_text().split()]
        except FileNotFoundError:return []

    def bind(self,pid):
        ref=self.identity(pid)
        if ref:self.root=pid;self.owned[pid]=ref[1]

    def capture(self):
        # The driver launches only one training command. Orphaned descendants
        # become its new direct children, rather than unrelated system children.
        pending=[pid for pid in self.children(os.getpid())
                 if (ref:=self.identity(pid)) and self.baseline.get(pid)!=ref[1]]
        if self.root is not None:
            ref=self.identity(self.root)
            if ref and self.owned.get(self.root)==ref[1]:pending.append(self.root)
        seen=set()
        while pending:
            pid=pending.pop()
            if pid in seen:continue
            seen.add(pid);ref=self.identity(pid)
            if not ref:continue
            old=self.owned.get(pid)
            if old is not None and old!=ref[1]:continue  # recycled PID is not ours
            self.owned[pid]=ref[1];pending.extend(self.children(pid))

    def live(self):
        self.capture()
        return [pid for pid,start in self.owned.items()
                if (ref:=self.identity(pid)) and ref[0]!='Z' and ref[1]==start]

    def close(self):
        try:
            deadline=time.monotonic()+5
            while True:
                self.capture()
                for pid,start in list(self.owned.items()):
                    ref=self.identity(pid)
                    if not ref or ref[1]!=start:continue
                    if ref[0]!='Z':
                        descriptor=None
                        try:
                            if hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'):
                                try:descriptor=os.pidfd_open(pid)
                                except OSError as exc:
                                    import errno
                                    if exc.errno!=errno.ENOSYS:raise
                            if descriptor is not None:
                                # Pin the process, then recheck birth identity.
                                current=self.identity(pid)
                                if current and current[1]==start:signal.pidfd_send_signal(descriptor,signal.SIGKILL)
                            else:
                                current=self.identity(pid)
                                if current and current[1]==start:os.kill(pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        finally:
                            if descriptor is not None:os.close(descriptor)
                    try:os.waitpid(pid,os.WNOHANG)
                    except ChildProcessError:pass  # Parent still exists; subreaper adopts it next.
                if not self.live():
                    for pid,start in list(self.owned.items()):
                        ref=self.identity(pid)
                        if not ref or ref[1]!=start:continue
                        try:os.waitpid(pid,os.WNOHANG)
                        except ChildProcessError:pass
                    break
                if time.monotonic()>=deadline:raise RuntimeError('training_descendants_cleanup_unconfirmed')
                time.sleep(.02)
        finally:
            if self.libc.prctl(36,self.previous,0,0,0):raise RuntimeError('training_owner_restore_failed')


def gpu_readiness(payload,environment,outputs):
    """Fixed driver allocation on this already-allocated worker, never training."""
    requested=payload.get('requested_gpus')
    if not requested:return None
    if type(requested) is not int or not 1<=requested<=128:raise ValueError('invalid_gpu_resource')
    timeout=min(45,payload.get('timeout_seconds',45))
    try:
        response=subprocess.run([sys.executable,'-c',payload['gpu_probe'],str(requested)],env=environment,
            capture_output=True,timeout=timeout)
        if len(response.stdout)>65536:raise ValueError('oversized_gpu_probe')
        raw=json.loads(response.stdout)
        if (raw.get('schema')!='iterlab-cuda-preflight/v1' or type(raw.get('ready')) is not bool
                or raw.get('requested_gpus')!=requested or raw.get('fitting') is not False):raise ValueError('gpu_receipt_mismatch')
        report={k:raw[k] for k in ('schema','ready','requested_gpus','fitting')}
        report['ready']=raw['ready'] and response.returncode==0
        if report['ready']:
            devices=raw.get('devices')
            if not isinstance(devices,list) or len(devices)!=requested:raise ValueError('gpu_device_count_mismatch')
            report['devices']=[]
            for item in devices:
                if (not isinstance(item,dict) or not isinstance(item.get('uuid'),str) or not re.fullmatch(r'GPU-[a-fA-F0-9-]{36}',item['uuid'])
                    or item.get('allocation_bytes')!=4 or any(type(item.get(k)) is not int or item[k]<0 for k in ('index','free_bytes','total_bytes'))
                    or item['free_bytes']>item['total_bytes']):raise ValueError('gpu_allocation_not_verified')
                report['devices'].append({k:item[k] for k in ('index','uuid','free_bytes','total_bytes','allocation_bytes')})
        else:
            reason=raw.get('reason')
            report['reason']=reason if isinstance(reason,str) and re.fullmatch(r'(?:cu[A-Za-z0-9_]+:[0-9]{1,10}|[a-z_]{1,80})',reason) else 'gpu_probe_failed'
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        report={'schema':'iterlab-cuda-preflight/v1','ready':False,'requested_gpus':requested,'fitting':False,'reason':'gpu_probe_unconfirmed'}
    report['probe_sha256']=hashlib.sha256(payload.get('gpu_probe','').encode()).hexdigest()
    # One managed runtime receipt holds this proof. A separate extra file would
    # displace evidence from the existing seven-file/24-read recovery envelope.
    print('IterLab GPU preflight: '+('ready' if report['ready'] else report.get('reason','failed')))
    return report


def bounded_training(command,environment,seconds):
    """Bound this runtime's training tree; never stops the LPAI task API."""
    if os.name!='posix':raise ValueError('bounded_training_requires_linux')
    owner=LinuxChildren() if sys.platform.startswith('linux') else None
    process=None
    try:
        process=subprocess.Popen(command,shell=True,executable='/bin/bash',env=environment,start_new_session=True)
        if owner:owner.bind(process.pid)
        try:
            code=process.wait(timeout=seconds)
            if owner and owner.live():
                print('IterLab training exited with active descendants; collecting owned processes.',file=sys.stderr)
                return code or 125
            return code
        except subprocess.TimeoutExpired:
            if owner:owner.capture()  # Capture independent rank groups before killing parents.
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            process.wait(timeout=10)
            print('IterLab authorized training time limit reached.',file=sys.stderr)
            return 124
    finally:
        if owner:owner.close()


def run(payload):
    started=time.monotonic()
    contract=payload['contract'];expected=contract['expected'];spec=expected['runtime']
    namespace={'__name__':'__iterlab_receipt__'}
    exec(compile(payload['writer'],'<iterlab-receipt-writer>','exec'),namespace)
    outputs=Path(spec['output_root']);inputs=Path(spec['input_root'])
    worker_only=payload.get('worker_only') is True
    if worker_only:
        import socket
        worker_key=hashlib.sha256(socket.gethostname().encode()).hexdigest()[:20]
        inputs=inputs.parent/(inputs.name+'-worker-'+worker_key)
        outputs=outputs.parent/(outputs.name+'-workers')/worker_key
    stage=spec.get('stage_work_files',{})
    # Check actual protocol bytes before training, and again before publishing.
    refs=list(expected['protocols'])+([expected['evaluation_contract']] if expected['evaluation_contract'] else [])+expected.get('execution_files',[])
    try:
        if payload.get('inputs'):
            if any(p.is_symlink() for p in [inputs,*inputs.parents]):raise ValueError('symlink_input')
            inputs.mkdir(parents=True,exist_ok=False)
            for name,encoded in payload['inputs'].items():
                name=stage.get(name,name)
                if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:raise ValueError('unsafe_input')
                path=inputs/name;path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as stream:stream.write(base64.b64decode(encoded,validate=True))
        for ref in refs:
            if namespace['file_ref'](inputs,stage.get(ref['path'],ref['path']))['sha256']!=ref['sha256']:raise ValueError('protocol_mismatch')
        if any(p.is_symlink() for p in [outputs,*outputs.parents]):raise ValueError('symlink_output')
        outputs.parent.mkdir(parents=True,exist_ok=True)
        outputs.mkdir(exist_ok=False)  # Never overwrite/reuse another attempt.
    except Exception:
        print('IterLab preflight failed: protocol or isolated result directory unavailable.',file=sys.stderr)
        return 116
    environment=dict(os.environ);environment['ITERLAB_OUTPUT_DIR']=str(outputs)
    environment['ITERLAB_INPUT_DIR']=str(inputs)
    environment['ITERLAB_RUN_ID']=expected['run_id']
    stage_name='training';failure={};gpu_report=None
    try:
        gpu_report=gpu_readiness(payload,environment,outputs)
        if gpu_report and not gpu_report['ready']:
            code=118;state='gpu_environment_failed';failure={'failure_stage':'gpu_preflight','error_type':'GpuPreflightFailed'}
        else:
            if payload.get('timeout_seconds') is not None:
                remaining=payload['timeout_seconds']-(math.ceil(time.monotonic()-started) if gpu_report else 0)
                code=bounded_training(payload['command'],environment,remaining) if remaining>0 else 124
            else:
                process=subprocess.run(payload['command'],shell=True,executable='/bin/bash',env=environment)
                code=process.returncode
            state='training_timed_out' if code==124 else 'training_failed' if code else 'training_completed'
        if not code and not worker_only:
            stage_name='result_publication'
            namespace['write'](contract,inputs,outputs);state='results_published'
    except Exception as exc:
        code=117;state='results_incomplete'
        # Exception text can contain credentials or arbitrary training output.
        # Keep a finite lifecycle stage and Python type, never the raw message.
        failure={'failure_stage':stage_name,'error_type':type(exc).__name__}
    outcome={'schema':'iterlab-runtime/v1','run_id':expected['run_id'],'state':state,'exit_code':code,**failure}
    if gpu_report:outcome['gpu_preflight']=gpu_report
    with (outputs/'iterlab-runtime.json').open('x',encoding='utf-8') as stream:json.dump(outcome,stream)
    print('IterLab result lifecycle: '+state)
    return code if code>=0 else 128-code


if __name__=='__iterlab_runtime__':raise SystemExit(run(PAYLOAD))
