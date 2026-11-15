"""Explicit plan recording over a pinned older native tool schema.

This only decodes a research_record invocation. Messages, files and logs are
never inspected; the normal role, provenance, evidence and approval gates run
after decoding. No grant or platform action is represented by this format.
"""
import json
import math

from .evaluation_contract import json_text

TITLE='__iterlab_plan_record_v1__'
SCHEMA='iterlab-plan-record/v1'


def _unique_key(value,depth=0):
    if depth>40:raise ValueError('兼容方案结构嵌套过深')
    if type(value) in {int,float}:
        try:
            if not math.isfinite(value):raise ValueError('兼容方案含非有限数值')
            return ('number',value.as_integer_ratio() if type(value) is float else (value,1))
        except OverflowError as exc:raise ValueError('兼容方案数值越界') from exc
    if isinstance(value,list):return ('array',tuple(_unique_key(v,depth+1) for v in value))
    if isinstance(value,dict):return ('object',tuple((k,_unique_key(v,depth+1)) for k,v in sorted(value.items())))
    return (type(value).__name__,value)


def validate(value,schema):
    """Validate the actual recording schema's basic JSON types and bounds."""
    pending=[(value,schema,'record',0)]
    types={'object':dict,'array':list,'string':str,'integer':int,'number':(int,float),'boolean':bool,'null':type(None)}
    supported={'type','properties','required','additionalProperties','items','enum','minimum','maximum',
        'minItems','maxItems','uniqueItems','minLength','maxLength','description','title','default'}
    while pending:
        item,rule,path,depth=pending.pop()
        if depth>40:raise ValueError('兼容方案结构嵌套过深')
        if set(rule)-supported:raise ValueError('当前方案协议需要更新兼容校验器：'+path)
        required_type=rule.get('type')
        if required_type:
            names=required_type if isinstance(required_type,list) else [required_type]
            matches=any(type(item) in (types[n] if isinstance(types[n],tuple) else (types[n],)) for n in names)
            if not matches:raise ValueError('兼容方案字段类型错误：'+path)
        if type(item) in {int,float}:
            try:finite=math.isfinite(item)
            except OverflowError:finite=False
            if not finite or 'minimum' in rule and item<rule['minimum'] or 'maximum' in rule and item>rule['maximum']:
                raise ValueError('兼容方案数值越界：'+path)
        if 'enum' in rule and item not in rule['enum']:raise ValueError('兼容方案字段取值无效：'+path)
        if isinstance(item,dict):
            properties=rule.get('properties',{});missing=set(rule.get('required',[]))-set(item)
            if missing:
                hint=('。后继评价未变时直接省略evaluation_contract，程序继承原冻结合同；不要复制snapshot的path/sha256引用或spec/protocols归档包装。只有声明新条件才填写完整spec，仍需原授权校验'
                    if path=='record.evaluation_contract' else '')
                raise ValueError('兼容方案缺少字段：'+path+'.'+','.join(sorted(missing))+hint)
            extra=set(item)-set(properties)
            if extra and rule.get('additionalProperties') is False:raise ValueError('兼容方案包含未知字段：'+path+'.'+','.join(sorted(extra)))
            for key,child in item.items():
                child_rule=properties.get(key,rule.get('additionalProperties',{}))
                if isinstance(child_rule,dict):pending.append((child,child_rule,path+'.'+key,depth+1))
        elif isinstance(item,list):
            if 'maxItems' in rule and len(item)>rule['maxItems'] or 'minItems' in rule and len(item)<rule['minItems']:
                raise ValueError('兼容方案数组长度越界：'+path)
            if rule.get('uniqueItems') and len({_unique_key(v,depth+1) for v in item})!=len(item):
                raise ValueError('兼容方案数组元素重复：'+path)
            if 'items' in rule:
                pending.extend((v,rule['items'],path+'[]',depth+1) for v in item)
        elif isinstance(item,str):
            if 'maxLength' in rule and len(item)>rule['maxLength'] or 'minLength' in rule and len(item)<rule['minLength']:
                raise ValueError('兼容方案文本长度越界：'+path)


