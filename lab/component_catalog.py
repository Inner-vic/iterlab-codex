"""Account-scoped, read-only projections of the official training UI APIs.

Endpoint provenance: official subapp main.06717f3b.js fetchQueueList/getNamespacesList.
Global compute packages only describe specs; queue membership supplies eligibility.
"""
import hashlib
import json
from urllib.parse import quote


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def subject(api,namespace):
    raw=api._send_request('GET','/lpai/api/currentuser',params={'namespace':namespace})
    value=raw.get('user') if isinstance(raw,dict) else None
    if not isinstance(value,str) or not value.strip():raise ValueError('官方当前账号身份未核实')
    return digest(value.strip())


def quantity(value):
    return value if type(value) in (int,float) and value>=0 else None


def queues(raw,specs,namespace):
    if not isinstance(raw,list) or not isinstance(specs,list):raise ValueError('官方账号资源响应格式未确认')
    result=[]
    for row in raw:
        if row.get('status')!='enable':continue
        business=row.get('business') or []
        # Match training/devspace selector, excluding image/asset-robot-only pools.
        if not set(business)&{'train','jupyter','llm'}:continue
        code=row.get('code');cluster=row.get('cluster')
        if not isinstance(code,str) or not isinstance(cluster,str):raise ValueError('队列实际身份缺失')
        gpu=(row.get('resource') or {}).get('GPU') or {};resources=[]
        for kind,info in gpu.items():
            total=info.get('resource') or {};own=(info.get('namespace') or {}).get(namespace) or {}
            shares=bool(info.get('notebook_share'))
            queue_free=quantity(total.get('guarantee_remain'));scope_free=quantity(own.get('guarantee_remain'))
            available=min(queue_free,scope_free) if queue_free is not None and scope_free is not None else None
            resources.append({'label':kind.split('/',1)[-1],'gpu_type':kind,'available':available,
                'queue_remaining':queue_free,'namespace_remaining':scope_free,'unit':'machine' if shares else 'card',
                'quota':quantity(own.get('guarantee')),'capacity':quantity(own.get('capability'))})
        eligible={item.get('package_code') for item in row.get('package_info',[])}
        packages=[]
        for spec in specs:
            if spec.get('Id') not in eligible:continue
            if any(type(spec.get(k)) not in (int,float) for k in ('Cpu','Mem','Gpu')):continue
            gpu_type=spec.get('GpuType') or ''
            if spec['Gpu']>0 and gpu_type not in gpu:continue
            packages.append({'id':spec['Id'],'name':spec['Name'],'cpu':spec['Cpu'],'memory_gib':spec['Mem'],
                'gpu_count':spec['Gpu'] if spec['Gpu']>=0 else None,'gpu_type':gpu_type,
                'gpu_memory':quantity(spec.get('GpuMem')),'selectable':spec['Gpu']>=0,
                'projects':[{'code':p.get('project_code'),'name':p.get('project_name')} for pack in row.get('package_info',[])
                    if pack.get('package_code')==spec['Id'] for p in pack.get('project_info_list',[]) if p.get('project_code')]})
        result.append({'key':digest([cluster,code]),'name':row.get('nickname') or code,'cluster':cluster,
            'queue_code':code,'business':business,'resources':resources,'packages':packages})
    if len({q['key'] for q in result})!=len(result):raise ValueError('官方队列身份重复')
    return result


def read_resources(api,namespace):
    account=subject(api,namespace)
    raw=api._send_request('GET','/lpai/api/resource/v1/queues/namespaces/'+quote(namespace,safe=''))
    specs=api._get_available_compute_packages('all')
    result=queues(raw,specs,namespace)
    if subject(api,namespace)!=account:raise ValueError('读取期间账号变化，未采用资源目录')
    return {'subject':account,'namespace':namespace,'queues':result}


def asset_path(kind,uid=None,version=None):
    if kind not in {'images','datasets','volumes'}:raise ValueError('未注册的资产类型')
    from .company import checked_id
    path='/lpai/api/assets/'+kind
    if uid:path+='/'+quote(checked_id(uid),safe='')
    if version:path+='/versions/'+quote(checked_id(version),safe='')
    return path


def project_asset(raw,kind):
    if not isinstance(raw,dict):raise ValueError('官方资产响应格式未确认')
    meta=raw.get('metadata') or {};spec=raw.get('spec') or {}
    # Names/references and compatibility metadata only, no annotations, members or STS.
    allowed={'image','url','uri','digest','version','cluster','clusterName','path','format','storage','framework','cuda','python'}
    return {'kind':kind,'uid':meta.get('uid'),'name':meta.get('name'),'namespace':meta.get('namespace'),
            'spec':{k:v for k,v in spec.items() if k in allowed and isinstance(v,(str,int,float,bool))},
            'labels':{k:v for k,v in (meta.get('labels') or {}).items() if k in {'cluster','clusterName','cuda','framework','python'} and isinstance(v,str)}}


def read_assets(api,namespace,kind,uid=None,version=None,page=1):
    if type(page) is not int or not 1<=page<=1000:raise ValueError('资产页码无效')
    account=subject(api,namespace)
    raw=api._send_request('GET',asset_path(kind,uid,version),params={'namespace':namespace,'includeShare':'true',
        **({'pageNum':page,'pageSize':50} if not uid else {})})
    if uid:
        result={'item':project_asset(raw,kind)}
        if not version and kind in {'images','datasets'}:
            versions=api._send_request('GET',asset_path(kind,uid)+'/versions',params={'namespace':namespace,'pageNum':page,'pageSize':50,'includeShare':'true'})
            if not isinstance(versions,dict) or not isinstance(versions.get('items'),list):raise ValueError('官方资产版本列表未确认')
            result['versions']=[project_asset(item,kind) for item in versions['items']]
            result['next_page']=page+1 if len(versions['items'])==50 else None
    else:
        if not isinstance(raw,dict) or not isinstance(raw.get('items'),list):raise ValueError('资产列表未确认')
        result={'items':[project_asset(item,kind) for item in raw['items']],
                'next_page':page+1 if len(raw['items'])==50 else None}
    if subject(api,namespace)!=account:raise ValueError('读取期间账号变化，未采用资产')
    return {**result,'subject':account,'namespace':namespace}
