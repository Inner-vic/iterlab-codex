"""Deterministic per-task result wrapper; no model or platform writes.

The caller must authorize the exact command position using task_policy. The SDK
preview and submit digest the same wrapped data; the shared component is intact.
"""
import base64
import copy
import json
from pathlib import Path
import re
import zlib

from . import task_policy
from .cloud_evidence import cloud_key,identifier


def root_path(value):
    if not isinstance(value,str) or not re.fullmatch(r'/(?:mnt|data|workspace|outputs?|home|lpai|tmp|root)/[A-Za-z0-9_./-]{1,650}',value):raise ValueError('运行目录须为明确Linux绝对路径，不接受Shell表达式')
    cloud_key(value.strip('/'))
    return value.rstrip('/')


def normalize(value):
    if not isinstance(value,dict) or not {'command_path','input_root','output_root'}<=set(value) or set(value)-{'command_path','input_root','output_root','mirror_paths','work_files','stage_work_files','layout'}:raise ValueError('结果执行器需command_path、input_root、output_root')
    try:path=task_policy.patch_path(value['command_path'])
    except ValueError as exc:
        raise ValueError('runtime.command_path是组件配置中的JSON路径（如/lpai_task/cmd或/lpai_task/roles/0/cmd），不是Python文件名。训练命令通过获批patch_file设置；包装路径仍须原task_policy许可，此字段不选择或上传源码。') from exc
    if not path.startswith('/lpai_task/'):raise ValueError('结果执行器只能包装一个明确训练入口')
    result={'command_path':path,'input_root':root_path(value['input_root']),'output_root':root_path(value['output_root'])}
    workspace=value.get('layout')=='workspace'
    if 'layout' in value:
        if not workspace or 'stage_work_files' in value:raise ValueError('未知工作包布局或混用了旧映射')
        result['layout']='workspace'
    if 'mirror_paths' in value:
        mirrors=value['mirror_paths']
        if not isinstance(mirrors,list) or len(mirrors)!=1 or set([path,*mirrors])!={'/lpai_task/cmd','/lpai_task/roles/0/cmd'}:raise ValueError('只允许明确的单角色总入口/角色入口镜像')
        result['mirror_paths']=mirrors
    legacy_mapping={}
    if 'work_files' in value:
        files=value['work_files']
        if not isinstance(files,list) or not 1<=len(files)<=8 or any(not isinstance(p,str) for p in files):
            raise ValueError('工作文件最多8个.py/.json/.txt；子目录包使用源路径=>云端路径显式映射')
        sources=[]
        for item in files:
            if workspace:
                from .work_package import workspace_path
                workspace_path(item)
                if item in sources:raise ValueError('工作包文件重复')
                sources.append(item)
                continue
            source,separator,target=item.partition('=>')
            if separator:
                if 'stage_work_files' in value:raise ValueError('同一工作包不能同时使用两种配置映射写法')
                legacy_mapping[source]=target
            ordinary=bool(re.fullmatch(r'implementation/[A-Za-z0-9_][A-Za-z0-9_.-]{0,75}\.(?:py|json|txt)',source))
            packaged=bool(re.fullmatch(r'research/[A-Za-z0-9_./-]{1,240}\.(?:py|json|txt)',source)
                and all(not part.startswith('.') for part in source.split('/')))
            mapping=value.get('stage_work_files',{})
            if (not ordinary and not packaged or source in sources
                    or packaged and not separator and (not isinstance(mapping,dict) or source not in mapping)):
                raise ValueError('源码须在implementation/或research/内；子目录包必须显式声明源路径=>implementation/目标，根目录只允许JSON配置')
            cloud_key(source);sources.append(source)
        result['work_files']=sorted(sources)
    if 'stage_work_files' in value or legacy_mapping:
        mapping=value.get('stage_work_files',legacy_mapping)
        files=result.get('work_files',[])
        def valid_target(k,v):
            if k not in files or not isinstance(v,str):return False
            target=(re.fullmatch(r'implementation/[A-Za-z0-9_][A-Za-z0-9_.-]{0,75}\.(?:py|json|txt)',v)
                or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,75}\.json',v))
            return bool(target and Path(k).suffix==Path(v).suffix)
        if not isinstance(mapping,dict) or not mapping or any(not valid_target(k,v) for k,v in mapping.items()):
            raise ValueError('工作包映射须引用已列来源，保持文件类型，目标为implementation/文件或根目录JSON配置')
        result['stage_work_files']={k:mapping[k] for k in sorted(mapping)}
        for name in mapping.values():cloud_key(name)
        if len({mapping.get(k,k) for k in files})!=len(files):raise ValueError('工作包目标文件名冲突，不覆盖其他输入')
    return result


def source(value):
    if not isinstance(value,dict) or set(value)!={'kind','name','uid','prefix','mount_root'} or value['kind']!='volume':raise ValueError('卷来源需确切名称、UID、相对前缀和运行挂载根')
    identifier(value['name']);identifier(value['uid']);cloud_key(value['prefix']);root_path(value['mount_root'])
    return {**value,'mount_root':value['mount_root'].rstrip('/')}


