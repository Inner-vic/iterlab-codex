"""Versioned numeric conditions attached to a plan, never a research strategy."""
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
import re

from .store import dumps,uid
from .run_evidence import relative,checksum


def json_text(text):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('JSON字段重复，不能猜测使用哪一个：'+key)
            value[key]=item
        return value
    def constant(value):raise ValueError('指标含非有限数字：'+value)
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)


def number(value):
    try:valid=type(value) in {int,float} and math.isfinite(value)
    except OverflowError:valid=False
    if not valid:raise ValueError('指标需为有限数值，不能用布尔值、字符串或NaN代替')
    return Decimal(str(value))


def pointer(value,path):
    if not isinstance(path,str) or len(path)>300 or path and not path.startswith('/') or re.search(r'~(?![01])',path):
        raise ValueError('需要有效的JSON Pointer')
    for segment in path.split('/')[1:] if path else []:
        key=segment.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not re.fullmatch(r'0|[1-9][0-9]*',key):raise ValueError('数组索引无效')
            value=value[int(key)]
        else:value=value[key]
    return value


def scalar(value):
    if type(value) not in {str,int} or isinstance(value,str) and (not value or len(value)>100):raise ValueError('组别/配对身份需为明确的字符串或整数')
    return type(value).__name__+':'+str(value)


