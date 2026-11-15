"""Run one frozen job in the remote development space; stdout is the receipt."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time

BASE = Path('/lpai')


def job_digest(job):
    return hashlib.sha256(json.dumps(job,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def locate(run_id, space_uid):
    if not re.fullmatch('[0-9a-f]{32}',run_id):raise ValueError('Invalid run identity')
    if (BASE/'.iterlab-space-uid').read_text(encoding='utf8').strip()!=space_uid:
        raise ValueError('Development space identity mismatch')
    root=BASE/'iterlab-runs'/run_id
    if root.resolve()!=root:raise ValueError('Invalid run root')
    return root


def atomic(path,value):
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf8') as stream:
        json.dump(value,stream,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
    temp.replace(path)


def process_identity(pid):
    # /proc field 22 distinguishes PID reuse, even if command names contain spaces.
    return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]


def control(mode,run_id,space_uid,expected):
    root=locate(run_id,space_uid)
    if mode=='submit':
        stage(root,run_id,space_uid)
    if not root.is_dir():
        print(json.dumps({'run_id':run_id,'space_uid':space_uid,'job_digest':expected,'state':'unknown'}));return
    job=json.loads((root/'.iterlab-job.json').read_text())
    if job_digest(job)!=expected or job['run_id']!=run_id or job['space_uid']!=space_uid:
        raise ValueError('Frozen job identity differs')
    if mode=='submit':
        # The directory is created exactly once. An interrupted submit never
        # retries a launch, even if the supervisor acknowledgement was lost.
        supervisor=root/'.iterlab-supervisor.py'
        if sha256(supervisor)!=job['worker_digest']:raise ValueError('Supervisor checksum differs')
        atomic(root/'.iterlab-launch.json',{'state':'launching','job_digest':expected})
        with (root/'.iterlab-supervisor.log').open('xb') as log:
            process=subprocess.Popen([sys.executable,str(supervisor),'supervise',run_id,space_uid,expected],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
        try:birth=process_identity(process.pid)
        except (OSError,IndexError):birth=None
        atomic(root/'.iterlab-launch.json',{'state':'started','job_digest':expected,'pid':process.pid,'birth':birth})
    receipt=root/'.iterlab-receipt.json'
    if receipt.is_file():
        if receipt.is_symlink() or receipt.stat().st_size>46*1024*1024:raise ValueError('Invalid terminal receipt')
        value=json.loads(receipt.read_text());value['job_digest']=expected
    else:
        value={'run_id':run_id,'space_uid':space_uid,'job_digest':expected,'state':'unknown'}
        try:
            launch=json.loads((root/'.iterlab-launch.json').read_text())
            if launch['job_digest']==expected and launch.get('birth') is not None and process_identity(launch['pid'])==launch['birth']:
                value['state']='running'
        except (OSError,KeyError,ValueError,IndexError):pass
    print(json.dumps(value,separators=(',',':')))


def collect(root, job, exit_code, state):
    artifacts = {}
    remaining = 32 * 1024 * 1024
    for name in ['stdout.log', 'stderr.log', *job['outputs']]:
        path = root / name
        if path.resolve() != path or not path.is_file():
            continue
        if name in {'stdout.log', 'stderr.log'}:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 512 * 1024))
                body = stream.read(512 * 1024)
        elif path.stat().st_size <= remaining:
            with path.open('rb') as stream:
                body = stream.read(remaining + 1)
            if len(body) > remaining:
                continue
            remaining -= len(body)
        else:
            continue
        artifacts[name] = base64.b64encode(body).decode('ascii')
    return {'run_id': job['run_id'], 'space_uid': job['space_uid'], 'state': state, 'exit_code': exit_code, 'files': artifacts}


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def stage(root,run_id,space_uid):
    parent=root.parent
    parent.mkdir(exist_ok=True)
    if parent.resolve() != parent:
        raise ValueError('Invalid run root')
    root.mkdir(mode=0o700 if os.name!='nt' else 0o777, exist_ok=False)
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|*') as archive:
        for member in archive:
            path = root / member.name
            if not member.isfile() or not path.resolve().is_relative_to(root):
                raise ValueError('Invalid snapshot entry')
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, path.open('xb') as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
    job = json.loads((root / '.iterlab-job.json').read_text(encoding='utf-8'))
    if job['run_id'] != run_id or job['space_uid'] != space_uid:
        raise ValueError('Snapshot identity mismatch')
    for name, expected in job['inputs'].items():
        if sha256(root / name) != expected:
            raise ValueError('Snapshot hash mismatch')
    return job


def main(run_id, space_uid, staged=False, expected=None):
    root=locate(run_id,space_uid)
    job=json.loads((root/'.iterlab-job.json').read_text()) if staged else stage(root,run_id,space_uid)
    if staged:
        if job_digest(job)!=expected:raise ValueError('Supervisor identity differs')
        # A supervisor may be launched only once; status reads never execute it.
        (root/'.iterlab-executing').open('x').close()
        for name,checksum in job['inputs'].items():
            path=root/name
            if path.resolve()!=path or sha256(path)!=checksum:raise ValueError('Frozen input changed before execution')

    started=time.monotonic()
    exit_code = None
    state = 'failed'
    process = None
    with (root / 'stdout.log').open('wb') as stdout, (root / 'stderr.log').open('wb') as stderr:
        try:
            gpu = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, check=True, timeout=15) if job['gpus'] else None
            visible=sum(line.startswith('GPU ') for line in gpu.stdout.splitlines()) if gpu else 0
            if visible < job['gpus']:
                raise ValueError('Visible GPU count is below approved use')
            env = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'PYTHONUNBUFFERED': '1'}
            env.update(ITERLAB_INPUT_DIR=str(root),ITERLAB_OUTPUT_DIR=str(root),ITERLAB_RUN_ID=job['run_id'])
            inherited=os.environ.get('CUDA_VISIBLE_DEVICES')
            devices=([v.strip() for v in inherited.split(',') if v.strip() and v.strip()!='-1']
                     if inherited is not None else [str(i) for i in range(visible)])
            if len(devices)<job['gpus']:raise ValueError('CUDA mask has fewer devices than approved use')
            # A shared space can enumerate a whole node. Restrict this child to
            # the explicit plan limit without changing the container or quota.
            env['CUDA_VISIBLE_DEVICES']=','.join(devices[:job['gpus']])
            process = subprocess.Popen([sys.executable, '-B', job['script'], *job['args']], cwd=root,
                                       env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                exit_code = process.wait(timeout=job['timeout'])
                state = 'completed' if exit_code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                state = 'timed_out'
            finally:
                # No child may keep writing after the receipt is published.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            if process is not None:
                raise  # No terminal proof if a started process could not be reaped.
            stderr.write((str(error) + '\n').encode('utf-8'))
    if state == 'completed':
        for name, expected in job['inputs'].items():
            path = root / name
            if path.resolve() != path or not path.is_file():
                state = 'invalid_inputs';break
            if sha256(path) != expected:
                state = 'invalid_inputs';break
    value=collect(root, job, exit_code, state);value['training_seconds']=round(time.monotonic()-started,3)
    result = json.dumps(value, separators=(',', ':'))
    tmp = root / '.iterlab-receipt.tmp'
    tmp.write_text(result, encoding='utf-8')
    tmp.replace(root / '.iterlab-receipt.json')
    print(result)


if __name__ == '__main__':
    if len(sys.argv)==3:main(sys.argv[1], sys.argv[2])
    elif sys.argv[1]=='supervise':main(sys.argv[2],sys.argv[3],staged=True,expected=sys.argv[4])
    else:control(*sys.argv[1:])