def result_source(value):
    if not isinstance(value,dict) or set(value)!={'kind','name','uid','prefix','mount_root','run_id'}:raise ValueError('需冻结的完整运行卷来源')
    source({k:v for k,v in value.items() if k!='run_id'})
    if not isinstance(value['run_id'],str) or not re.fullmatch('[a-f0-9]{32}',value['run_id']) or value['prefix'].split('/')[-1]!=value['run_id']:raise ValueError('卷回收仅允许确切运行独立前缀')
    return value


def authorize(policy,spec):
    normalized=normalize(spec)
    if not policy or any(p not in policy['allowed_patch_paths'] for p in [normalized['command_path'],*normalized.get('mirror_paths',[])]):raise ValueError('结果包装入口未在原task_policy授权，不自动修改任务入口')
    return normalized


def input_refs(expected):
    refs=list(expected['protocols'])+([expected['evaluation_contract']] if expected['evaluation_contract'] else [])+expected.get('execution_files',[])
    paths=[ref['path'] for ref in refs]
    if len(paths)!=len(set(paths)) or sorted(ref['path'] for ref in expected.get('execution_files',[]))!=expected['runtime'].get('work_files',[]):raise ValueError('冻结工作文件缺失、重复或和执行入口不一致')
    mapping=normalize(expected['runtime']).get('stage_work_files',{})
    if len({mapping.get(p,p) for p in paths})!=len(paths):raise ValueError('工作包目标与冻结协议冲突')
    return refs


def freeze(contract,policy,store=None,project_id=None,timeout_seconds=None):
    expected=contract['expected'];spec=expected.get('runtime')
    if not spec:return None
    authorize(policy,spec)
    writer=Path(__file__).with_name('result_receipt_writer.py').read_text(encoding='utf-8')
    driver=Path(__file__).with_name('cloud_runtime_worker.py').read_text(encoding='utf-8')
    refs=input_refs(expected)
    inputs={};size=0
    for ref in refs:
        cloud_key(ref['path'])
        if store is None:raise ValueError('结果执行器需要实际冻结协议字节，不接受只声明摘要')
        path=store.evidence_path(project_id,ref['path'])
        if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024:raise ValueError('单个协议快照不可用或超过64KiB')
        body=path.read_bytes();size+=len(body)
        import hashlib
        if size>256*1024 or len(refs)>16 or hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('协议工作包越界或内容已变化')
        inputs[ref['path']]=base64.b64encode(body).decode()
    result={'schema':1,'command_path':spec['command_path'],'contract':contract,'writer':writer,'driver':driver,'inputs':inputs,
            'gpu_probe':Path(__file__).with_name('gpu_preflight.py').read_text(encoding='utf-8')}
    if '/lpai_task/roles/1/cmd' in policy['allowed_patch_paths']:
        result['worker_paths']=['/lpai_task/roles/1/cmd']
    if timeout_seconds is not None:
        if type(timeout_seconds) is not int or not 1<=timeout_seconds<=604800:raise ValueError('云执行时限须来自有效的正式资源授权')
        result['timeout_seconds']=timeout_seconds
    return result


