"""Official SDK task output reader child. Local archive only; no remote writes."""
from __future__ import annotations

from contextlib import redirect_stdout,redirect_stderr
import json
from pathlib import Path
import sys
import re

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import lpai_asset_bridge as bridge
from scripts.probe_cloud_outputs import inside_root
from lab.cloud_evidence import TransferBudget,identifier,resource_observation
from lab.task_output_files import collect,MAX_TOTAL,required_paths


def inspect_volume(request):
    if not isinstance(request,dict) or set(request)!={'operation','namespace','name'} or request['operation']!='inspect_volume':raise bridge.BoundaryError('仅接受确切卷名称及工作空间')
    name=identifier(request['name']);namespace=identifier(request['namespace'])
    budget=TransferBudget(max_total=2*1024*1024,max_calls=1,timeout=45)
    asset,conf,_=bridge.load_official()
    try:
        path='volumes/'+name;bridge.install_read_only_boundary(conf,path,budget,2*1024*1024)
        info=asset.resource(path,config=conf).get(force_refresh=True,namespace=namespace);meta=info.get('metadata',{})
        if meta.get('name')!=name or meta.get('namespace') not in (None,namespace):raise bridge.BoundaryError('卷名称/工作空间不符')
        return {'ok':True,'name':name,'uid':identifier(meta['uid']),'namespace':namespace,'platform_writes':0,'volume_files_read':False}
    finally:conf.session.close()


