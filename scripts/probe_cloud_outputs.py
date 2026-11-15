"""Cloud provenance probe: offline by default, explicit reads, collection disabled."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
MAX_PROTOCOL=2*1024*1024
READ_TERMINAL={'succeeded','completed','success','failed','stopped','terminated','cancelled','canceled','error'}


class ProbeError(ValueError):
    def __init__(self,message,process=None):
        super().__init__(message);self.process=process


def dependencies():
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from lab import cloud_evidence
    return cloud_evidence


def preflight(root=ROOT):
    """No SDK imports, login, Store, subprocess, HTTP, directory or file creation."""
    names=['lab/cloud_evidence.py','lab/lpai_training.py','scripts/lpai_asset_bridge.py',
           'scripts/probe_cloud_outputs.py','scripts/run-quiet.ps1']
    missing=[];syntax=[]
    for name in names:
        path=root/name
        if not path.is_file():missing.append(name);continue
        if path.suffix=='.py':
            try:ast.parse(path.read_text(encoding='utf-8'),filename=name)
            except (SyntaxError,UnicodeError,OSError):syntax.append(name)
    python=root/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    return {'preflight_only':True,'python':sys.version.split()[0],'python_supported':sys.version_info>=(3,11),
            'platform':sys.platform,'missing_files':missing,'syntax_errors':syntax,
            'original_read_cli_present':bool(shutil.which('lpai-agent')),
            'official_sdk_python_file_present':python.is_file(),
            'sdk_imported':False,'login_or_refresh_requested':False,'store_opened':False,
            'processes_started':0,'platform_reads':0,'platform_writes':0,'files_downloaded':0,
            'mapping_state':'mapping_unconfirmed','automatic_collection_enabled':False,
            'meaning':'静态预检不检查登录/SDK运行，不证明Windows、云回收或科研闭环通过'}


def inside_root(path,root=ROOT):
    if not path.is_absolute():raise ProbeError('本机探针输入需要项目内的绝对路径')
    # Windows may spell the trusted temporary root with an 8.3 alias while
    # resolve() returns its long name. Keep the lexical containment check BEFORE
    # walking components; resolving the input first would hide a symlink/junction.
    original_root=Path(root);root=original_root.resolve();parts=None
    for spelling in (original_root,root):
        try:parts=path.relative_to(spelling).parts;break
        except ValueError:pass
    if parts is None:raise ProbeError('探针只处理当前项目内的明确导出文件')
    current=root
    for part in parts:
        if part in {'.','..'} or part.lower() in {'.data','.runtime','.git','.lpai','credentials'}:raise ProbeError('探针不打开研究库、SDK认证或系统目录')
        current=current/part
        reparse=False
        if os.name=='nt' and current.exists():
            reparse=bool(getattr(current.lstat(),'st_file_attributes',0)&0x400)
        if current.is_symlink() or getattr(current,'is_junction',lambda:False)() or reparse:raise ProbeError('探针输入/收据路径不能经过链接或junction')
    resolved=path.resolve()
    if not resolved.is_relative_to(root):raise ProbeError('探针路径不能逃出当前项目')
    if any(part.lower() in {'.data','.runtime','.git','.lpai','credentials'} for part in resolved.relative_to(root).parts):
        raise ProbeError('探针不通过重解析路径打开研究库或认证目录')
    return path


def load_origin(path,root=ROOT):
    path=inside_root(path,root)
    if not path.is_file() or path.suffix.lower()!='.json' or path.stat().st_size>16384:raise ProbeError('原公开身份文件需为项目内普通JSON，至多16KiB')
    with path.open('rb') as stream:data=stream.read(16385)
    if len(data)>16384:raise ProbeError('原身份文件在读取时超过范围')
    export=json.loads(data.decode('utf-8'))
    dependencies().validate_origin(export)
    return export


def receipt_directory(root=ROOT):
    parent=inside_root(root/'.evidence/cloud-probe',root)
    parent.mkdir(parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
    directory=parent/name;directory.mkdir(exist_ok=False)
    return directory


def write_json(path,value):
    # New immutable receipt names only; no overwrite or rewriting previous failures.
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def run_child(argv,*,deadline,input_data=None,max_output=MAX_PROTOCOL,allow_safe_error=False,cancel_event=None,text_result=False):
    """Bound protocol memory and process wait; clean only this child's owned tree."""
    from lab.owned_process import OwnedJob
    if time.monotonic()>=deadline:raise ProbeError('探针截止时间已到，未启动读取进程')
    if cancel_event is not None and cancel_event.is_set():raise ProbeError('服务已停止读取，未启动子进程；云任务不受影响')
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    start=time.monotonic();process=None;job=None;threads=[];stdout=bytearray();total=[0,0];failed=threading.Event()
    def reader(pipe,index):
        try:
            while True:
                block=pipe.read(16384)
                if not block:break
                total[index]+=len(block)
                if total[index]>max_output:failed.set();break
                if index==0:stdout.extend(block)  # stderr is discarded, not echoed or saved.
        except (OSError,ValueError):failed.set()
        finally:pipe.close()
    def writer():
        try:
            if input_data is not None:process.stdin.write(input_data);process.stdin.flush()
        except (OSError,ValueError):failed.set()
        finally:process.stdin.close()
    problem=None;cleanup_error=None;code=None
    try:
        process=subprocess.Popen(argv,stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT,shell=False,**flags)
        job=OwnedJob(process)
        if os.name!='nt' and job.group is None:
            # A very short-lived parent can exit before getpgid(pid). We know
            # this Popen was started in its own session; its descendants still
            # belong to this exact pid-as-pgid, never the application's group.
            job.group=process.pid
        for pipe,index in ((process.stdout,0),(process.stderr,1)):
            thread=threading.Thread(target=reader,args=(pipe,index),daemon=True);thread.start();threads.append(thread)
        if input_data is not None:
            thread=threading.Thread(target=writer,daemon=True);thread.start();threads.append(thread)
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():raise ProbeError('服务停止读取；仅清理自有读取子进程，不取消云训练')
            if failed.is_set():raise ProbeError('只读子进程响应超额或协议管道异常；未回显原输出')
            if time.monotonic()>=deadline:raise ProbeError('只读子进程到期；仅清理本探针，不取消云训练')
            time.sleep(0.01)
        code=process.returncode
        if time.monotonic()>=deadline:raise ProbeError('原只读进程在总请求期限内未确认结束，不把迟到退出视为通过')
    except (OSError,ValueError,ProbeError) as exc:
        problem=exc if isinstance(exc,ProbeError) else ProbeError('只读子进程未启动或未取得自有进程树；未切换执行入口')
    finally:
        # Cleanup is an explicit bounded grace after request wait, not claimed to
        # fit in the network deadline. A cleanup error cannot produce success.
        if job:
            try:job.terminate()
            except (OSError,ValueError):cleanup_error=True
            try:job.close()
            except (OSError,ValueError):cleanup_error=True
        if process and process.poll() is None:
            try:process.kill();process.wait(timeout=3)
            except (OSError,subprocess.TimeoutExpired):cleanup_error=True
        for thread in threads:thread.join(timeout=1)
        if any(thread.is_alive() for thread in threads):cleanup_error=True
        if process:
            for pipe in (process.stdout,process.stderr,process.stdin):
                if pipe is not None and not pipe.closed and not any(t.is_alive() for t in threads):pipe.close()
    stats={'exit_code':code,'stdout_bytes':total[0],'stderr_bytes_discarded':total[1],
           'elapsed_seconds':round(time.monotonic()-start,3),'owned_cleanup_confirmed':not cleanup_error}
    if cleanup_error:raise ProbeError('探针自有子进程/管道清理未确认；本次读取不算通过，不另起替代请求',stats)
    if problem:raise ProbeError(str(problem),stats)
    if failed.is_set():raise ProbeError('只读响应/管道超过范围或短写；未保存原输出',stats)
    if text_result:
        if code!=0:raise ProbeError('只读工具非零退出；未保存原诊断，不自动重试',stats)
        try:return {'text':stdout.decode('utf-8-sig')},stats
        except UnicodeError:raise ProbeError('只读文本不是UTF-8，不猜测转换或回显',stats) from None
    try:raw=json.loads(stdout.decode('utf-8-sig'))
    except (UnicodeError,ValueError):raise ProbeError('只读工具没有返回有效的有界UTF-8 JSON',stats) from None
    if code!=0:
        if (allow_safe_error and isinstance(raw,dict) and {'ok','error','message'}<=set(raw)
                and set(raw)<={'ok','error','message','http_status','stage','exception_type','projection'}
                and ('stage' not in raw or raw['stage'] in {'sdk_load','read_boundary','resource_get','response_projection'})
                and ('exception_type' not in raw or raw['exception_type'] in {'AttributeError','ModuleNotFoundError','ImportError','ValueError','KeyError','TypeError','RuntimeError','RequestError','S3RequestError','ClientError','BoundaryError','ReadTimeout','ConnectTimeout','Timeout','ConnectionError','ProxyError','SSLError'})
                and ('http_status' not in raw or type(raw['http_status']) is int and 100<=raw['http_status']<=599) and raw['ok'] is False
                and isinstance(raw['error'],str) and raw['error'] in {'auth_unavailable','access_denied','resource_unavailable','read_unconfirmed','boundary_rejected','network_timeout','network_unavailable'}):
            # Never persist or echo the child message, even though the bridge
            # normally emits only an authored one. Preserve an enumerated code.
            result={'ok':False,'error':raw['error'],**{key:raw[key] for key in ('http_status','stage','exception_type') if key in raw}}
            if 'projection' in raw:
                from lab.cloud_evidence import identifier,validate_shape
                projection=raw['projection']
                if not isinstance(projection,dict) or set(projection)!={'identity','response_shape'}:raise ProbeError('无效的公开投影诊断',stats)
                validate_shape(projection['response_shape'])
                identity=projection['identity']
                if not isinstance(identity,dict) or set(identity)-{'root','metadata'}:raise ProbeError('无效的公开身份诊断',stats)
                for fields in identity.values():
                    if not isinstance(fields,dict) or set(fields)!={'uid','name','namespace'}:raise ProbeError('无效的身份字段',stats)
                    for value in fields.values():
                        if not isinstance(value,dict):raise ProbeError('无效的身份类型',stats)
                        if set(value)=={'value'}:identifier(value['value'])
                        elif set(value)!={'type'} or value['type'] not in {'str','int','dict','list','NoneType','bool','other'}:raise ProbeError('无效的身份类型',stats)
                result['projection']=projection
            return result,stats
        raise ProbeError('原只读工具返回非零退出码；未保存SDK/CLI原诊断，不自动重试',stats)
    return raw,stats


