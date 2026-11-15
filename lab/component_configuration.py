"""Build exact SDK configs from native Codex recommendations, not arbitrary patches."""
import json
import re
from pathlib import PurePosixPath
from functools import wraps

from .store import dumps
from .component_catalog import digest

RECIPE_FIELDS=frozenset({'schema','account_scope','queue_key','package_id','workers','packages_per_worker','image','volumes','datasets',
    'output_volume_uid','command','training_parameters','rationale','compatibility'})


def recipe_contract():
    """The actual compiler input, not an executable or account-specific default."""
    return {'schema':1,'required_fields':sorted(RECIPE_FIELDS),'optional_fields':['project_code'],
        'shapes':{'account_scope':['subject','namespace','connection_revision'],'image':['uid','version'],
                  'volumes[]':['uid','name','mount_path'],'datasets[]':['uid','name','version','mount_path']},
        'sources':{'account_scope':'lpai_read(resources).account_scope','queue_key':'queues[].key',
                   'package_id':'selected_queue.packages[].id','project_code':'selected_package.projects[].code',
                   'image':'lpai_read(assets, images): exact uid/version','volumes':'lpai_read(assets, volumes): exact uid/name'},
        'rules':'workers为正整数；packages_per_worker=1；training_parameters为对象。资源、镜像和挂载从当前账号读取，不猜默认值。',
        'compatibility':'非空字符串，或至多16项字符串值的说明对象；布尔标记转为说明文字，不加入执行参数。',
        'separate_contracts':'output_contract和runtime属于lpai_prepare，不放入组件recipe；package_spec/name等读回字段也不是recipe输入。',
        'boundary':'结构检查不证明权限、余量、软件兼容或训练通过；审核预览和正式提交仍实时验证。'}


def recipe_shape_errors(recipe):
    """Pure, bounded shape checks shared with preparation; never probes assets."""
    if not isinstance(recipe,dict):return [{'field':'$','issue':'expected_object'}]
    if len(recipe)>64:return [{'field':'$','issue':'too_many_fields_max_64'}]
    errors=[{'field':key,'issue':'missing'} for key in sorted(RECIPE_FIELDS-recipe.keys())]
    errors += [{'field':str(key)[:96],'issue':'unsupported'} for key in sorted(recipe.keys()-RECIPE_FIELDS-{'project_code'},key=str)]
    if type(recipe.get('schema')) is not int or recipe.get('schema')!=1:
        errors.append({'field':'schema','issue':'expected_integer_1'})
    shapes={'account_scope':{'subject','namespace','connection_revision'},'image':{'uid','version'}}
    for key,fields in shapes.items():
        if key in recipe and (not isinstance(recipe[key],dict) or set(recipe[key])!=fields):
            errors.append({'field':key,'issue':'expected_fields','fields':sorted(fields)})
    for key,fields in [('volumes',{'uid','name','mount_path'}),('datasets',{'uid','name','version','mount_path'})]:
        if key not in recipe:continue
        if not isinstance(recipe[key],list) or len(recipe[key])>16:
            errors.append({'field':key,'issue':'expected_list_max_16'});continue
        for index,item in enumerate(recipe[key]):
            if not isinstance(item,dict) or set(item)!=fields:
                errors.append({'field':f'{key}[{index}]','issue':'expected_fields','fields':sorted(fields)})
    for key in ('workers','packages_per_worker','package_id'):
        if key in recipe and (type(recipe[key]) is not int or recipe[key]<1):
            errors.append({'field':key,'issue':'expected_positive_integer'})
    if 'packages_per_worker' in recipe and recipe['packages_per_worker']!=1:
        errors.append({'field':'packages_per_worker','issue':'expected_1'})
    if 'training_parameters' in recipe and not isinstance(recipe['training_parameters'],dict):
        errors.append({'field':'training_parameters','issue':'expected_object'})
    if 'compatibility' in recipe:
        try:compatibility_text(recipe['compatibility'])
        except ValueError:errors.append({'field':'compatibility','issue':'expected_nonempty_text_or_string_values_max_16'})
    return errors