def apply(data,runtime):
    if not runtime:return copy.deepcopy(data)
    if not isinstance(runtime,dict) or set(runtime)-{'timeout_seconds','worker_paths','gpu_probe'}!={'schema','command_path','contract','writer','driver','inputs'} or runtime['schema']!=1:raise ValueError('结果执行工作包不完整')
    if 'timeout_seconds' in runtime and (type(runtime['timeout_seconds']) is not int or not 1<=runtime['timeout_seconds']<=604800):raise ValueError('结果执行时限无效')
    expected=runtime['contract']['expected'];spec=normalize(expected['runtime'])
    if runtime['command_path']!=spec['command_path']:raise ValueError('结果执行位置变化')
    from .result_receipt_writer import digest
    if digest(expected)!=runtime['contract']['sha256']:raise ValueError('结果执行合同摘要不符')
    refs=input_refs(expected)
    if not isinstance(runtime['inputs'],dict) or set(runtime['inputs'])!={r['path'] for r in refs}:raise ValueError('执行器缺少实际协议字节')
    total=0
    import hashlib
    for ref in refs:
        cloud_key(ref['path']);body=base64.b64decode(runtime['inputs'][ref['path']],validate=True);total+=len(body)
        if len(body)>64*1024 or total>256*1024 or hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('执行器协议字节不符或越界')
    # SDK executes only reviewed application code, never a supplied arbitrary
    # driver masquerading as a fixed runtime contract.
    for key,name in [('writer','result_receipt_writer.py'),('driver','cloud_runtime_worker.py')]:
        if runtime[key]!=Path(__file__).with_name(name).read_text(encoding='utf-8'):raise ValueError('结果执行器版本已变化，须重新准备，不热改旧任务')
    if 'gpu_probe' in runtime and runtime['gpu_probe']!=Path(__file__).with_name('gpu_preflight.py').read_text(encoding='utf-8'):
        raise ValueError('GPU预检版本已变化，须重新准备，不热改旧任务')
    result=copy.deepcopy(data);parts=spec['command_path'].strip('/').split('/');target=result
    try:
        for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
        command=target[parts[-1]]
    except (KeyError,IndexError,TypeError) as exc:raise ValueError('原任务没有该训练入口') from exc
    if not isinstance(command,str) or not command or len(command)>12000:raise ValueError('原训练入口不完整或过长')
    from .training_entry import require_actual
    require_actual(command)
    roles=result.get('lpai_task',{}).get('roles') or []
    distributed=bool(len(roles)==2 and roles[0].get('role')=='master' and roles[0].get('replica')==1
        and roles[1].get('role')=='worker' and type(roles[1].get('replica')) is int and roles[1]['replica']>0)
    mirrors=[]
    if spec.get('mirror_paths'):
        if not distributed and (len(roles)!=1 or roles[0].get('replica')!=1):raise ValueError('镜像入口只允许单角色单副本或明确master/worker配置')
        for mirror in spec['mirror_paths']:
            node=result;keys=mirror.strip('/').split('/')
            try:
                for key in keys[:-1]:node=node[int(key)] if isinstance(node,list) else node[key]
                if node[keys[-1]]!=command:raise ValueError('两个入口的原命令不一致，不能合并或覆盖')
            except (KeyError,IndexError,TypeError) as exc:raise ValueError('镜像入口缺失') from exc
            mirrors.append((node,keys[-1]))
    if '/roles/' in spec['command_path']:
        selected=int(parts[-2]);role=roles[selected]
        if role.get('replica')!=1:raise ValueError('结果生产入口必须是单个副本；分布式汇总需要明确主角色')
    elif roles and not distributed and (len(roles)!=1 or roles[0].get('replica')!=1):raise ValueError('多角色任务须明确单副本的结果汇总入口')
    if distributed:
        if runtime.get('worker_paths')!=['/lpai_task/roles/1/cmd']:raise ValueError('分布式worker入口未获原策略授权')
        if spec['command_path']!='/lpai_task/cmd' or spec.get('mirror_paths')!=['/lpai_task/roles/0/cmd']:
            raise ValueError('分布式结果入口必须是总入口与单副本master，不能在多个worker发布主收据')
        if roles[1].get('cmd')!=command:raise ValueError('分布式master/worker训练命令不一致')
    def gpu_need(role):
        resource=role.get('resource') or {}
        values=[resource[k] for k in ('Gpu','gpu') if k in resource]
        if not values:return None # Legacy non-resource unit fixtures; actual SDK admission validates resources.
        if any(type(value) is not int or value<0 for value in values):raise ValueError('GPU角色资源未确认')
        positive={value for value in values if value>0}
        if len(positive)>1:raise ValueError('GPU角色资源冲突')
        return next(iter(positive)) if positive else 0
    selected_role=roles[int(parts[-2])] if '/roles/' in spec['command_path'] else roles[0] if roles else {}
    needed=gpu_need(selected_role)
    if needed and 'gpu_probe' not in runtime:raise ValueError('GPU任务缺少冻结预检代码，请重新准备')
    payload={**runtime,'command':command,'requested_gpus':needed}
    encoded=base64.b64encode(zlib.compress(json.dumps(payload,ensure_ascii=False).encode())).decode()
    bootstrap="import base64,json,zlib; p=json.loads(zlib.decompress(base64.b64decode('"+encoded+"'))); exec(compile(p['driver'],'<iterlab-result-runtime>','exec'),{'__name__':'__iterlab_runtime__','PAYLOAD':p})"
    # Shell command contains application-owned encoded payload only. The original
    # entrypoint remains the subprocess command inside the immutable payload.
    import shlex
    wrapped='python3 -c '+shlex.quote(bootstrap)
    if len(wrapped.encode('utf-8'))>110*1024:raise ValueError('实际单个Linux命令参数过长，使用已挂载的数据而非嵌入大文件')
    target[parts[-1]]=wrapped
    for node,key in mirrors:node[key]=target[parts[-1]]
    if distributed:
        worker_needed=gpu_need(roles[1])
        if worker_needed and 'gpu_probe' not in runtime:raise ValueError('GPU worker缺少冻结预检代码，请重新准备')
        worker_payload={**payload,'worker_only':True,'requested_gpus':worker_needed}
        worker_encoded=base64.b64encode(zlib.compress(json.dumps(worker_payload,ensure_ascii=False).encode())).decode()
        worker_bootstrap="import base64,json,zlib; p=json.loads(zlib.decompress(base64.b64decode('"+worker_encoded+"'))); exec(compile(p['driver'],'<iterlab-result-runtime>','exec'),{'__name__':'__iterlab_runtime__','PAYLOAD':p})"
        worker_wrapped='python3 -c '+shlex.quote(worker_bootstrap)
        if len(worker_wrapped.encode('utf-8'))>110*1024:raise ValueError('分布式worker工作包过长')
        roles[1]['cmd']=worker_wrapped
    return result