def execute(request,archive_root=None):
    if not isinstance(request,dict) or not {'task_uid','namespace','output_dir'}<=set(request) or set(request)-{'task_uid','namespace','output_dir','required_files','result_source'}:raise bridge.BoundaryError('需要原任务、空间和新暂存目录')
    required=required_paths(request.get('required_files',[]))
    selected=request.get('result_source')
    if selected:
        from lab.cloud_runtime import result_source
        result_source(selected)
        if 'iterlab-run.json' not in required:raise bridge.BoundaryError('卷回收必须核对运行收据')
    uid=identifier(request['task_uid']);namespace=identifier(request['namespace'])
    if not isinstance(request['output_dir'],str):raise bridge.BoundaryError('暂存目录必须是本机绝对路径')
    if archive_root is None:
        root=inside_root(Path(request['output_dir']),ROOT)
        if not root.is_relative_to((ROOT/'.evidence/cloud-probe').resolve()):raise bridge.BoundaryError('开发回收仅写新的独立证据目录')
    else:
        # This root is an application-owned argv, never accepted from the JSON
        # tool arguments. The child may write only this attempt's files folder.
        trusted=Path(archive_root)
        if not trusted.is_absolute() or trusted.is_symlink() or getattr(trusted,'is_junction',lambda:False)():raise bridge.BoundaryError('应用归档根无效')
        root=inside_root(Path(request['output_dir']),trusted)
        if root!=trusted/'files':raise bridge.BoundaryError('只能写本次归档文件目录')
    if root.name!='files' or not root.is_dir() or root.is_symlink() or any(root.iterdir()):raise bridge.BoundaryError('暂存目录非空或不安全')
    budget=TransferBudget(max_total=MAX_TOTAL+2*1024*1024,max_calls=3 if selected else 2,timeout=100)
    asset,conf,sources=bridge.load_official()
    client=None
    try:
        path='tasks/'+uid
        transport=conf.session.request
        bridge.install_read_only_boundary(conf,path,budget,2*1024*1024,download_sts=True)
        resource=asset.resource(path,config=conf)
        raw=resource.get(force_refresh=True,namespace=namespace)
        observation=resource_observation(raw,{'kind':'tasks','name':uid,'version':None,'uid':uid},namespace)
        if observation['observation']['uid']!=uid:raise bridge.BoundaryError('资产返回的UID不属于原任务')
        from asset.util import parse_s3_path
        if selected:
            # Switch to the one frozen volume AFTER verifying the original task.
            # Preserve shared counters/deadline, not the task-only HTTP wrapper.
            conf.session.request=transport
            path='volumes/'+selected['name'];bridge.install_read_only_boundary(conf,path,budget,2*1024*1024,download_sts=True)
            volume=asset.resource(path,config=conf);info=volume.get(force_refresh=True,namespace=namespace)
            meta=info.get('metadata',{})
            if meta.get('uid')!=selected['uid'] or meta.get('name')!=selected['name'] or meta.get('namespace') not in (None,namespace):raise bridge.BoundaryError('冻结数据卷名称/UID/空间不符')
            mappings=volume.get_s3_sts(use='download',storage='bos')
            if not isinstance(mappings,dict) or len(mappings)!=1:raise bridge.BoundaryError('卷下载映射不唯一，不猜目录')
            storage,sts=next(iter(mappings.items()));location=parse_s3_path(storage)
            client=volume._s3_client.create_refreshable_s3_client(sts,'download','bos',storage,None)
            location={**location,'path':'/'.join(p for p in [location['path'].rstrip('/'),selected['prefix']] if p)}
        else:
            location_raw=raw.get('spec',{}).get('dir',{}).get('path')
            if not isinstance(location_raw,str) or not location_raw:raise bridge.BoundaryError('原任务没有可读取的dir输出，不注册替代版本')
            location=parse_s3_path(location_raw)
            # Explicit download scope. Never use the upload fallback.
            client=resource._s3_client.get_s3_client(endpoint=location['endpoint'],use='download')
        prefix=location['path'].rstrip('/')+'/'
        def guard(params,model,**kwargs):
            if params.get('Bucket')!=location['bucket']:raise bridge.BoundaryError('对象读取越过原任务存储')
            if model.name=='ListObjectsV2':
                if params.get('Prefix')!=prefix or params.get('MaxKeys')!=128:raise bridge.BoundaryError('目录读取越界')
            elif model.name in {'HeadObject','GetObject'}:
                if not isinstance(params.get('Key'),str) or not params['Key'].startswith(prefix):raise bridge.BoundaryError('文件读取越过原任务目录')
            else:raise bridge.BoundaryError('对象客户端仅允许列目录、HEAD和GET')
        sends=[0]
        def before_send(request,**kwargs):
            budget.check();sends[0]+=1
            if request.method not in {'GET','HEAD'}:raise bridge.BoundaryError('文件回收不允许对象存储写操作')
            if sends[0]>26:raise bridge.BoundaryError('对象读取和重试超过累计范围')
        client.meta.events.register('before-parameter-build.s3',guard)
        client.meta.events.register('before-send.s3',before_send)
        file_budget=TransferBudget(max_total=MAX_TOTAL,max_calls=24,timeout=max(0.01,budget.deadline-budget.clock()))
        from asset.io.s3 import _decode_list_objects_v2_response
        class ReadClient:
            def list_objects_v2(self,**kwargs):
                return _decode_list_objects_v2_response(client.list_objects_v2(**kwargs),endpoint=location['endpoint'])
            def head_object(self,**kwargs):return client.head_object(**kwargs)
            def get_object(self,**kwargs):return client.get_object(**kwargs)
        manifest=collect(ReadClient(),location,uid,namespace,root,file_budget,required_files=required,result_source=selected)
        return {'ok':True,'manifest':manifest,'metadata_api_calls':budget.calls,'object_calls':file_budget.calls,
                'object_http_attempts':sends[0],'metadata_bytes_read':budget.bytes_read,'files_downloaded':len(manifest['files']),
                'platform_writes':0,'credentials_exported':False,'login_or_refresh_requested':False}
    finally:
        if client is not None:client.close()
        conf.session.close()


def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    args=sys.argv[1:];archive_root=None
    if len(args)==3 and args[:2]==['--stdio','--archive-root']:
        archive_root=args[2]
    elif args!=['--stdio']:
        print(json.dumps({'preflight_only':True,'platform_reads':0,'platform_writes':0}));return 0
    try:
        body=sys.stdin.read(16385)
        if len(body.encode())>16384:raise bridge.BoundaryError('文件回收请求越界')
        request=json.loads(body)
        with redirect_stdout(bridge.Discard()),redirect_stderr(bridge.Discard()):result=inspect_volume(request) if request.get('operation')=='inspect_volume' else execute(request,archive_root)
    except Exception as exc:result=bridge.public_error(exc)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__=='__main__':sys.exit(main())