class ConfigurationError(ValueError):
    """Application-owned validation text, safe to return to the native model."""


def configuration_boundary(function):
    @wraps(function)
    def checked(*args,**kwargs):
        try:return function(*args,**kwargs)
        except ValueError as exc:
            trace=exc.__traceback__
            while trace.tb_next:trace=trace.tb_next
            # Never return an SDK exception body, asset payload or credential.
            if trace.tb_frame.f_code.co_filename==__file__:raise ConfigurationError(str(exc)) from exc
            raise
    return checked


def recommendation(store,plan):
    source=json.loads(plan['source']);path=(source.get('scope_proposal') or {}).get('component_config')
    if not path:return None
    if not isinstance(path,str) or not path.endswith('.json'):raise ValueError('运行配置须为方案证据中的JSON文件')
    ref=next((ref for ref in json.loads(plan['evidence']) if ref['path']==path),None)
    file=store.safe_path(plan['project_id'],path)
    if not ref or not file.is_file() or file.stat().st_size>65536:raise ValueError('运行配置未纳入确切方案证据或文件过大')
    import hashlib
    data=file.read_bytes()
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('方案运行配置已变化，不能沿用审核')
    recipe=json.loads(data)
    errors=recipe_shape_errors(recipe)
    if errors:
        # Reject local structural mistakes before starting the cold SDK child.
        # Keep the original bytes/evidence; the model must save its correction.
        fields='; '.join(error['field']+':'+error['issue'] for error in errors[:16])
        raise ValueError('运行配置结构未通过（未调用SDK或提交任务）：'+fields+'。按company_context.component_recipe_contract修复后重新引用确切文件。')
    return recipe


def positive(value,label,maximum=128):
    if type(value) is not int or not 1<=value<=maximum:raise ValueError(label+'须为明确的正整数')
    return value


def mount(path):
    if (not isinstance(path,str) or not path.startswith('/mnt/') or '..' in PurePosixPath(path).parts
            or '\\' in path or re.search(r'[\s\x00-\x1f]',path)):
        raise ValueError('挂载路径须为明确的/mnt/绝对路径')
    return path.rstrip('/')


def compatibility_text(value):
    """Bounded explanatory metadata only; never environment or SDK options."""
    if isinstance(value,str) and value.strip() and len(value)<=8000:return value
    if (isinstance(value,dict) and 1<=len(value)<=16
            and all(isinstance(k,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_ -]{0,63}',k)
                    and isinstance(v,str) and v.strip() for k,v in value.items())
            and len(dumps(value))<=8000):
        return '\n'.join(k+': '+v for k,v in value.items())
    raise ValueError('compatibility需为非空文字或至多16项字符串说明对象，总长度不超过8000；仅说明兼容依据，不作为环境参数')