def inspect(export,candidate,*,task_output_id=None,timeout=60,runner=run_child,root=ROOT):
    module=dependencies();origin=module.validate_origin(export)
    if candidate is not None:module.validate_candidate(candidate)
    if type(timeout) not in (int,float) or not 0<timeout<=120:raise ProbeError('探针总请求等待需为0..120秒')
    if task_output_id is not None:module.identifier(task_output_id)
    cli=shutil.which(origin['read_channel'])
    python=root/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    bridge=root/'scripts/lpai_asset_bridge.py'
    if not cli or Path(cli).suffix.lower() in {'.bat','.cmd','.ps1'}:raise ProbeError('原lpai-agent真实可执行入口不可用；未改用SDK/其他CLI')
    if candidate is not None or task_output_id is not None:
        if not python.is_file() or not bridge.is_file():raise ProbeError('官方资产SDK独立Python/窄桥未就绪；未安装或换入口')
    deadline=time.monotonic()+timeout
    result={'schema':'iterlab-cloud-probe/v1','started_at':datetime.now(timezone.utc).isoformat(),
            'origin_sha256':export['sha256'],'origin_authenticity_verified':False,
            'origin_validation':'stored_export_checksum_only_not_signature_or_grant',
            'requested_task_uid':origin['task_uid'],'namespace':origin['namespace'],
            'candidate':candidate,'mapping':module.mapping_assessment(None,None),
            'stages':{},'platform_write_attempts':0,'cloud_submissions':0,'model_calls':0,
            'files_downloaded':0,'automatic_collection_enabled':False,
            'live_acceptance':{'file_recovery':'not_run','research_loop':'not_run'},
            'timeout_semantics':'total_request_wait_with_bounded_owned_cleanup_grace'}
    stage='original_task'
    try:
        raw,stats=runner([cli,'--timeout','25','tasks','get','--namespace',origin['namespace'],'--uid',origin['task_uid']],deadline=deadline)
        if not isinstance(raw,dict) or raw.get('status')!='ok' or not isinstance(raw.get('data'),dict):raise ProbeError('原任务读取回执未核实')
        task=module.original_task_observation(raw['data'],export)
        result['stages']['original_task']={**task,'process':stats}
        if task['observation']['identity_state']!='original_uid_scope_checked':raise ProbeError('原任务读回缺少UID/namespace/组件/时间，未继续读取资产')
        if origin['platform_identity'] and task['observation']['account_scope_state']!='checked_against_original':
            raise ProbeError('原公开平台账号读回缺失，未按未确认账号继续读取资产')
        if task['observation']['phase'].lower() not in READ_TERMINAL:raise ProbeError('所选原任务不是明确终态；未继续回收或启动替代任务')
        targets=[('candidate_version',candidate)] if candidate is not None else []
        if task_output_id is not None:targets.append(('task_output_resource',{'kind':'tasks','name':task_output_id,'version':None,'uid':None}))
        for name,target in targets:
            stage=name
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ProbeError('原探针总请求期限已到；未重置期限或重新读任务')
            request={'operation':'inspect_resource','namespace':origin['namespace'],'candidate':target,
                     'timeout':min(remaining,60),'max_response_bytes':MAX_PROTOCOL}
            raw,stats=runner([str(python),'-I','-B',str(bridge),'--stdio-inspect'],deadline=deadline,
                input_data=json.dumps(request,ensure_ascii=False).encode('utf-8'),allow_safe_error=True)
            if isinstance(raw,dict) and raw.get('ok') is False:
                result['stages'][name]={'state':'read_unconfirmed','process':stats,
                    'error_category':raw.get('error') if isinstance(raw.get('error'),str) and raw.get('error') in {'auth_unavailable','access_denied','resource_unavailable','read_unconfirmed','boundary_rejected'} else 'read_unconfirmed'}
                raise ProbeError('明确候选资产只读失败；保留错误分类，不自动登录/重试/切换账号')
            if not isinstance(raw,dict) or raw.get('ok') is not True or not isinstance(raw.get('result'),dict):raise ProbeError('明确候选资产只读回执未核实；未猜版本或切换账号')
            observed=raw['result']
            module.validate_resource_projection(observed,target,origin['namespace'],module.SDK_SOURCES)
            result['stages'][name]={**observed,'process':stats}
        result['state']='inspected_mapping_unconfirmed'
        result['live_acceptance']['metadata_inspection']='read_returned_provenance_unconfirmed'
    except (ValueError,OSError,TimeoutError) as exc:
        result['state']='read_unconfirmed'
        result['failure']=str(exc) if isinstance(exc,ProbeError) else '原只读身份/响应未确认；未回显原正文，不自动重试'
        result['failed_stage']=stage
        if isinstance(exc,ProbeError) and exc.process is not None:
            result['stages'][stage]={'state':'read_unconfirmed','process':exc.process}
        result['live_acceptance']['metadata_inspection']='incomplete'
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    result['meaning']='本次仅元数据读取/形状探测；不是任务→确切版本来源认证，更不是文件回收或科研验收'
    return result

