"""Frozen cloud output requirements and byte-backed result identity.

No model, network, submission, or scientific-method choice lives here. A run
receipt is a producer assertion checked against immutable local preparation;
it is not a signature or proof that the evaluation implementation is correct.
"""
import hashlib
import json

from . import evaluation_contract as evaluation
from .cloud_evidence import cloud_key
from .store import dumps
from .result_receipt_writer import digest

RECEIPT='iterlab-run.json'


def declaration(value):
    if not isinstance(value,dict) or 'required_files' not in value or set(value)-{'required_files','runtime','source'}:raise ValueError('结果清单只接受required_files及可选runtime/source')
    names=value['required_files']
    if not isinstance(names,list) or not 1<=len(names)<=5 or any(not isinstance(n,str) for n in names):raise ValueError('声明1—5个必要的小型结果文件，权重另建索引')
    if len(set(names))!=len(names) or RECEIPT in names:raise ValueError('结果清单重复或占用了运行收据名称')
    from .task_output_files import required_paths
    required_paths(names)
    result={'required_files':list(names)}
    from . import cloud_runtime
    if 'runtime' in value:result['runtime']=cloud_runtime.normalize(value['runtime'])
    if 'source' in value:result['source']=cloud_runtime.source(value['source'])
    if 'runtime' in result and 'source' not in result:raise ValueError('自动结果执行器需明确可回收的卷来源，不猜平台输出路径')
    if 'source' in result and 'runtime' not in result:raise ValueError('新卷来源必须随确切运行生产收据，不能任意绑定历史目录')
    if 'source' in result and result['runtime']['output_root']!=result['source']['mount_root']+'/'+result['source']['prefix']:raise ValueError('结果生产目录与回收卷前缀不一致')
    return result


def execution_files(store,project_id,value,plan=None):
    from .result_receipt_writer import file_ref
    from .task_output_files import local_path
    names=declaration(value).get('runtime',{}).get('work_files',[])
    if not names:return []
    root=local_path(store.safe_path(project_id,'.'))
    refs=[{'path':name,**file_ref(root,name,64*1024)} for name in names]
    if declaration(value).get('runtime',{}).get('layout')=='workspace':
        from .work_package import validate_refs
        validate_refs(plan,refs)
    return refs


def freeze(store,plan,trial_id,run_id,component,value,work_files=None):
    value=declaration(value);protocol=evaluation.load(store,plan)
    if protocol and protocol['spec']['artifact'] not in value['required_files']:raise ValueError('结果清单必须包含固定评价合同中的指标文件')
    expected={'schema':'iterlab-cloud-result/v1','run_id':run_id,'plan_id':plan['id'],
        'trial_id':trial_id,'component_uid':component['uid'],'component_digest':component['digest'],
        'evaluation_contract':protocol['reference'] if protocol else None,
        'protocols':protocol['protocols'] if protocol else [],'required_files':value['required_files']}
    if value.get('runtime'):expected['runtime']={**value['runtime'],'input_root':value['runtime']['input_root']+'/'+run_id,'output_root':value['runtime']['output_root']+'/'+run_id}
    if value.get('source'):expected['source']={**value['source'],'prefix':value['source']['prefix']+'/'+run_id}
    if value.get('runtime',{}).get('work_files'):expected['execution_files']=execution_files(store,plan['project_id'],value,plan) if work_files is None else work_files
    return {'expected':expected,'sha256':digest(expected)}


def bundle(store,pid,frozen):
    """Materialize an executable producer helper, without changing cloud entrypoints.

    Training implementation must copy/invoke it after results are written. Missing
    integration fails closed at collection; preparing never edits shared code.
    """
    from pathlib import Path
    directory='research/cloud-results/'+frozen['expected']['run_id']
    files={'contract.json':(json.dumps(frozen,ensure_ascii=False,indent=2)+'\n').encode(),
           'write_result_receipt.py':Path(__file__).with_name('result_receipt_writer.py').read_bytes()}
    for name,body in files.items():
        path=store.safe_path(pid,directory+'/'+name);path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if path.read_bytes()!=body:raise ValueError('已冻结的结果生产工作包发生变化，未覆盖')
        else:
            with path.open('xb') as stream:stream.write(body)
    return {'directory':directory,'contract':directory+'/contract.json','writer':directory+'/write_result_receipt.py',
        'files':[{'path':directory+'/'+name,'sha256':hashlib.sha256(body).hexdigest()} for name,body in files.items()],
        'integration_required':not bool(frozen['expected'].get('runtime')),'message':('结果执行器将随正式任务配置提交；训练代码须将必要结果写入ITERLAB_OUTPUT_DIR，实际退出后自动生成收据。准备不代表云端已执行。' if frozen['expected'].get('runtime') else '训练实现须在实际运行环境中执行write_result_receipt.py --contract contract.json --inputs <输入根> --outputs <结果根>；不会自动修改共享组件入口。')}


