"""Cloud output inspection primitives; no guessed provider mapping or model client.

This batch deliberately has no enabled task -> asset-version provenance parser.
An origin export/checksum, matching names, paths, or metadata are not such proof.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import time

from .lpai_identity import timestamp,platform_identity
from .lpai_evidence import SENSITIVE
from .run_evidence import MAX_FILE,MAX_TOTAL

# Reviewed code anchors, not an assertion that every transitive SDK dependency
# is verified. No package imports or configuration reads are performed here.
SDK_SOURCES={
    'asset/__init__.py':'bea48ddd16c32bb0fd4a6370eba37f9a017844613a43087ea96716a6576eb5ef',
    'asset/core/config.py':'29bc8786597f9b447f7c947511ddf17b766f348fb26080649e9536362ed53597',
    'asset/client/client.py':'4457c0e216a7e9e2c2b87b6ceaefc58f40d23b772cb691f825f5226ab65e6663',
    'asset/resource/resource.py':'760057ad61731c3c76cbace4fca7e9b7de4521bfab64471d86c1234329a779f1',
    'asset/cli/cmd.py':'7a01fc384cee6fd4294e0a1e21d55436a90cf2c7ee84283aaff50b40a93145c6',
    'asset/io/s3.py':'f8d0f3d292ae165d5bed05642f1d1f5f7de1dea8e0eb39cefa4191081ec202da',
    'asset/cli/__init__.py':'b673e2721031f13290558cd0bd4c071610da511e8a9a0e6c5fa8d7ec3546aa9c',
    'asset/core/__init__.py':'24e8a31211f8f4fd20e7b71edbd380cb12dddd19cdbe427d7ecdc0dc44d0858d',
    'asset/resource/__init__.py':'b626cbfce2e266da8cf27c5499611e720ebe8e38e03f0df9b373db94ec697ba1',
    'asset/io/__init__.py':'d8ebe93ffe9018c3ff967d0af6cee9580f9474d0cd986e7671589a6ed115f403',
    'asset/util/logger.py':'fe498a3c2d4fcc2c9ba7029ae987d65acf4e524064195c9726dca87384210cb2',
}
PROVENANCE_ADAPTER=None  # Enable only after actual official response fields are verified.
PUBLIC_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z')
SHA256=re.compile(r'[a-f0-9]{64}\Z')
SYMBOLIC={'latest','head','current','main','master','default'}
ORIGIN_IDS={'project_id','session_id','plan_id','trial_id','submission_id','task_uid','namespace','component_uid'}
ORIGIN_HASHES={'plan_snapshot_sha256','request_sha256','receipt_sha256','submission_authority_sha256','connection_authority_sha256'}
ORIGIN_FIELDS=ORIGIN_IDS|ORIGIN_HASHES|{'plan_version','task_name','profile','session_channel','read_channel','requested_at','platform_identity'}
# A shape is diagnostic, not a raw key dump. Unknown map keys can themselves
# contain secrets (e.g. an environment value used as a label/parameter name).
SHAPE_FIELDS=set('''uid name namespace kind apiVersion metadata spec status phase data item items
    result resourceVersion generation creationTimestamp updateTimestamp deletionTimestamp
    annotations labels tags ownerReferences shareReferences parent parent_name parentName
    version versions version_uid versionUid asset_uid assetUid resource_path resourcePath
    dir path format files outputs output model models dataset datasets source sources
    task task_uid taskUid task_id taskId task_name taskName source_task_uid sourceTaskUid
    sourceTask source_task origin provenance job jobs job_id jobId job_uid jobUid jobType
    sourceID targetID sourceId targetId platform_identity platform tenant_uid account_uid user_uid
    created_at updated_at requested_at create_timestamp start_timestamp update_timestamp
    project_uid project_name projectUid projectName cluster clusterName project task_information
    expected_duration expected_duration_desc expected_result role roles replica resource resource_info
    resources Gpu gpu cpu memory queue_code framework_type retry max_retry running_duration
    configuration environment env envs headers submit_info lpai_envs lpai_model_register
    lpai_task lpai_git lpai_input_dataset lpai_input_model model_set model_version dataset_name
    source_type source_path storage storage_name size number count revision frozen freeze
    checkpoint checkpoints code digest sha256 receipt register_model registered_model
    registration output_assets outputAssets asset_version assetVersion manifest metrics evaluation
    problem_statement solution_approach'''.split())


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def identifier(value):
    if not isinstance(value,str) or not PUBLIC_ID.fullmatch(value):raise ValueError('需要确切的公开资源标识，不接受路径、URL、凭据或命令')
    return value


def resource_path(kind,name,version=None):
    if not isinstance(kind,str) or kind not in {'tasks','models','datasets'}:raise ValueError('仅支持原任务或确切模型/数据集版本')
    identifier(name)
    if kind=='tasks':
        if version is not None:raise ValueError('tasks资源没有版本，不能伪造versions路径')
        return 'tasks/'+name
    identifier(version)
    if version.lower() in SYMBOLIC:raise ValueError('不接受latest/HEAD等可漂移版本指针')
    return kind+'/'+name+'/versions/'+version


def cloud_key(value):
    if not isinstance(value,str) or not value or len(value)>700 or any(c in value for c in '\\%:?*"<>|') or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('文件key必须是明确的原资源内POSIX相对路径')
    parts=value.split('/')
    if any(not part or part in {'.','..'} or part.endswith((' ','.')) for part in parts):raise ValueError('文件key不能归一化、越界或包含空目录段')
    for part in parts:
        if (part.startswith('.') or SENSITIVE.search(part) or SENSITIVE.search(PurePosixPath(part).stem)
                or part.lower() in {'id_rsa','id_ed25519'}):raise ValueError('回收范围不包含认证/隐藏配置文件')
        if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part):raise ValueError('文件名不能使用Windows保留设备名称')
    if PurePosixPath(value).is_absolute():raise ValueError('文件key不能是绝对路径')
    return value


ORIGIN_MEANING='仅原库公开身份的有界导出；内容校验不是来源签名，不授权下载/训练，不证明资产版本归属'


def validate_origin(export):
    """Validate a public identity envelope, NOT its authenticity or a download grant."""
    fields={'schema','origin','sha256','exported_at','platform_read_performed','meaning'}
    if not isinstance(export,dict) or set(export)!=fields or export['schema']!='iterlab-cloud-origin/v1':
        raise ValueError('原身份导出schema/字段未确认')
    origin=export['origin']
    if not isinstance(origin,dict) or set(origin)!=ORIGIN_FIELDS:raise ValueError('原身份字段不完整或包含额外内容')
    for key in ORIGIN_IDS|{'task_name'}:identifier(origin[key])
    if not isinstance(origin['plan_version'],str) or not re.fullmatch(r'[1-9][0-9]{0,8}\.[0-9]{1,9}',origin['plan_version']):
        raise ValueError('原方案的确切版本未确认')
    profile=origin['profile']
    if not isinstance(profile,str) or not profile.strip() or len(profile)>200 or any(ord(c)<32 for c in profile):
        raise ValueError('原模型profile未确认')
    if origin['session_channel']!='ept' or origin['read_channel']!='lpai-agent':raise ValueError('原企业/只读通道不受支持，不切换通道')
    for key in ORIGIN_HASHES:
        if not isinstance(origin[key],str) or not SHA256.fullmatch(origin[key]):raise ValueError('原身份摘要无效：'+key)
    public=platform_identity(origin['platform_identity'])
    if public:
        for value in public.values():identifier(value)
    requested,exported=timestamp(origin['requested_at']),timestamp(export['exported_at'])
    if requested is None or exported is None or exported<requested:raise ValueError('原提交/导出时间须为明确且有时区的时间')
    if export['platform_read_performed'] is not False or export['meaning']!=ORIGIN_MEANING:raise ValueError('身份导出不能伪装实网回执或新授权')
    if not isinstance(export['sha256'],str) or not SHA256.fullmatch(export['sha256']) or fingerprint(origin)!=export['sha256']:
        raise ValueError('原身份导出内容摘要不符；摘要不是平台签名')
    if len(json.dumps(export,ensure_ascii=False).encode('utf-8'))>16384:raise ValueError('原公开身份导出超过16KiB')
    return origin


def response_shape(raw):
    """Bounded field/type shape. No scalar values or user-controlled metadata maps."""
    remaining=[160];truncated=[False]
    dynamic={'annotations','labels','tags','headers','configuration','environment','env','envs'}
    def walk(value,depth=0):
        remaining[0]-=1
        if depth>=8 or remaining[0]<0:truncated[0]=True;return {'type':'truncated'}
        if isinstance(value,dict):
            fields={}
            for index,(key,item) in enumerate(value.items()):
                if index>=40 or remaining[0]<=0:truncated[0]=True;break
                if not isinstance(key,str) or key not in SHAPE_FIELDS:
                    truncated[0]=True;continue
                fields[key]={'type':'redacted'} if SENSITIVE.search(key) or key.lower() in dynamic else walk(item,depth+1)
            return {'type':'object','fields':fields}
        if isinstance(value,list):
            if len(value)>3:truncated[0]=True
            return {'type':'array','items':[walk(item,depth+1) for item in value[:3]]}
        kind='null' if value is None else 'boolean' if isinstance(value,bool) else 'integer' if isinstance(value,int) else 'number' if isinstance(value,float) else 'string' if isinstance(value,str) else 'unsupported'
        return {'type':kind}
    result={'shape':walk(raw),'values_omitted':True,'truncated':truncated[0]}
    if len(json.dumps(result).encode('utf-8'))>32768:raise ValueError('响应形状超过32KiB；未回显原正文')
    return result


def mapping_assessment(task,asset):
    """No known official output-provenance dialect is enabled in this release."""
    return {'state':'mapping_unconfirmed','provenance_adapter':None,
            'automatic_collection_enabled':False,'numeric_comparison_authorized':False,
            'reasons':['official_task_to_asset_version_provenance_not_verified'],
            'required_evidence':['original_task_uid_and_scope','exact_asset_and_version_uid',
                                 'official_source_or_registration_job_receipt','original_account_scope'],
            'meaning':'任务终态、名称/时间、共享目录、人工annotation和注册配置意图都不能单独证明版本归属'}


def require_confirmed_mapping(mapping,*,reader=None,output=None):
    # Do not trust a JSON state flag, checksum, annotation, or a user-supplied adapter.
    # A future verified provider dialect must change this gate with its own tests.
    raise ValueError('原任务→正式资产版本的来源映射尚未核实；未调用reader或创建回收目录（mapping_unconfirmed）')

def validate_candidate(candidate):
    if not isinstance(candidate,dict) or set(candidate)!={'kind','name','version','uid'}:
        raise ValueError('候选查询只接收资源类型、名称、确切版本及可选UID')
    path=resource_path(candidate['kind'],candidate['name'],candidate['version'])
    if candidate['uid'] is not None:identifier(candidate['uid'])
    return path


def resource_observation(raw,candidate,namespace):
    """Only query identity is checked here; source-task provenance is NOT inferred."""
    path=validate_candidate(candidate);identifier(namespace)
    if not isinstance(raw,dict) or not isinstance(raw.get('metadata',{}),dict):
        raise ValueError('资产元数据响应格式未确认')
    metadata=raw.get('metadata',{});observed={}
    for field in ('uid','name','namespace'):
        values=[source[field] for source in (raw,metadata) if source.get(field) is not None]
        # Task names are display labels in the asset API, not lookup IDs.
        # A localized/compound label must not invalidate a matching task UID,
        # nor be exported as an identifier or used to establish provenance.
        display_name=candidate['kind']=='tasks' and field=='name'
        if len(values)>1 and values[0]!=values[1]:
            if display_name:values=[]
            else:raise ValueError('平台返回冲突的资产身份：'+field)
        value=values[0] if values else None
        if value is not None:
            if display_name:
                if not isinstance(value,str):raise ValueError('任务展示名称类型不符')
                if not PUBLIC_ID.fullmatch(value):value=None
            else:identifier(value)
        observed[field]=value
    # The official docs allow a tasks lookup identifier to be a task UID or
    # task name. Without a live alias contract, neither is inferred from the
    # other. Preserve returned names as observations, never as version proof.
    expected_name=None if candidate['kind']=='tasks' else candidate['version']
    for field,expected in [('namespace',namespace),('name',expected_name),('uid',candidate['uid'])]:
        if observed[field] is not None and expected is not None and observed[field]!=expected:
            raise ValueError('候选资产UID/名称/namespace与明确查询不一致，不改用其他版本')
    observed['query_path']=path
    observed['identity_state']=('unversioned_resource_alias_unconfirmed' if candidate['kind']=='tasks' else
        'query_identity_checked' if all(observed[k] for k in ('uid','name','namespace')) else 'query_identity_unconfirmed')
    observed['version_identity_kind']='unversioned_task_output' if candidate['kind']=='tasks' else 'candidate_formal_version'
    observed['provenance_certified']=False
    observed['platform_immutable_content_verified']=False
    shape=response_shape(raw)
    return {'observation':observed,'response_shape':shape,
            'public_projection_sha256':fingerprint({'observation':observed,'shape':shape})}


def original_task_observation(raw,export):
    """Check the exact original UID/scope; do not fill absent server evidence."""
    from .lpai_identity import task_observation
    origin=validate_origin(export);item=task_observation(raw)
    safe={key:item.get(key) for key in ('uid','name','namespace','project_uid')}
    for key,value in safe.items():
        if value is not None:identifier(value)
    expected={'uid':origin['task_uid'],'namespace':origin['namespace'],'project_uid':origin['component_uid']}
    if any(safe.get(key) is not None and safe[key]!=value for key,value in expected.items()):
        raise ValueError('原任务UID/namespace/组件读回不一致；未查询替代任务')
    name=safe['name']
    if name is not None and name!=origin['task_name']:raise ValueError('原UID任务名称与已返回的原收据不一致')
    created=timestamp(item.get('created_at'))
    if created is not None and created<timestamp(origin['requested_at']):raise ValueError('任务创建时间早于原实际提交')
    actual_platform=platform_identity(item.get('platform_identity'))
    if actual_platform:
        try:
            for value in actual_platform.values():identifier(value)
        except ValueError:actual_platform=None  # Do not emit unsupported identity text.
    expected_platform=origin['platform_identity']
    if expected_platform and actual_platform and any(actual_platform.get(k)!=v for k,v in expected_platform.items()):
        raise ValueError('原任务平台公开账号身份不一致，不切换账号')
    phase=item.get('phase')
    safe.update(phase=phase if isinstance(phase,str) and re.fullmatch(r'[A-Za-z_]{1,40}',phase) else 'unknown',
                created_at=created.isoformat() if created else None,
                platform_identity=actual_platform,
                account_scope_state='checked_against_original' if expected_platform and actual_platform else 'unconfirmed',
                identity_state='original_uid_scope_checked' if all(safe[k] for k in expected) and name and created else 'unconfirmed')
    return {'observation':safe,'response_shape':response_shape(raw),
            'output_provenance_certified':False}


def validate_shape(shape):
    """Validate the child protocol too; a forged shape must not carry scalar values."""
    if not isinstance(shape,dict) or set(shape)!={'shape','values_omitted','truncated'} or shape['values_omitted'] is not True or type(shape['truncated']) is not bool:
        raise ValueError('资产桥响应形状schema不符')
    count=[0]
    def walk(node,depth):
        count[0]+=1
        if not isinstance(node,dict) or depth>8 or count[0]>256:raise ValueError('资产桥响应形状超过边界')
        kind=node.get('type')
        if kind=='object':
            if set(node)!={'type','fields'} or not isinstance(node['fields'],dict) or len(node['fields'])>40 or set(node['fields'])-SHAPE_FIELDS:
                raise ValueError('响应形状不能含未知字段名或正文值')
            for child in node['fields'].values():walk(child,depth+1)
        elif kind=='array':
            if set(node)!={'type','items'} or not isinstance(node['items'],list) or len(node['items'])>3:raise ValueError('响应形状数组超过范围')
            for child in node['items']:walk(child,depth+1)
        elif kind not in {'null','boolean','integer','number','string','unsupported','truncated','redacted'} or set(node)!={'type'}:
            raise ValueError('响应形状不能携带scalar原值')
    walk(shape['shape'],0)
    if len(json.dumps(shape).encode('utf-8'))>32768:raise ValueError('资产桥响应形状超过32KiB')
    return shape


def validate_resource_projection(result,candidate,namespace,sdk_sources):
    """Fail closed if a child adds raw bodies, changes identity or reports fake success."""
    fields={'observation','response_shape','public_projection_sha256','sdk','sdk_package','sdk_version','sdk_sources',
            'api_calls','metadata_bytes_read','credentials_exported','login_or_refresh_requested','platform_writes',
            'files_downloaded','account_scope_verified','mapping_state'}
    if not isinstance(result,dict) or set(result)!=fields:raise ValueError('资产桥公开投影字段不符，未保存原正文')
    path=validate_candidate(candidate);identifier(namespace)
    observation=result['observation']
    names={'uid','name','namespace','query_path','identity_state','version_identity_kind',
           'provenance_certified','platform_immutable_content_verified'}
    if not isinstance(observation,dict) or set(observation)!=names or observation['query_path']!=path:raise ValueError('资产桥候选身份不符')
    for field in ('uid','name','namespace'):
        if observation[field] is not None:identifier(observation[field])
    if observation['namespace'] not in {None,namespace} or candidate['uid'] is not None and observation['uid'] not in {None,candidate['uid']}:
        raise ValueError('资产桥返回了其他namespace/UID')
    if candidate['kind']!='tasks' and observation['name'] not in {None,candidate['version']}:raise ValueError('资产桥返回了其他版本')
    state='unversioned_resource_alias_unconfirmed' if candidate['kind']=='tasks' else 'query_identity_checked' if all(observation[k] for k in ('uid','name','namespace')) else 'query_identity_unconfirmed'
    version_kind='unversioned_task_output' if candidate['kind']=='tasks' else 'candidate_formal_version'
    if (observation['identity_state']!=state or observation['version_identity_kind']!=version_kind
            or observation['provenance_certified'] is not False or observation['platform_immutable_content_verified'] is not False):
        raise ValueError('资产桥不能伪装正式版本来源/不可变保证')
    shape=validate_shape(result['response_shape'])
    if result['public_projection_sha256']!=fingerprint({'observation':observation,'shape':shape}):raise ValueError('资产桥公开投影摘要不符')
    if result['sdk']!='official_asset' or result['sdk_package']!='lpai-asset' or result['sdk_version']!='3.0.23':raise ValueError('资产桥SDK身份不符')
    report=result['sdk_sources']
    if not isinstance(report,dict) or set(report)!=set(sdk_sources):raise ValueError('资产桥源码摘要清单不符')
    for name,expected in sdk_sources.items():
        if report[name]!={'sha256':expected,'matches_reviewed_source':True}:raise ValueError('资产桥源码与审核版本不符')
    if type(result['api_calls']) is not int or result['api_calls']!=1 or type(result['metadata_bytes_read']) is not int or not 0<result['metadata_bytes_read']<=2*1024*1024:
        raise ValueError('资产桥读取计量未核实')
    if (any(result[key] is not False for key in ('credentials_exported','login_or_refresh_requested','account_scope_verified'))
            or any(type(result[key]) is not int or result[key]!=0 for key in ('platform_writes','files_downloaded'))
            or result['mapping_state']!='mapping_unconfirmed'):raise ValueError('资产桥越过只读/未确认来源边界')
    return result


class TransferBudget:
    """Host-delivered byte/call/deadline accounting, not an SDK wire-byte guarantee."""
    def __init__(self,*,max_total=MAX_TOTAL,max_calls=24,timeout=60,clock=time.monotonic):
        if type(max_total) is not int or not 1<=max_total<=MAX_TOTAL:raise ValueError('累计读取额度需为1..64MiB')
        if type(max_calls) is not int or not 1<=max_calls<=24:raise ValueError('读取调用额度需为1..24')
        if type(timeout) not in (int,float) or not 0<timeout<=120:raise ValueError('读取期限需为0..120秒')
        self.max_total,self.max_calls,self.clock=max_total,max_calls,clock
        self.deadline=clock()+timeout;self.bytes_read=0;self.calls=0

    @property
    def remaining(self):return max(0,self.max_total-self.bytes_read)

    def check(self):
        if self.clock()>=self.deadline:raise TimeoutError('原回收截止时间已到；未换身份或重置额度')

    def call(self):
        self.check()
        if self.calls>=self.max_calls:raise ValueError('原读取调用额度已用尽')
        self.calls+=1

    def account(self,size):
        if type(size) is not int or size<0:raise ValueError('读取字节计量无效')
        self.bytes_read+=size  # Include failed/short/over-delivered bytes; never roll back.
        self.check()
        if self.bytes_read>self.max_total:raise ValueError('读取接口返回超过累计额度，未写入超额数据')


def stream_snapshot(stream,sink,budget,*,expected_size,max_file=MAX_FILE,expected_sha256=None):
    """Deterministic streaming primitive; no SDK, discovery, publication or grant.

    The caller must own the stream deadline/cleanup and publish only after HEAD
    and provenance verification. Failure leaves a partial sink, NOT evidence.
    A full-budget read cannot reserve an EOF check and is refused before I/O.
    """
    try:
        if type(max_file) is not int or not 0<max_file<=MAX_FILE:raise ValueError('单文件读取范围无效')
        if type(expected_size) is not int or not 0<expected_size<=max_file:raise ValueError('HEAD大小缺失、为空或超过单文件范围')
        if expected_sha256 is not None and (not isinstance(expected_sha256,str) or not SHA256.fullmatch(expected_sha256)):
            raise ValueError('预期文件SHA-256无效，ETag不能代替它')
        budget.check()
        if expected_size>=budget.remaining:raise ValueError('累计额度不足以读取并核对EOF；未开始读取')
        digest=hashlib.sha256();size=0
        while size<expected_size:
            budget.check();requested=min(64*1024,expected_size-size,budget.remaining)
            block=stream.read(requested)
            if not isinstance(block,bytes):raise ValueError('读取接口必须返回字节')
            budget.account(len(block))
            if len(block)>requested:raise ValueError('读取接口忽略请求大小；未把超额块写入归档')
            if not block:raise ValueError('原对象短读；保留已消耗额度，不发表完整证据')
            if sink.write(block)!=len(block):raise OSError('临时文件短写；未发表证据')
            size+=len(block);digest.update(block)
        extra=stream.read(1)
        if not isinstance(extra,bytes):raise ValueError('EOF核对未返回字节')
        budget.account(len(extra))
        if extra:raise ValueError('对象比HEAD范围大或读取时改变；未发表证据')
        actual=digest.hexdigest()
        if expected_sha256 and actual!=expected_sha256:raise ValueError('本次字节SHA-256与固定内容不符')
        return {'size':size,'sha256':actual,'snapshot_bytes_verified':True,
                'platform_immutable_content_verified':False,'provenance_certified':False}
    finally:
        stream.close()