def decode_plan_record(arguments,record_schema):
    if arguments.get('title')!=TITLE:return None
    outer={'kind','title','summary','content','status','evidence'}
    # Some pinned clients serialize every optional field. Neutral outer array
    # placeholders carry no plan/control value; inner record remains strict.
    neutral={key for key,value in arguments.items() if key not in outer
        and key in record_schema.get('properties',{}) and value==[]}
    extra=set(arguments)-outer-neutral
    if extra:
        inner_error=''
        try:decode_plan_record({k:v for k,v in arguments.items() if k not in extra},record_schema)
        except ValueError as exc:inner_error='；内层同时需要修正：'+str(exc)[:300]
        raise ValueError('旧线程方案兼容记录外层只允许kind/title/summary/content/status/evidence；以下字段请放入content.record，不在外层混入：'+','.join(sorted(extra))[:240]+inner_error)
    if (arguments.get('kind')!='plan' or arguments.get('status')!='proposed' or arguments.get('evidence')!=[]
            or not isinstance(arguments.get('content'),str) or len(arguments['content'].encode())>512*1024):
        raise ValueError('旧线程方案兼容记录只接收明确的待审plan信封，不混入其他操作')
    try:value=json_text(arguments['content'])
    except (ValueError,TypeError,RecursionError) as exc:raise ValueError('方案兼容信封需为无重复字段的有限JSON对象') from exc
    if not isinstance(value,dict) or set(value)!={'schema','record'} or value['schema']!=SCHEMA:
        raise ValueError('不支持的方案兼容信封')
    record=value['record'];validate(record,record_schema)
    if (record.get('kind')!='plan' or record.get('status')!='proposed'
            or not record.get('title','').strip() or record['title'].startswith('__iterlab_') or not record.get('content','').strip()):
        raise ValueError('方案兼容信封只能保存具有人类可读标题和正文的待审plan')
    return record


def retry_template(arguments,record_schema):
    """Suggest only; the rejected invocation remains rejected and unrecorded.

    Keep the caller's exact inner content, never merge conflicting outer data.
    The model must decide whether this is its intended plan and explicitly retry;
    ordinary role/evidence/approval checks apply to that future invocation.
    """
    allowed={'kind','title','summary','content','status','evidence'}
    extra=set(arguments)-allowed
    if (arguments.get('title')!=TITLE or not extra
            or not extra<=set(record_schema.get('properties',{}))):return None
    minimal={key:arguments[key] for key in arguments if key in allowed}
    try:
        if len(json.dumps(minimal,ensure_ascii=False,allow_nan=False).encode())>32768:return None
        decode_plan_record(minimal,record_schema)
    except (ValueError,TypeError,RecursionError):return None
    return {'retry_arguments':minimal,'meaning':'仅格式建议，原请求仍被拒绝，未保存/批准/执行。content与上次逐字相同；未合并外层字段。若外层另有意图，先明确写入record，再重试。重试时只发送这六个键，不补旧可选字段，即使为空。'}


INSTRUCTIONS='''旧线程若research_record.scope_proposal未列出component_config/compute/budget，不能只在正文说已补齐。
使用原research_record的显式兼容记录：外层kind=plan，title=__iterlab_plan_record_v1__，status=proposed，summary说明兼容保存，evidence=[]；content是JSON对象{"schema":"iterlab-plan-record/v1","record":{完整正常plan参数}}。
外层只发送这六个键，不能复制普通record的全字段或补空默认值。正常plan参数全部在content内record。可选字段无实际含义就省略，禁止用null代替string/object。后继评价未变时省略evaluation_contract，由程序继承原冻结合同，不复制snapshot的path/sha256引用或spec/protocols归档包装；仅声明新条件时填写完整spec，不写摘要字符串。继承原研究范围时同样省略scope_proposal，用原authority_id/scope_digest与前轮关系，不重新措辞声明边界。若格式拒绝，先按返回的具体字段修正，不能反复提交同一参数。
record仍有kind=plan、真实标题/Markdown正文/摘要、status=proposed、实际evidence、原方案与正式评审关系和促成依据；scope_proposal可包含当前component_config路径、compute={gpu_count,workflow}、budget={additional_runs,single_attempt_seconds}等明确字段。只在此工具调用中使用该信封，普通消息/文件不作控制。
宿主按当前同一记录协议校验并保存新的可读方案，旧记录不改写；返回的实际字段才算保存成功。兼容记录不批准方案、GPU或平台操作，角色/轮次/证据及执行校验不变。'''