def requirements(frozen):
    return [RECEIPT]+frozen['expected']['required_files'] if frozen else []


def validate_archive(root,manifest,frozen):
    """Checks already hash-verified local files; returns gaps without inventing data."""
    from .task_output_files import local_path
    root=local_path(root);expected=frozen['expected']
    if digest(expected)!=frozen['sha256']:raise ValueError('冻结结果合同摘要不符')
    entries={e['name']:e for e in manifest['files']}
    if expected.get('source') and manifest.get('result_source')!={**expected['source'],'run_id':expected['run_id']}:raise ValueError('本地归档不是冻结运行的卷来源')
    missing=[n for n in requirements(frozen) if n not in entries or entries[n].get('partial')]
    if missing:return {'state':'missing_required','missing_files':missing,'identity_verified':False}
    receipt=entries[RECEIPT]
    if receipt['size']>64*1024:raise ValueError('运行收据超过64KiB')
    data=evaluation.json_text((root/receipt['object']).read_text(encoding='utf-8'))
    required_keys={'expected','contract_sha256','files','protocols'}|({'execution_files'} if expected.get('execution_files') else set())
    if not isinstance(data,dict) or set(data)!=required_keys:raise ValueError('运行收据格式不符')
    if data['expected']!=expected or data['contract_sha256']!=frozen['sha256']:raise ValueError('结果不是本次冻结运行/方案/实验')
    protocols={ref['path']:ref['sha256'] for ref in expected['protocols']}
    if expected['evaluation_contract']:protocols[expected['evaluation_contract']['path']]=expected['evaluation_contract']['sha256']
    if data['protocols']!=protocols:raise ValueError('运行中的评价协议或划分字节与冻结合同不同')
    if expected.get('execution_files') and data['execution_files']!={ref['path']:ref['sha256'] for ref in expected['execution_files']}:raise ValueError('实际云端执行文件与冻结工作包不同')
    observed={n:{'sha256':entries[n]['sha256'],'size':entries[n]['size']} for n in expected['required_files']}
    if data['files']!=observed:raise ValueError('运行收据与实际结果文件不一致，不能借用其他实验结果')
    return {'state':'identity_verified','missing_files':[],'identity_verified':True,'scientific_result_validated':False,
            'meaning':'仅核对运行归属、结果字节及生产者声明的协议摘要；不认证评价代码或工业泛化。'}


def read(store,pid,run_id):
    """Cloud source for the same deterministic evaluator used by local runs."""
    from .task_output_files import verify_archive,local_path
    run=store.one('SELECT * FROM company_training WHERE id=? AND project_id=?',(run_id,pid))
    if not run:raise ValueError('云运行不属于当前项目')
    if run['state'].lower() not in {'success','succeeded','completed'}:raise ValueError('非成功云运行只供异常分析，不能参加数值比较')
    frozen=json.loads(run['request']).get('output_contract')
    if not frozen:raise ValueError('原云运行未冻结结果归属合同，不补造历史身份')
    expected=frozen['expected']
    if (expected['run_id'],expected['plan_id'],expected['trial_id'],expected['component_uid'])!=(run['id'],run['plan_id'],run['trial_id'],run['component_uid']):raise ValueError('结果合同不属于原提交身份')
    rows=store.rows('SELECT * FROM company_output_reads WHERE session_id=? AND task_uid=? ORDER BY attempt DESC',(run['session_id'],run['task_uid']))
    if not rows:raise ValueError('原云运行没有本地结果归档')
    row=rows[0]
    if row['state']!='files_archived':raise ValueError('最新结果读取未完整确认，不择优使用旧尝试')
    result=json.loads(row['result']);root=local_path(store.safe_path(pid,row['path']))
    manifest=verify_archive(root/'files',run['task_uid'],row['namespace'])
    if hashlib.sha256((root/'files/manifest.json').read_bytes()).hexdigest()!=result['manifest_sha256']:raise ValueError('原云端归档清单已变化')
    if not validate_archive(root/'files',manifest,frozen)['identity_verified']:raise ValueError('必要结果缺失，不能进行数值核验')
    return run,frozen,manifest,root/'files',result
