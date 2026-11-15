"""Explicit official asset metadata child; never login, register, upload or infer provenance."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout,redirect_stderr
import hashlib
import json
from pathlib import Path
import sys
import sysconfig

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab.cloud_evidence import (SDK_SOURCES,TransferBudget,identifier,require_confirmed_mapping,
                                resource_observation,validate_candidate,response_shape)

# The reviewed SDK anchors live in the shared offline boundary module.
# Drift stops before import/configuration; no bypass or auto-upgrade flag.
MAX_REQUEST=16384
MAX_RESPONSE=2*1024*1024


class BoundaryError(ValueError):pass


class Discard:
    """Do not retain SDK stdout/stderr; even import diagnostics may contain secrets."""
    def write(self,text):return len(text)
    def flush(self):pass


def source_check(base):
    result={}
    for name,expected in SDK_SOURCES.items():
        path=base/name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base.resolve()) or path.stat().st_size>256*1024:
            result[name]={'matches_reviewed_source':False};continue
        with path.open('rb') as stream:data=stream.read(256*1024+1)
        if len(data)>256*1024:result[name]={'matches_reviewed_source':False};continue
        actual=hashlib.sha256(data).hexdigest()
        result[name]={'sha256':actual,'matches_reviewed_source':actual==expected}
    return result


def validate_request(request):
    if not isinstance(request,dict):raise BoundaryError('只读请求必须是JSON对象')
    if request.get('operation') in {'collect','head','range','list_files'}:
        require_confirmed_mapping(request.get('mapping'))
    if set(request)!={'operation','namespace','candidate','timeout','max_response_bytes'} or request['operation']!='inspect_resource':
        raise BoundaryError('仅启用明确候选的元数据读取')
    identifier(request['namespace']);validate_candidate(request['candidate'])
    if type(request['timeout']) not in (int,float) or not 0<request['timeout']<=60:raise BoundaryError('SDK子进程期限超过范围')
    if type(request['max_response_bytes']) is not int or not 1<=request['max_response_bytes']<=MAX_RESPONSE:
        raise BoundaryError('元数据响应超过2MiB范围')
    return request


def install_read_only_boundary(conf,path,budget,max_response,*,download_sts=False):
    """Use the official auth loader in memory, but never trigger login/refresh."""
    if getattr(conf,'_env',None)!='prod' or getattr(conf,'_grey',None) is not False:
        raise BoundaryError('原资产SDK环境不是已审核的prod，未改环境/渠道')
    official_token=conf._get_token
    def nonrefreshing_token():
        if getattr(conf,'_jwt_token',None):return official_token()
        if getattr(conf,'_bearer_file',None):
            cached=conf._load_cached_bearer_token()
            if cached is not None:return cached
        raise BoundaryError('现有官方认证不可用或需刷新；探针不登录、不刷新、不换账号')
    conf._get_token=nonrefreshing_token
    original=conf.session.request
    expected=conf._lpai_endpoint+'/lpai/api/assets/'+path
    def bounded_request(*args,**kwargs):
        url=kwargs.get('url');params=kwargs.get('params') or {}
        sts=download_sts and url==expected+'/sts' and params=={'use':'download','storage':'bos'}
        if args or kwargs.get('method')!='GET' or not (url==expected or sts) or kwargs.get('json') is not None:
            raise BoundaryError('SDK请求超出明确元数据GET范围')
        if any(str(key).lower()=='x-lpai-user' for key in (kwargs.get('headers') or {})) or params.get('user') or params.get('targetId'):
            raise BoundaryError('探针不冒充其他平台用户')
        budget.call();remaining=budget.deadline-budget.clock()
        kwargs.update(stream=True,allow_redirects=False,timeout=(min(5,remaining),min(10,remaining)))
        response=original(**kwargs)
        try:
            chunks=[];size=0
            for block in response.iter_content(chunk_size=64*1024):
                if not isinstance(block,bytes):raise BoundaryError('官方元数据流未返回字节')
                budget.account(len(block));size+=len(block)
                if size>max_response:raise BoundaryError('官方元数据实际响应超过2MiB边界')
                chunks.append(block)
            budget.check()
            response._content=b''.join(chunks);response._content_consumed=True
            return response
        finally:response.close()
    conf.session.request=bounded_request


def isolate_sdk_import_path(base):
    # Keep SDK/lazy imports out of the caller's project. lab is already loaded.
    canonical_base=Path(base).resolve();canonical_root=ROOT.resolve()
    sys.path[:]=[str(base)]+[entry for entry in sys.path if Path(entry or '.').resolve() not in {canonical_root,canonical_base}]


def load_official():
    bases={Path(sysconfig.get_path(key)).resolve() for key in ('purelib','platlib')}
    checked=[(base,source_check(base)) for base in bases]
    matches=[(base,report) for base,report in checked if all(v['matches_reviewed_source'] for v in report.values())]
    if len(matches)!=1:raise BoundaryError('官方资产SDK源码缺失或与审核版本不同；未导入/登录/联网')
    base,report=matches[0]
    from importlib.metadata import version
    try:package_version=version('lpai-asset')
    except Exception:raise BoundaryError('官方资产SDK安装版本无法核对；未导入/登录/联网') from None
    if package_version!='3.0.23':raise BoundaryError('官方资产SDK不是已核对的3.0.23；未自动升级/导入')
    # -I excludes the current directory, but this script added the project root
    # only to import lab. Do not let a project-level asset/requests shim run when
    # importing the SDK or its lazy dependencies after verifying site-packages.
    isolate_sdk_import_path(base)
    import asset
    from asset.cli import cmd
    for module,name in [(asset,'asset/__init__.py'),(cmd,'asset/cli/cmd.py')]:
        if Path(module.__file__).resolve()!=base/name:raise BoundaryError('导入的SDK不是已审核的安装位置')
    conf=cmd._init_config()  # Official non-auth resolution; never resource callback/login.
    return asset,conf,report


def execute(request,*,loader=load_official):
    request=validate_request(request)
    budget=TransferBudget(max_total=request['max_response_bytes'],max_calls=1,timeout=request['timeout'])
    conf=None;stage='sdk_load'
    try:
        asset,conf,source_report=loader()
        stage='read_boundary'
        path=validate_candidate(request['candidate'])
        install_read_only_boundary(conf,path,budget,request['max_response_bytes'])
        stage='resource_get'
        raw=asset.resource(path,config=conf).get(force_refresh=True,namespace=request['namespace'])
        stage='response_projection'
        budget.check()
        try:result=resource_observation(raw,request['candidate'],request['namespace'])
        except ValueError as exc:
            # Public identity fields only; no body, paths, annotation or auth.
            identity={}
            for label,source in [('root',raw),('metadata',raw.get('metadata') if isinstance(raw,dict) else None)]:
                if not isinstance(source,dict):continue
                identity[label]={}
                for key in ('uid','name','namespace'):
                    value=source.get(key)
                    try:identifier(value)
                    except ValueError:identity[label][key]={'type':type(value).__name__ if type(value) in (str,int,dict,list,type(None),bool) else 'other'}
                    else:identity[label][key]={'value':value}
            exc.asset_projection={'identity':identity,'response_shape':response_shape(raw)}
            raise
        return {**result,'sdk':'official_asset','sdk_package':'lpai-asset','sdk_version':'3.0.23','sdk_sources':source_report,
                'api_calls':budget.calls,'metadata_bytes_read':budget.bytes_read,
                'credentials_exported':False,'login_or_refresh_requested':False,
                'platform_writes':0,'files_downloaded':0,
                'account_scope_verified':False,'mapping_state':'mapping_unconfirmed'}
    except Exception as exc:
        exc.asset_read_stage=stage
        raise
    finally:
        if conf is not None:conf.session.close()


def public_error(exc):
    # The official Config wraps requests errors in RuntimeError. Preserve only
    # numeric HTTP status and known exception classes, never provider text.
    chain=[];current=exc
    while current is not None and len(chain)<6 and all(current is not item for item in chain):
        chain.append(current);current=current.__cause__ or current.__context__
    status=next((value for item in chain for value in
        [getattr(item,'status',None),getattr(getattr(item,'response',None),'status_code',None),
         (getattr(item,'response',{}).get('ResponseMetadata',{}).get('HTTPStatusCode') if isinstance(getattr(item,'response',None),dict) else None)]
        if type(value) is int and 100<=value<=599),None)
    category={401:'auth_unavailable',403:'access_denied',404:'resource_unavailable'}.get(status,'read_unconfirmed') if type(status) is int else 'read_unconfirmed'
    if type(status) is not int:
        if any(type(item).__name__ in {'ReadTimeout','ConnectTimeout','Timeout','TimeoutError'} for item in chain):category='network_timeout'
        elif any(type(item).__name__ in {'ConnectionError','ProxyError','SSLError'} for item in chain):category='network_unavailable'
    if any(isinstance(item,BoundaryError) for item in chain):category='boundary_rejected'
    result={'ok':False,'error':category,
        'message':str(exc) if type(exc) is BoundaryError else '官方只读请求未核实；未回显SDK诊断、登录或切换通道'}
    if status is not None:result['http_status']=status
    stage=getattr(exc,'asset_read_stage',None)
    if stage in {'sdk_load','read_boundary','resource_get','response_projection'}:result['stage']=stage
    known={'AttributeError','ModuleNotFoundError','ImportError','ValueError','KeyError','TypeError',
           'RuntimeError','RequestError','S3RequestError','ClientError','BoundaryError','ReadTimeout','ConnectTimeout','Timeout','ConnectionError','ProxyError','SSLError'}
    if type(exc).__name__ in known:result['exception_type']=type(exc).__name__
    if getattr(exc,'asset_projection',None) is not None:result['projection']=exc.asset_projection
    return result


def protocol_main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='strict')
    try:
        text=sys.stdin.read(MAX_REQUEST+1)
        if len(text.encode('utf-8'))>MAX_REQUEST:raise BoundaryError('只读请求超过16KiB')
        request=json.loads(text)
        with redirect_stdout(Discard()),redirect_stderr(Discard()):result=execute(request)
        response={'ok':True,'result':result}
    except Exception as exc:
        # SDK exceptions often contain URLs, auth headers or response bodies.
        # Only our authored boundary errors may include a message.
        response=public_error(exc)
    print(json.dumps(response,ensure_ascii=False))
    return 0 if response['ok'] else 1


def main():
    parser=argparse.ArgumentParser(description='默认无平台操作；仅由明确inspect探针启动stdio只读协议。')
    parser.add_argument('--stdio-inspect',action='store_true')
    args=parser.parse_args()
    if not args.stdio_inspect:
        print(json.dumps({'preflight_only':True,'sdk_imported':False,'platform_reads':0,'platform_writes':0,'mapping_state':'mapping_unconfirmed'},ensure_ascii=False))
        return 0
    return protocol_main()


if __name__=='__main__':sys.exit(main())