@configuration_boundary
def build(recipe,directory,read_asset,name,namespace):
    required=RECIPE_FIELDS
    if not isinstance(recipe,dict) or set(recipe)-{'project_code'}!=required or type(recipe['schema']) is not int or recipe['schema']!=1:raise ValueError('推荐配置字段不完整或含未支持字段')
    if any(not isinstance(recipe[k],list) or len(recipe[k])>16 for k in ('volumes','datasets')):raise ValueError('挂载需为至多16项的明确列表')
    if directory.get('live') is not True or recipe['account_scope']!=directory.get('account_scope'):raise ValueError('推荐配置的账号已变化，请重新核对')
    queue=next((q for q in directory['queues'] if q['key']==recipe['queue_key']),None)
    if not queue or 'train' not in queue['business']:raise ValueError('所选队列不支持组件库训练')
    spec=next((s for s in queue['packages'] if s['id']==recipe['package_id']),None)
    if not spec or not spec['selectable']:raise ValueError('此队列没有该固定资源规格，共享动态规格不能猜卡数')
    projects={p['code']:p for p in spec.get('projects',[])}
    selected=recipe.get('project_code') or (next(iter(projects)) if len(projects)==1 else None)
    if not selected or selected not in projects:raise ValueError('需从当前规格实际关联项目中推荐计费归属，不能使用开发者个人默认')
    workers=positive(recipe['workers'],'worker数');copies=positive(recipe['packages_per_worker'],'每worker规格份数')
    if copies!=1:raise ValueError('首版每个worker使用一份官方规格，多卡应选择对应多卡规格，不能拼出超过单机容量的配置')
    count=spec['gpu_count']*workers
    if count>128:raise ValueError('总GPU数超出支持范围')
    image=recipe['image']
    if not isinstance(image,dict) or set(image)!={'uid','version'} or not image['version']:raise ValueError('镜像须选择确切资产及版本')
    observations=[]
    def asset(kind,uid,version=None):
        value=read_asset(kind,uid,version)
        if value.get('subject')!=directory['account_scope']['subject'] or value.get('namespace')!=namespace:raise ValueError('资产与资源账号不同')
        item=value['item']
        # Versions may have their own UID; parent identity is verified by the
        # exact official /{kind}/{uid}/versions/{version} request, not a name search.
        if not version and item.get('uid')!=uid:raise ValueError('资产UID读回不符')
        cluster=(item.get('spec',{}).get('cluster') or item.get('spec',{}).get('clusterName')
            or item.get('labels',{}).get('cluster') or item.get('labels',{}).get('clusterName'))
        if cluster and cluster!=queue['cluster']:raise ValueError('资产位于其他集群，不能沿用挂载')
        observations.append({'kind':kind,'uid':uid,'version':version,'observation':item})
        return item
    parent=asset('images',image['uid']);version=asset('images',image['uid'],image['version'])
    reference=version.get('spec',{}).get('image') or version.get('spec',{}).get('uri')
    if not reference:
        base=parent.get('name');tag=version.get('name')
        if tag!=image['version'] or not isinstance(base,str) or '/' not in base:raise ValueError('官方镜像版本引用未确认')
        reference=base+':'+tag
    image_digest=version.get('spec',{}).get('digest')
    if isinstance(image_digest,str) and re.fullmatch('sha256:[a-f0-9]{64}',image_digest):reference=parent['name']+'@'+image_digest
    if not isinstance(reference,str) or re.search(r'[\s\x00-\x1f]',reference) or '://' in reference:raise ValueError('官方镜像引用无效')
    volumes=[];datasets=[];output=None;paths=[];seen=set()
    for entry in recipe['volumes']:
        if not isinstance(entry,dict) or set(entry)!={'uid','name','mount_path'} or entry['uid'] in seen:raise ValueError('数据卷配置无效或重复')
        item=asset('volumes',entry['uid'])
        if item['name']!=entry['name']:raise ValueError('数据卷名称/UID不匹配')
        path=mount(entry['mount_path']);paths.append(path);seen.add(entry['uid'])
        volumes.append({'name':entry['name'],'env_key':'LPAI_INPUT_DATA_'+str(len(volumes)),'env_value':path})
        if entry['uid']==recipe['output_volume_uid']:output=entry
    for entry in recipe['datasets']:
        if not isinstance(entry,dict) or set(entry)!={'uid','name','version','mount_path'}:raise ValueError('数据集需固定UID、名称、版本和挂载')
        item=asset('datasets',entry['uid']);asset('datasets',entry['uid'],entry['version'])
        if item['name']!=entry['name']:raise ValueError('数据集名称/UID不匹配')
        path=mount(entry['mount_path']);paths.append(path)
        datasets.append({'name':entry['name'],'version':entry['version'],'env_key':'LPAI_INPUT_DATASET_'+str(len(datasets)),'env_value':path})
    if not output:raise ValueError('必须选择实际可访问的结果卷')
    for i,path in enumerate(paths):
        if any(path==other or path.startswith(other+'/') or other.startswith(path+'/') for other in paths[i+1:]):raise ValueError('挂载路径重叠')
    command=recipe['command']
    if not isinstance(command,str) or not command.strip() or len(command)>12000 or re.search(r'(?i)(password|token|secret)\s*[=:]',command):raise ValueError('训练入口无效或含凭据')
    if not isinstance(recipe['rationale'],str) or not recipe['rationale'].strip():raise ValueError('需说明资源选择依据')
    compatibility=compatibility_text(recipe['compatibility'])
    if not isinstance(recipe['training_parameters'],dict) or len(dumps(recipe['training_parameters']))>8000:raise ValueError('训练参数须为可读的有界对象')
    # PyTorchJob needs a Master even for one node running local torchrun ranks.
    # Node count does not decide whether this framework needs a master role.
    # Keep single-node custom/CPU templates compatible with their worker role.
    roles=[{'rolename':'master' if count or workers>1 else 'worker','replica':'1','resource':{'id':spec['id'],'replica':1}}]
    if workers>1:roles.append({'rolename':'worker','replica':str(workers-1),'resource':{'id':spec['id'],'replica':1}})
    config={'name':name,'describe':'IterLab approved execution '+digest(recipe)[:16],'framework':'pytorch' if count else 'custom',
        'queue_code':queue['queue_code'],'cmd':"python3 -c \"print('IterLab approved component ready')\"",'image':reference,'retry':0,'wait':False,
        'task_role_specs':roles,'input_data_volume':volumes,'input_dataset':datasets,'code':[],'input_model':[], 'gems_project':selected}
    return {'schema':1,'config':config,'cluster':queue['cluster'],'namespace':namespace,'config_sha256':digest(config),
        'gpu_count':count,'cpu':spec['cpu']*workers,'memory_gib':spec['memory_gib']*workers,
        'package':spec,'workers':workers,'queue_name':queue['name'],'account_scope':directory['account_scope'],
        'recipe':recipe,'assets':observations,'output_volume':output,'platform_writes':0,
        'compatibility_verified':'asset_identity_and_cluster_only','software_compatibility':compatibility}