def main(argv=None):
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='strict')
    parser=argparse.ArgumentParser(description='默认静态预检；显式inspect只读原任务和所选候选，collect因来源未确认禁用。')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--preflight',action='store_true')
    mode.add_argument('--inspect',action='store_true')
    mode.add_argument('--collect',action='store_true')
    parser.add_argument('--origin',type=Path,help='当前项目内原公开身份JSON的绝对路径；不是认证配置')
    parser.add_argument('--asset-kind',choices=('models','datasets'))
    parser.add_argument('--asset-name')
    parser.add_argument('--asset-version')
    parser.add_argument('--candidate-uid',help='可选的预先明确候选版本UID；不会用该输入证明来源')
    parser.add_argument('--task-output-id',help='可选的明确tasks资源查询标识；不自动用任务名替换UID')
    parser.add_argument('--timeout',type=float,default=60,help='总请求等待0..120秒，另有有界自有进程清理宽限')
    parser.add_argument('--acknowledge-network-read',action='store_true',help='明确执行GET；不授权下载、登录、注册或训练')
    args=parser.parse_args(argv)
    if args.collect:
        # Fail before reading any file, starting a process, or creating receipts.
        print(json.dumps({'state':'mapping_unconfirmed','automatic_collection_enabled':False,
            'platform_reads':0,'files_downloaded':0,'platform_writes':0,
            'message':'当前未启用正式来源映射；collect不可执行，外部confirmed字段也不能解锁'},ensure_ascii=False))
        return 2
    if not args.inspect:
        if any((args.origin,args.asset_kind,args.asset_name,args.asset_version,args.candidate_uid,args.task_output_id,args.acknowledge_network_read)):
            parser.error('查询参数仅用于显式--inspect；默认预检不会访问平台')
        result=preflight();print(json.dumps(result,ensure_ascii=False,indent=2))
        return int(bool(result['missing_files'] or result['syntax_errors'] or not result['python_supported']))
    if not args.origin or not args.acknowledge_network_read:parser.error('inspect需原公开身份文件及--acknowledge-network-read；不会自动申请权限')
    provided=(args.asset_kind,args.asset_name,args.asset_version)
    if any(provided) and not all(provided) or args.candidate_uid and not all(provided):parser.error('正式候选查询需同时明确kind/name/version，不接受latest')
    if not 0<args.timeout<=120:parser.error('timeout需为0..120秒')
    candidate={'kind':args.asset_kind,'name':args.asset_name,'version':args.asset_version,'uid':args.candidate_uid} if all(provided) else None
    try:
        export=load_origin(args.origin)
        module=dependencies()
        if candidate is not None:module.validate_candidate(candidate)
        if args.task_output_id is not None:module.identifier(args.task_output_id)
        # No production Store or new observer is constructed. The origin should
        # be exported by the authenticated API of an already opened application.
        directory=receipt_directory()
        write_json(directory/'request.json',{'origin_sha256':export['sha256'],'task_uid':export['origin']['task_uid'],
            'namespace':export['origin']['namespace'],'candidate':candidate,'task_output_id':args.task_output_id,
            'timeout':args.timeout,'scope':'explicit_metadata_get_only_not_download_grant'})
        try:result=inspect(export,candidate,task_output_id=args.task_output_id,timeout=args.timeout)
        except (ValueError,OSError,TimeoutError) as exc:
            result={'state':'read_unconfirmed','mapping_state':'mapping_unconfirmed','automatic_collection_enabled':False,
                    'message':str(exc) if type(exc) is ProbeError else '原读取前置条件未确认；未回显原文件/配置，不重试',
                    'files_downloaded':0,'live_acceptance':{'file_recovery':'not_run','research_loop':'not_run'}}
        except KeyboardInterrupt:
            result={'state':'interrupted','mapping_state':'mapping_unconfirmed','automatic_collection_enabled':False,
                    'message':'本机用户中断探针；云训练未取消，未重试原GET；部分阶段不算通过'}
        write_json(directory/'receipt.json',result)
        print('探针收据：'+(directory/'receipt.json').relative_to(ROOT).as_posix())
        print(json.dumps({'state':result['state'],'mapping_state':'mapping_unconfirmed',
                          'files_downloaded':0,'research_loop_acceptance':'not_run'},ensure_ascii=False))
        return 3 if result['state']=='inspected_mapping_unconfirmed' else 2
    except (ValueError,OSError,TimeoutError):
        print('探针输入/前置条件未确认；未回显文件或认证正文，不自动重试。')
        return 2


if __name__=='__main__':sys.exit(main())
