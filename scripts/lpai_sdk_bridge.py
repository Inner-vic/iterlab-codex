"""Narrow official SDK subprocess. JSON over stdin; never credentials in argv/output."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import re
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab.lpai_evidence import scrub, task_summary, gpu_count, apply_code_patches
from lab.task_policy import supported_paths


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def project_items(value):
    if isinstance(value,list):return value
    if isinstance(value,dict):
        if isinstance(value.get('items'),list):return value['items']
        # Observed official SDK empty-list envelope. A null with nonzero or
        # missing total remains unknown, never interpreted as safe to create.
        if value.get('items') is None and type(value.get('total')) is int and value['total']==0:return []
    raise ValueError('官方组件列表响应未确认，未发出创建请求')


def uncertain_creation(error=None,response=None):
    """Keep safe boundary facts, never credentials or an SDK response body."""
    result={'state':'unknown','write_attempted':True}
    if error is not None:
        from lab.platform_health import error_category
        result.update(error_type=type(error).__name__,error_category=error_category(error))
        status=re.search(r"Invalid status code ['\"]?(\d{3})",str(error))
        if status:result['http_status']=int(status[1])
    else:
        result['response_type']=type(response).__name__
        if isinstance(response,dict):result['response_keys']=sorted(set(response)&{'uid','result','data','code','message','project_uid'})
    return result


def creation_receipt(response):
    """Normalize SDK acknowledgement only; exact object/config readback still follows.

    The official service may return the project UID directly rather than a
    dictionary. Arbitrary strings/envelopes remain unknown and are never logged.
    """
    if isinstance(response,dict) and isinstance(response.get('uid'),str) and response['uid']:
        from lab.company import checked_id
        try:return {'state':'created','uid':checked_id(response['uid'])}
        except ValueError:return uncertain_creation(response=response)
    if isinstance(response,str) and re.fullmatch(r'project-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',response):
        return {'state':'created','uid':response}
    return uncertain_creation(response=response)


def execute(request):
    # Capture SDK diagnostics too: some dependencies print configuration on import.
    from lpai.conf.lipai_conf import LpaiConf
    from lpai.pipelinev2.project import Project
    from lpai.pipelinev2.task import Task
    conf=LpaiConf(env='prod')
    configured=bool(conf.get('lpai.token'))
    scope=conf.get_namespace()
    if request['operation']=='auth_status':
        return {'sdk':'lpai','token_present':configured,'namespace_matches':scope==request['namespace'],
                'verification':'local_configuration_only','connection_checked':False}
    if not configured:
        raise ValueError('官方SDK本机认证缺失；请完成训练SDK登录')
    if scope!=request['namespace']:
        raise ValueError('官方SDK namespace不符；请核对所选工作空间')
    if request['operation']=='account_resources':
        from lab.component_catalog import read_resources
        return read_resources(Project(conf),scope)
    if request['operation']=='execution_assets':
        from lab.component_catalog import read_assets
        return read_assets(Project(conf),scope,request['kind'],request.get('uid'),request.get('version'),request.get('page',1))
    if request['operation'] in {'configured_template','provision_configured','verify_configured'}:
        from lab.component_catalog import read_resources,read_assets
        from lab.component_configuration import build
        project=Project(conf);directory=read_resources(project,scope)
        directory.update(live=True,account_scope={'subject':directory['subject'],'namespace':scope,'connection_revision':request['connection_revision']})
        template=build(request['recipe'],directory,lambda kind,uid,version:read_assets(project,scope,kind,uid,version),request['component_name'],scope)
        if request['operation']=='configured_template':return template
        if template['config_sha256']!=request['expected_config_sha256']:raise ValueError('官方规格或镜像/数据配置变化，未创建或提交')
        if request['operation']=='provision_configured':
            if any(p.get('name')==request['component_name'] for p in project_items(project.list(name=request['component_name'],page_size=2,page_num=1))):
                return {'state':'name_conflict'}
            with TemporaryDirectory() as temp:
                path=Path(temp)/'component.json';path.write_text(json.dumps(template['config'],ensure_ascii=False),encoding='utf-8')
                try:created=project.create(template['cluster'],str(path))
                except Exception as exc:return uncertain_creation(error=exc)
            return creation_receipt(created)
        raw=project.get(request['component_uid'])
        from lab.component_configuration import verify_component
        return verify_component(raw,template)
    if request['operation']=='resource_packages':
        return Project(conf)._get_available_compute_packages('all')
    if request['operation']=='workspace_resources':
        from lab.company import checked_id
        try:result=Project(conf)._get_available_gpu_type(scope,checked_id(request['cluster']))
        except Exception as exc:
            if 'queue.scheduling.volcano.sh' in str(exc) and 'not found' in str(exc):
                return {'queue_verified':False,'allocation_verified':False,'resource_types':[],
                        'message':'官方资源接口返回当前工作空间的队列不存在；尚不能确认可申请配额，不能据此自动选择或跨集群提交。'}
            raise
        if not isinstance(result,dict):raise ValueError('官方工作空间资源格式未确认')
        resource=result.get('resource') or {}
        # SDK's legacy workspace endpoint does not define free-card telemetry.
        return {'resource_types':list((resource.get('gputype') or {}).keys()),
                'queue_verified':False,'allocation_verified':False,
                'message':'仅核对资源类型；未取得可验证的队列授权和实时空闲量。'}
    if request['operation']=='connection_check':
        if request.get('component_uid'):
            raw=Project(conf).get(request['component_uid'])
            if not isinstance(raw,dict) or raw.get('uid')!=request['component_uid'] or raw.get('namespace')!=scope:
                raise ValueError('SDK组件身份未核实')
        else:
            raw=Project(conf).list(page_size=1,page_num=1)
            project_items(raw)
        return {'sdk':'lpai','namespace':scope,'connection_checked':True,'read_verified':True,'write_verified':False}
    if request['operation']=='task':
        return {'item':task_summary(Task(conf).get(request['uid']))}
    if request['operation']=='lookup_component':
        from lab.company import checked_id
        name=checked_id(request['component_name'])
        matches=[item for item in project_items(Project(conf).list(name=name,page_size=2,page_num=1))
                 if item.get('name')==name and item.get('namespace')==scope]
        if not matches:
            return {'state':'not_observed','name':name,'namespace':scope}
        if len(matches)!=1 or not matches[0].get('uid'):
            raise ValueError('组件身份不唯一或缺失，不能继续提交')
        return {'state':'observed','name':name,'namespace':scope,'uid':checked_id(matches[0]['uid'])}
    if request['operation']=='cpu_lookup':
        from lab.company import checked_id
        name=checked_id(request['component_name']);value=Project(conf).list(name=name,page_size=2,page_num=1)
        def shape(item,depth=0):
            if depth>3:return type(item).__name__
            if isinstance(item,dict):return {k:shape(v,depth+1) for k,v in list(item.items())[:20]}
            if isinstance(item,list):return {'type':'list','length':len(item),'item_shapes':[shape(v,depth+1) for v in item[:2]]}
            return type(item).__name__
        return {'state':'read_only_lookup','name':name,'shape':shape(value),'total':value.get('total') if isinstance(value,dict) and type(value.get('total')) is int else None,'platform_writes':0}
    operation=request['operation']
    if operation not in {'component','submit','cpu_template','create_cpu_component','provision_component','verify_provisioned_component'}:
        raise ValueError('未注册的SDK操作')
    raw=Project(conf).get(request['component_uid'])
    if not isinstance(raw,dict) or raw.get('uid')!=request['component_uid'] or raw.get('namespace')!=scope or not isinstance(raw.get('data'),dict):
        raise ValueError('SDK组件身份/配置未核实')
    frozen={key:raw.get(key) for key in ['uid','namespace','cluster','name','queue_code','framework_type','data']}
    digest=fingerprint(frozen)
    task=raw['data'].get('lpai_task') or {}
    roles=task.get('roles') or raw.get('role') or []
    count=gpu_count(roles,raw.get('resource_info'))
    if operation=='verify_provisioned_component':
        config=request['config'];spec=config['task_role_specs'][0]
        role=roles[0] if len(roles)==1 else {};resource=role.get('resource') or {}
        if (raw.get('name')!=config['name'] or raw.get('cluster')!=request['cluster']
                or raw.get('framework_type')!=config['framework'] or raw.get('queue_code')!=config['queue_code']
                or raw.get('gems_project')!=config.get('gems_project') or count!=0
                or role.get('replica')!=1 or role.get('role')!=spec['rolename']
                or resource.get('Id')!=spec['resource']['id'] or resource.get('Cpu')!=1 or resource.get('Mem')!=4
                or (role.get('image_info') or {}).get('image')!=config['image']
                or role.get('cmd')!=config['cmd'] or task.get('cmd') not in {None,'',config['cmd']}
                or any(raw.get(key) not in (None,False,0) for key in ('retry','max_retry','elastic_train','wrap_train'))
                or raw['data'].get('lpai_input_pvc')!=config['input_data_volume']
                or any(raw['data'].get(key) for key in ('lpai_git','lpai_input_dataset','lpai_model_download'))):
            raise ValueError('读回组件配置与已审核预览不符')
        return {'uid':raw['uid'],'name':raw['name'],'namespace':scope,'digest':digest,'gpu_count':count}
    if operation in {'cpu_template','create_cpu_component','provision_component'}:
        from lab.company import checked_id
        checked_id(request['component_name'])
        if count!=0 or len(roles)!=1 or roles[0].get('replica')!=1:raise ValueError('验收模板只能复用单副本零GPU配置')
        role=roles[0];resource=role['resource']
        if resource.get('Cpu')!=1 or resource.get('Mem')!=4 or type(resource.get('Id')) is not int:raise ValueError('验收模板需已确认1CPU/4GiB的资源包')
        image=(role.get('image_info') or {}).get('image')
        if not isinstance(image,str) or not image or '@' in image or '://' in image:raise ValueError('原镜像引用未确认')
        volume=checked_id(request['volume_name']);mount='/mnt/volumes/'+volume
        config={'name':request['component_name'],'describe':'IterLab component-only core acceptance; independent CPU template; no automatic retries.',
            'framework':raw['framework_type'],'queue_code':raw['queue_code'],'cmd':'python3 -c "print(\'IterLab CPU component ready\')"',
            'image':image,'retry':0,'wait':False,
            'task_role_specs':[{'rolename':role.get('role') or 'worker','replica':'1','resource':{'id':resource['Id'],'replica':1}}],
            'input_data_volume':[{'name':volume,'env_key':'LPAI_INPUT_DATA_0','env_value':mount}],
            'code':[],'input_dataset':[],'input_model':[]}
        if raw.get('gems_project'):config['gems_project']=raw['gems_project']
        if operation=='provision_component':
            if (config!=request.get('config') or fingerprint(config)!=request.get('expected_config_sha256')
                    or digest!=request.get('source_digest') or raw['cluster']!=request.get('cluster')):
                return {'state':'preflight_failed'}
            existing=project_items(Project(conf).list(name=config['name'],page_size=2,page_num=1))
            if any(item.get('name')==config['name'] for item in existing):
                return {'state':'name_conflict'}
            with TemporaryDirectory() as temp:
                path=Path(temp)/'component.json'
                path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
                try:created=Project(conf).create(raw['cluster'],str(path))
                except Exception:return {'state':'unknown'}
            if not isinstance(created,dict) or not created.get('uid'):return {'state':'unknown'}
            return {'state':'created','uid':checked_id(created['uid'])}
        if request['operation']=='create_cpu_component':
            path=Path(request['config_path']).resolve()
            if path!=ROOT/'.evidence/core-lpai-20261103/component-preview/component.json':raise ValueError('只能创建已审核的确切CPU验收配置')
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved!=config or fingerprint(config)!=request.get('expected_config_sha256') or digest!=request.get('source_digest'):raise ValueError('CPU预览或源组件变化，未创建')
            existing=Project(conf).list(name=config['name'],page_size=2,page_num=1)
            items=project_items(existing)
            matched=[item for item in items if item.get('name')==config['name'] and item.get('namespace')==scope]
            if matched:
                if len(matched)!=1 or not matched[0].get('uid'):raise ValueError('同名组件不唯一，不创建或猜测')
                return {'state':'existing_requires_verification','uid':matched[0]['uid'],'name':config['name'],'platform_writes':0}
            try:created=Project(conf).create(raw['cluster'],str(path))
            except Exception:return {'state':'unknown','write_attempted':True,'message':'官方组件创建未返回UID，须只读查重，禁止重建'}
            if not isinstance(created,dict) or not created.get('uid'):return {'state':'unknown','write_attempted':True,'message':'组件创建收据不完整，不能重复调用'}
            return {'state':'created','uid':created['uid'],'name':config['name'],'namespace':scope,'platform_writes':1}
        return {'config':config,'cluster':raw['cluster'],'namespace':scope,'source_uid':raw['uid'],
                'source_digest':digest,'config_sha256':fingerprint(config),'gpu_count':0,'cpu':1,'memory_gib':4,'platform_writes':0}
    projection={'uid':raw['uid'],'name':raw.get('name'),'namespace':scope,'cluster':raw.get('cluster'),'gpu_count':count,
        'resource_specs':[{'package_id':r.get('resource',{}).get('Id'),'replicas':r.get('replica'),
                           'resource_replicas':r.get('resource',{}).get('replica',1),
                           'gpu_type':r.get('resource',{}).get('GpuType')} for r in roles],
        'digest':digest,'queue_code':raw.get('queue_code'),'framework_type':raw.get('framework_type'),
        'resources':scrub(raw.get('resource_info')),'roles':scrub(roles),
        'configuration_shape':{'top_keys':list(raw),'data_keys':list(raw['data']),'task_keys':list(task),'role_count':len(roles)},
        'code':scrub(raw['data'].get('lpai_git',[])),
        'datasets':scrub(raw['data'].get('lpai_input_dataset',[])),
        'volumes':scrub(raw['data'].get('lpai_input_pvc',[]))}
    data=apply_code_patches(raw['data'],request.get('patches',[]))
    if operation=='submit' or request.get('result_runtime'):
        from lab.training_entry import require_actual
        actual=data.get('lpai_task') or {}
        for command in [actual.get('cmd'),*[(role or {}).get('cmd') for role in actual.get('roles') or []]]:
            if command:require_actual(command)
    from lab import cloud_runtime
    data=cloud_runtime.apply(data,request.get('result_runtime'))
    task_api=Task(conf)
    import copy
    validation_data=copy.deepcopy(data)
    for key in ('lpai_input_dataset','lpai_model_download'):
        if validation_data.get(key) is None:validation_data[key]=[]
    try:task_api.validate_data(validation_data)
    except (ValueError,AssertionError,TypeError,KeyError) as exc:
        from lab.component_configuration import ConfigurationError
        raise ConfigurationError('官方SDK本地输入配置校验失败，未调用Task.submit；请核对数据集及模型版本字段') from exc
    from lab import submission_policy
    storage=submission_policy.read(conf)
    execution_digest=submission_policy.execution_digest(digest,data,storage)
    if request['operation']=='component':
        projection.update(execution_digest=execution_digest,submission_policy=storage,
            preflight={'official_sdk_input_validation':True,'result_storage_selection':'platform_default',
                'result_storage_override_sent':False,'effective_storage_backend_verified':False,
                'platform_initialization_verified':False},
            supported_patch_paths=supported_paths(raw['data']))
        if request.get('patches'):projection['patches_validated']=True
        return projection
    if request.get('expected_digest')!=digest: raise ValueError('组件已改变，未沿用旧审批提交')
    if request.get('expected_execution_digest') is not None and request['expected_execution_digest']!=execution_digest:
        raise ValueError('任务实际代码/入口与冻结工作包不符，未调用SDK提交')
    submission_policy.require(request.get('expected_submission_policy'),storage)
    fields=request['task_information']
    if request.get('execution_configuration'):
        from lab.component_catalog import read_resources,read_assets
        from lab.component_configuration import build,verify_component
        saved=request['execution_configuration'];approved=saved['template'];api=Project(conf)
        directory=read_resources(api,scope)
        directory.update(live=True,account_scope={'subject':directory['subject'],'namespace':scope,'connection_revision':saved['identity']['revision']})
        current=build(approved['recipe'],directory,lambda kind,uid,version:read_assets(api,scope,kind,uid,version),approved['config']['name'],scope)
        if current['config_sha256']!=approved['config_sha256'] or current['assets']!=approved['assets']:
            raise ValueError('提交前资源或资产变化，未调用Task.submit')
        verify_component(raw,current)
    try:
        result=task_api.submit(cluster=raw['cluster'],project_uid=raw['uid'],name=request['task_name'],
            data=data,**fields)
    except Exception:
        return {'state':'unknown','message':'官方SDK已进入提交边界但未确认UID；必须原身份核对，禁止重复提交'}
    # A returned envelope/error is not permission to retry; host persists unknown.
    if not isinstance(result,dict) or not result.get('uid'):
        return {'state':'unknown','message':'SDK未返回确切任务UID；保留原操作，先对账','response_shape':list(result) if isinstance(result,dict) else type(result).__name__}
    return {'state':'submitted','item':task_summary(result),'component_digest':digest,'execution_digest':execution_digest}


def main():
    # -I ignores PYTHONIOENCODING. Pin the actual protocol streams explicitly;
    # a GBK Windows locale can otherwise silently corrupt valid Chinese JSON.
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='strict')
    try:
        request=json.load(sys.stdin)
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            result=execute(request)
        response={'ok':True,'result':result}
    except Exception as exc:
        from lab.platform_health import error_category
        response={'ok':False,'error_category':error_category(exc),'error_type':type(exc).__name__}
        from lab.component_configuration import ConfigurationError
        if isinstance(exc,ConfigurationError):response['validation']=str(exc)[:260]
    print(json.dumps(response,ensure_ascii=False))


if __name__=='__main__': main()