def validate(spec):
    required={'schema','title','artifact','rows','group_by','pair_by','baseline','candidate','pairs','protocol_files','rules'}
    if not isinstance(spec,dict):raise ValueError('评价合同须为完整spec对象；后继边界未变时省略evaluation_contract即可继承，不要复制snapshot.reference')
    if set(spec)!=required or type(spec.get('schema')) is not int or spec['schema']!=1:
        raise ValueError('评价合同字段/版本无效：schema必须为整数1；缺少='+','.join(sorted(required-set(spec)))+
            '；多余='+','.join(sorted(set(spec)-required))+'。后继边界未变时省略evaluation_contract即可继承原合同，不要把path/sha256引用当作spec或重新猜测条件')
    if not isinstance(spec['title'],str) or not spec['title'].strip() or len(spec['title'])>100:raise ValueError('评价条件需要简洁名称')
    relative(spec['artifact'])
    for field in ['rows','group_by','pair_by']:
        pointer({},spec[field]) if not spec[field] else _valid_pointer(spec[field])
    if spec['baseline'] is not None and scalar(spec['baseline'])==scalar(spec['candidate']):raise ValueError('对照与候选不能相同')
    scalar(spec['candidate'])
    if not isinstance(spec['pairs'],list) or not 1<=len(spec['pairs'])<=20 or len({scalar(x) for x in spec['pairs']})!=len(spec['pairs']):raise ValueError('配对身份需为1—20个不重复值')
    paths=spec['protocol_files']
    if not isinstance(paths,list) or not 1<=len(paths)<=12 or len({relative(x) for x in paths})!=len(paths):raise ValueError('明确固定的数据划分/评价协议文件，不能仅凭指标自报可比')
    if not isinstance(spec['rules'],list) or not 1<=len(spec['rules'])<=16:raise ValueError('评价条件需为1—16项')
    seen=set()
    for rule in spec['rules']:
        required_rule={'key','label','value','direction','aggregation','comparison'}
        if not isinstance(rule,dict) or not required_rule<=set(rule) or set(rule)-required_rule-{'tolerance','target','bounds'}:raise ValueError('评价条件字段无效')
        if not isinstance(rule['key'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,60}',rule['key']) or rule['key'] in seen:raise ValueError('评价条件key需唯一')
        seen.add(rule['key'])
        if not isinstance(rule['label'],str) or not rule['label'].strip() or len(rule['label'])>100 or any(x in rule['label'] for x in '\n\r|'):raise ValueError('指标名称无效')
        _valid_pointer(rule['value'])
        if rule['direction'] not in {'maximize','minimize'} or rule['aggregation'] not in {'each_pair','mean'} or rule['comparison'] not in {'improve','noninferior','target'}:raise ValueError('指标方向/聚合/比较方式无效')
        tolerance=number(rule.get('tolerance',0))
        if tolerance<0:raise ValueError('容差不能为负')
        if rule['comparison']=='target':
            number(rule.get('target'))
            if tolerance!=0:raise ValueError('目标阈值不能借容差降低')
        elif 'target' in rule:raise ValueError('仅target条件接收目标阈值')
        if 'bounds' in rule:
            bounds=rule['bounds']
            if not isinstance(bounds,list) or len(bounds)!=2 or number(bounds[0])>number(bounds[1]):raise ValueError('指标范围无效')
            if rule['comparison']=='target' and not number(bounds[0])<=number(rule['target'])<=number(bounds[1]):raise ValueError('目标阈值与指标范围/单位不符')
        if spec['baseline'] is None and rule['comparison']!='target':raise ValueError('没有对照时只支持目标阈值核验，不宣称相对改进')
    if len(dumps(spec).encode())>48*1024:raise ValueError('评价合同过长')
    return spec


def _valid_pointer(path):
    if not isinstance(path,str) or len(path)>300 or path and not path.startswith('/') or re.search(r'~(?![01])',path):raise ValueError('需要有效JSON Pointer')


def description(spec):
    lines=['### 固定的数值评价条件','', '| 指标 | 比较范围 | 通过条件 |','|---|---|---|']
    for rule in spec['rules']:
        direction='提高' if rule['direction']=='maximize' else '降低'
        margin=rule.get('tolerance',0)
        text={'improve':direction+('超过 '+str(margin) if margin else ''),
              'noninferior':('相对对照允许最多'+('下降 ' if rule['direction']=='maximize' else '上升 ')+str(margin)) if margin else '不变差',
              'target':('至少 ' if rule['direction']=='maximize' else '至多 ')+str(rule.get('target',''))}[rule['comparison']]
        lines.append('| '+rule['label']+' | '+('每组配对均满足' if rule['aggregation']=='each_pair' else '配对均值')+' | '+text+' |')
    lines+=['','数值核验只覆盖以上条件，不证明统计显著性、评价代码正确或工业泛化，也不授予新的执行权限。']
    return '\n'.join(lines)


def load(store,plan):
    ref=json.loads(plan['source']).get('evaluation_contract')
    if not ref:return None
    refs={r['path']:r for r in json.loads(plan['evidence'])}
    if refs.get(ref['path'],{}).get('sha256')!=ref['sha256']:raise ValueError('评价合同不在方案冻结证据中')
    path=store.safe_path(plan['project_id'],ref['path'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024:raise ValueError('评价合同原件不可用')
    body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('评价合同字节已改变，不能沿用旧审核')
    value=json_text(body.decode('utf-8'));validate(value['spec'])
    if {r['path'] for r in value['protocols']}!={relative(p) for p in value['spec']['protocol_files']}:raise ValueError('评价协议清单不符')
    for protocol in value['protocols']:
        if refs.get(protocol['path'])!=protocol:raise ValueError('评价协议未在当前方案中保持固定')
    return value|{'reference':ref}


def prepare_plan(store,session,record,checked,content,source):
    previous=store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'",(record.get('previous_plan_id'),session['project_id'])) if record.get('previous_plan_id') else None
    parent=load(store,previous) if previous and json.loads(previous['source']).get('evaluation_contract') else None
    if 'evaluation_contract' not in record and not parent:return checked,content,source
    spec=record.get('evaluation_contract',parent['spec'] if parent else None)
    if spec is None:
        source.pop('evaluation_contract',None);source['evaluation_contract_change']='removed'
        return checked,content+'\n\n本方案明确移除数值评价合同；不能再沿用原数值核验结论。',source
    validate(spec);refs={item['path']:item for item in checked}
    if parent and 'evaluation_contract' not in record:
        for ref in parent['protocols']:refs.setdefault(ref['path'],ref)
    protocols=[]
    for name in spec['protocol_files']:
        name=relative(name)
        if name not in refs:raise ValueError('评价协议文件需纳入方案证据：'+name)
        ref=refs[name];path=store.safe_path(session['project_id'],name)
        if not path.is_file() or checksum(path)!=ref['sha256']:raise ValueError('固定评价协议已改变：'+name)
        protocols.append(ref)
    value={'schema':1,'spec':spec,'protocols':protocols};body=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8');digest=hashlib.sha256(body).hexdigest()
    if len(body)>64*1024:raise ValueError('评价合同快照过长')
    name='research-contracts/'+digest[:24]+'.json';path=store.safe_path(session['project_id'],name);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=body:raise ValueError('原评价合同快照已改变，未覆盖')
    else:
        temporary=path.with_name(uid()+'.tmp')
        try:temporary.write_bytes(body);temporary.replace(path)
        finally:
            if temporary.exists():temporary.unlink()
    ref={'path':name,'sha256':digest,'size':len(body)};refs[name]=ref
    source['evaluation_contract']={'path':name,'sha256':digest,'version':1}
    source['evaluation_contract_change']='declared' if 'evaluation_contract' in record else 'inherited'
    if source.get('scope_proposal'):
        proposal=dict(source['scope_proposal'])
        paths=proposal.get('frozen_files',[])
        if not isinstance(paths,list) or any(not isinstance(path,str) for path in paths):
            raise ValueError('scope_proposal.frozen_files须为相对路径字符串数组，不是path/sha256对象；文件摘要由程序保存。边界未变的后继省略scope_proposal继承原授权')
        proposal['frozen_files']=list(dict.fromkeys([*paths,name,*[r['path'] for r in protocols]]))
        source['scope_proposal']=proposal
    suffix='\n\n'+description(spec)
    # snapshot returns the saved body including this program-owned appendix.
    # A metadata repair may preserve that exact body; do not grow the same
    # numerical table again on every read/write cycle. Never strip user text.
    return list(refs.values()),content if content.endswith(suffix) else content+suffix,source


def require_scope_files(store,plan,scope):
    value=load(store,plan)
    if value:
        paths={ref['path'] if isinstance(ref,dict) else ref for ref in scope['frozen_files']}
        required={value['reference']['path'],*[ref['path'] for ref in value['protocols']]}
        if not required<=paths:raise ValueError('数值评价合同和协议需一并纳入初始自主授权的固定文件；不能事后换口径')


SCHEMA={'type':['object','null'],'description':'仅plan可选：事先固定的数值条件；程序保存冻结快照并在方案中展示人可读条件。后继缺省继承，显式null表示移除并需重新审核。无需为不适合数值判据的问题硬填。',
    'properties':{'schema':{'type':'integer','enum':[1]},'title':{'type':'string'},'artifact':{'type':'string','description':'声明输出中的JSON指标文件，相对运行目录'},
    'rows':{'type':'string','description':'JSON Pointer，指向结果对象或对象列表'},'group_by':{'type':'string','description':'每行组别的JSON Pointer'},
    'pair_by':{'type':'string','description':'每行seed/fold等配对身份的JSON Pointer'},'baseline':{'type':['string','integer','null']},'candidate':{'type':['string','integer']},
    'pairs':{'type':'array','items':{'type':['string','integer']},'minItems':1,'maxItems':20,'uniqueItems':True},
    'protocol_files':{'type':'array','items':{'type':'string'},'description':'已纳入方案证据的数据划分/评价协议文件，不要求固定可改的模型配置'},
    'rules':{'type':'array','minItems':1,'maxItems':16,'items':{'type':'object','properties':{
        'key':{'type':'string'},'label':{'type':'string'},'value':{'type':'string','description':'每行数值的JSON Pointer'},
        'direction':{'type':'string','enum':['maximize','minimize']},'aggregation':{'type':'string','enum':['each_pair','mean']},
        'comparison':{'type':'string','enum':['improve','noninferior','target']},'target':{'type':'number'},'tolerance':{'type':'number','minimum':0},
        'bounds':{'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2}},
        'required':['key','label','value','direction','aggregation','comparison'],'additionalProperties':False}}},
    'required':['schema','title','artifact','rows','group_by','pair_by','baseline','candidate','pairs','protocol_files','rules'],'additionalProperties':False}