@configuration_boundary
def verify_component(raw,template):
    from .lpai_evidence import gpu_count
    config=template['config'];data=raw.get('data') or {};task=data.get('lpai_task') or {};roles=task.get('roles') or []
    if (raw.get('namespace')!=template['namespace'] or raw.get('name')!=config['name'] or raw.get('cluster')!=template['cluster']
            or raw.get('queue_code')!=config['queue_code'] or raw.get('framework_type')!=config['framework'] or raw.get('gems_project')!=config['gems_project']
            or gpu_count(roles,raw.get('resource_info'))!=template['gpu_count'] or len(roles)!=len(config['task_role_specs'])
            or data.get('lpai_input_pvc')!=config['input_data_volume'] or data.get('lpai_input_dataset',[])!=config['input_dataset']
            or data.get('lpai_git') or data.get('lpai_model_download')
            or any(raw.get(k) not in (None,False,0) for k in ('retry','max_retry','elastic_train','wrap_train'))):
        raise ValueError('官方组件资源、镜像或挂载与已审核配置不符')
    for role,spec in zip(roles,config['task_role_specs']):
        resource=role.get('resource') or {}
        if (role.get('role')!=spec['rolename'] or role.get('replica')!=int(spec['replica'])
                or resource.get('Id')!=spec['resource']['id'] or resource.get('Gpu')!=template['package']['gpu_count']
                or resource.get('Cpu')!=template['package']['cpu'] or resource.get('Mem')!=template['package']['memory_gib']
                or resource.get('GpuType','')!=template['package']['gpu_type']
                or (role.get('image_info') or {}).get('image')!=config['image'] or role.get('cmd')!=config['cmd']):
            raise ValueError('官方组件单worker配置与审核不符')
    if task.get('cmd') not in (None,'',config['cmd']):raise ValueError('组件总入口与审核不符')
    frozen={key:raw.get(key) for key in ['uid','namespace','cluster','name','queue_code','framework_type','data']}
    return {'uid':raw['uid'],'name':raw['name'],'namespace':raw['namespace'],'digest':digest(frozen),'gpu_count':template['gpu_count']}
