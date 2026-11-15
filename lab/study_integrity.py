"""SDM research safeguards on existing records/contracts, not a second lifecycle.

References and derived numeric outcomes are checked by code. Scientific meaning,
independence and applicability remain explicit, attributed research judgments.
"""
import hashlib
import json
from pathlib import Path

from .store import dumps

REVISION = 'study-integrity-1'
ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
OUTCOMES = {'supported':'支持', 'refuted':'反对', 'inconclusive':'证据不足', 'invalid':'比较无效'}
LENSES = ['domain', 'leakage', 'baseline_fairness', 'statistical_validity', 'deployment_parity']
TRUST = {'review_independence':'not_attested', 'signatures':'not_configured', 'external_anchor':'not_configured',
         'evidence_assurance':'local_identity_and_hash_checks',
         'meaning':'同线程角色审议不是隔离盲审；本地留痕不提供外部不可抵赖证明；不授予运行权限。'}


def obj(properties, required):
    return {'type':'object','additionalProperties':False,'properties':properties,'required':required}


STR = {'type':'string'}
IDS = {'type':'array','maxItems':20,'items':STR}
CHECK = obj({'basis':{'type':'string','enum':['source','calculation']},'claim':STR,'reason':STR,
             'source_quotes':{'type':'array','maxItems':4,'items':obj({'path':STR,'text':STR},['path','text'])},
             'command_ids':{'type':'array','maxItems':6,'items':STR}},
            ['basis','claim','reason','source_quotes','command_ids'])
FIELDS = {
    'baseline_selection':obj({'scope':STR,'candidate_record_id':STR,'candidate':STR,'identity_files':IDS,
                             'previous_selection_id':{'type':['string','null']},'reason':STR,'comparison_impact':STR},
                            ['scope','candidate_record_id','candidate','identity_files','previous_selection_id','reason','comparison_impact']),
    'baseline_ref':dict(STR),
    'confirmation_plan':obj({'selection_record_id':STR,'search_comparison_ids':IDS,'candidate_files':IDS,
                             'independence_reason':STR},
                            ['selection_record_id','search_comparison_ids','candidate_files','independence_reason']),
    'scientific_assessment':obj({'claim':STR,'question_id':STR,'basis':{'type':'string','enum':['comparison_rules','evidence_review']},
                                 'level':{'type':'string','enum':['exploratory','confirmatory','diagnostic']},
                                 'outcome':{'type':'string','enum':list(OUTCOMES)},'reason':STR,'limitations':STR,
                                 'evidence':IDS,'supersedes':STR},
                                ['claim','basis','level','reason','limitations','evidence']),
    'study_review':obj({'readiness':{'type':'array','maxItems':3,'items':obj({
        'phase':{'type':'string','enum':['planning','protocol','implementation']},
        'status':{'type':'string','enum':['checked','needs_evidence','not_applicable']},'reason':STR,
        'checks':{'type':'array','maxItems':4,'items':CHECK}},['phase','status','reason','checks'])},
        'lenses':{'type':'array','maxItems':5,'items':obj({
        'lens':{'type':'string','enum':LENSES},'status':{'type':'string','enum':['checked','needs_evidence','not_applicable']},
        'reason':STR,'checks':{'type':'array','maxItems':4,'items':CHECK}},['lens','status','reason','checks'])}},['readiness','lenses']),
}
FIELDS['baseline_selection']['description']='仅明确选择/更新基线时填写；探索候选不自动锁基线。scope为适用目标，旧比较不随更新改写。'
FIELDS['baseline_ref']['description']='本方案/实验使用的确切基线选择记录ID；以后更换基线不改变此引用。'
FIELDS['confirmation_plan']['description']='仅确认方案：引用已经保存的选择和全部相关搜索比较，冻结相同候选/评价规则与新配对单位。当前实现核对新seed/fold等pair，不能自行认证统计独立。'
FIELDS['scientific_assessment']['description']='科学结论与停止原因分开。comparison_rules由程序计算四态（不得传outcome），只判断冻结数值条件；evidence_review是有来源的研究判断，不能自报确认性数值通过。'
FIELDS['study_review']['description']='按适用风险记录就绪/五视角及真实核查；未声明为未知，不要求每轮全做，不以字段齐全认证科学正确。'


def policy_bytes():
    return (dumps({'revision':REVISION,'sha256':ENGINE_SHA256})+'\n').encode()


def enabled(store, sid):
    from .sdm import SDM, io_path
    binding = SDM(store).binding(sid)
    name = 'integrity-engine.json'
    if binding['mode'] != 'builtin' or name not in json.loads(binding['manifest']):
        return False
    return io_path(Path(binding['path']).parent/name).read_bytes() == policy_bytes()


def text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>4000:
        raise ValueError('研究依据需为1—4000字符的明确说明')
    return value.strip()


def ids(value, maximum=20, required=False):
    if not isinstance(value,list) or len(value)>maximum or any(not isinstance(i,str) or not i for i in value) or len(set(value))!=len(value) or required and not value:
        raise ValueError('研究引用需为有界、不重复的实际身份列表')
    return value


def record_ref(store, pid, rid, kinds=None):
    row=store.one('SELECT * FROM records WHERE id=? AND project_id=?',(rid,pid)) if isinstance(rid,str) else None
    if not row or kinds and row['kind'] not in kinds:
        raise ValueError('研究依据必须是同项目的适用原记录')
    return row


def payload(record):
    return json.loads(record['source']).get('study_integrity',{})


def current_baseline(store, pid, scope):
    row=store.one("SELECT * FROM records WHERE project_id=? AND json_extract(source,'$.study_integrity.baseline_selection.scope')=? ORDER BY rowid DESC LIMIT 1",(pid,scope))
    return row


def files(store, pid, names, refs, prior=None):
    selected=[]
    for name in ids(names,required=True):
        ref=refs.get(name)
        if not ref or prior is not None and prior.get(name,{}).get('sha256')!=ref['sha256']:
            raise ValueError('候选身份文件必须保持原证据身份，并列入本次冻结证据：'+name)
        path=store.evidence_path(pid,name)
        with path.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=ref['sha256']:raise ValueError('研究身份文件已变化：'+name)
        selected.append(ref)
    return selected


def prepare_record(store, session, record, checked, source):
    if not any(k in record for k in FIELDS):return source
    if not enabled(store,session['id']):raise ValueError('当前固定方法未包含研究完整性机制，须空闲时显式升级；不改旧方法')
    pid=session['project_id'];kind=record['kind'];refs={r['path']:r for r in checked}
    result={'revision':REVISION,'engine_sha256':ENGINE_SHA256,'trust':TRUST}
    if 'baseline_selection' in record:
        if kind not in {'research','decision','review'}:raise ValueError('基线选择应保存为研究决策，不是一次训练')
        choice=record['baseline_selection']
        if not isinstance(choice,dict) or set(choice)!=set(FIELDS['baseline_selection']['properties']):raise ValueError('基线选择字段不完整')
        scope=text(choice['scope']);prior=current_baseline(store,pid,scope)
        if choice['previous_selection_id']!=(prior['id'] if prior else None):raise ValueError('基线选择已更新或原选择引用不符，先核对当前适用范围')
        candidate=record_ref(store,pid,choice['candidate_record_id'],{'result','research','trial'})
        selected=files(store,pid,choice['identity_files'],refs,{r['path']:r for r in json.loads(candidate['evidence'])})
        result['baseline_selection']={**choice,'scope':scope,'candidate':text(choice['candidate']),
            'reason':text(choice['reason']),'comparison_impact':text(choice['comparison_impact']),
            'identity':selected,'candidate_content_sha256':hashlib.sha256(candidate['content'].encode()).hexdigest(),
            'meaning':'明确研究选择，不认证冠军/复现成功；历史引用不变。'}
    if 'baseline_ref' in record:
        selected=record_ref(store,pid,record['baseline_ref'])
        if not payload(selected).get('baseline_selection'):raise ValueError('baseline_ref必须引用明确的基线选择记录')
        result['baseline_ref']=selected['id']
    if 'confirmation_plan' in record and kind!='plan':
        raise ValueError('确认约定只能声明在事前方案上')
    if 'study_review' in record:
        review=record['study_review']
        if not isinstance(review,dict) or set(review)!={'readiness','lenses'}:raise ValueError('研究审查需readiness和lenses')
        from .inquiry import prepare_checks
        prepared={}
        for section,key,allowed,limit in [('readiness','phase',{'planning','protocol','implementation'},3),('lenses','lens',set(LENSES),5)]:
            values=review[section]
            if not isinstance(values,list) or len(values)>limit:raise ValueError('研究审查列表超过范围')
            entries=[];seen=set()
            for entry in values:
                if not isinstance(entry,dict) or set(entry)!={key,'status','reason','checks'} or entry[key] not in allowed or entry[key] in seen:raise ValueError('研究审查项目未知或重复')
                seen.add(entry[key]);text(entry['reason'])
                if entry['status'] not in {'checked','needs_evidence','not_applicable'}:raise ValueError('研究审查状态无效')
                if not isinstance(entry['checks'],list) or len(entry['checks'])>4:raise ValueError('每项最多4份核查依据')
                checks=prepare_checks(store,session,[dict(c,topic='decision') for c in entry['checks']],checked)
                if entry['status']=='checked' and (not checks or any(c['basis']=='unverified' for c in checks)):
                    raise ValueError('已核查需要实际原文或成功计算收据，不接受空勾选')
                entries.append({**entry,'checks':checks})
            prepared[section]=entries
        prepared['meaning']='适用性与解释由研究者负责；未记录项目为未知，非默认通过。'
        result['study_review']=prepared
    if 'scientific_assessment' in record:
        result['scientific_assessment']=assess(store,session,record,checked,source)
    return {**source,'study_integrity':result}


def prepare_plan(store, session, record, checked, source):
    """Called after the existing evaluation contract is frozen."""
    if 'confirmation_plan' not in record:return source
    if not enabled(store,session['id']):raise ValueError('确认机制未固定，须显式升级方法')
    pid=session['project_id'];config=record['confirmation_plan']
    if not isinstance(config,dict) or set(config)!=set(FIELDS['confirmation_plan']['properties']):raise ValueError('确认方案字段不完整')
    ids(config['candidate_files'],required=True)
    choice=record_ref(store,pid,config['selection_record_id'],{'research','decision','review'})
    from .evaluation_contract import load, scalar
    from .comparison import read_report
    current=load(store,{'project_id':pid,'source':dumps(source),'evidence':dumps(checked)})
    if not current:raise ValueError('确认方案需要事前冻结的数值评价合同')
    search_ids=ids(config['search_comparison_ids'],maximum=8,required=True)
    declared={r['id'] for r in json.loads(choice['source']).get('comparisons',[])}
    if set(search_ids)!=declared:raise ValueError('确认须保留选择记录引用的完整比较集合，不能事后只挑赢家')
    reports=[];search_pairs=set();search_runs=set();artifact_hashes=set();search_files={}
    for cid in search_ids:
        report=read_report(store,pid,cid)
        if report['state']!='verified':raise ValueError('不能根据不可比较的搜索结果登记确认')
        old_plan=record_ref(store,pid,report['plan_id'],{'plan'});old=load(store,old_plan)
        if not old:raise ValueError('搜索评价合同缺失')
        # Keep evaluator/target/selection identity; new pair IDs are the one
        # supported confirmation axis, not proof of stochastic independence.
        for key in ['artifact','rows','group_by','pair_by','baseline','candidate','rules']:
            if old['spec'][key]!=current['spec'][key]:raise ValueError('确认阶段不能更换候选或放宽原评价规则：'+key)
        if old['protocols']!=current['protocols']:raise ValueError('本确认机制仅支持相同数据/评价协议下的新配对单位')
        search_pairs.update(scalar(v) for v in old['spec']['pairs'])
        search_runs.update(report['run_ids']);artifact_hashes.update(s['artifact_sha256'] for s in report['sources'])
        old_refs={r['path']:r for r in json.loads(old_plan['evidence'])}
        for name in config['candidate_files']:
            ref=old_refs.get(name)
            if not ref or name in search_files and search_files[name]!=ref:raise ValueError('所选候选在搜索材料中的身份不一致')
            search_files[name]=ref
        reports.append({'id':cid,'plan_id':report['plan_id'],'contract':report['contract']})
    local_refs={r['run_id'] for r in store.rows('SELECT run_id FROM research_record_runs WHERE record_id=?',(choice['id'],))}
    cloud_refs=set(json.loads(choice['source']).get('cloud_run_ids',[]))
    from .rounds import CLOUD_TERMINAL
    for search_plan in {r['plan_id'] for r in reports}:
        attempts=store.rows('SELECT id,state FROM research_runs WHERE plan_id=?',(search_plan,))
        if any(r['state'] in {'requested','running','unknown'} for r in attempts):raise ValueError('搜索尚有在途/未知尝试，不能登记已结束选择的确认')
        if {r['id'] for r in attempts}-local_refs:raise ValueError('选择记录须覆盖搜索的全部实际尝试，包含失败/取消；只需引用索引，不重复加载全部日志')
        if store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
            attempts=store.rows("SELECT id,state FROM company_training WHERE plan_id=? AND state<>'prepared'",(search_plan,))
            if any(r['state'].lower() not in CLOUD_TERMINAL for r in attempts):raise ValueError('搜索云任务尚未对账')
            if {r['id'] for r in attempts}-cloud_refs:raise ValueError('选择记录遗漏实际云尝试')
    if {scalar(v) for v in current['spec']['pairs']} & search_pairs:raise ValueError('独立确认不能复用已用于选择的配对单位')
    identities=files(store,pid,config['candidate_files'],{r['path']:r for r in checked},search_files)
    ref={'selection_record_id':choice['id'],'search_comparisons':reports,'search_run_ids':sorted(search_runs),
         'search_artifact_sha256':sorted(artifact_hashes),'candidate_identity':identities,
         'independence_reason':text(config['independence_reason']),
         'meaning':'核对新pair、候选/规则和运行身份；不认证种子/样本确实独立或统计功效充分。'}
    result=dict(source.get('study_integrity',{}));result.update(revision=REVISION,engine_sha256=ENGINE_SHA256,trust=TRUST,confirmation_plan=ref)
    return {**source,'study_integrity':result}


def confirmation_check(store, pid, plan, reports):
    protocol=payload(plan).get('confirmation_plan')
    reasons=[]
    if not protocol:return {'ready':False,'issues':['未登记事前确认方案'],'meaning':TRUST['meaning']}
    if any(r['plan_id']!=plan['id'] or r.get('prospective') is not True for r in reports):reasons.append('确认结果未来自本事前冻结方案')
    sources=[s for r in reports for s in r['sources'] if s.get('contains_candidate') is True]
    if any('contains_candidate' not in s for r in reports for s in r['sources']):
        reasons.append('旧核验未区分候选产物归属，请复核原归档，无需重训')
    if not sources:reasons.append('没有明确的候选确认产物')
    runs={s['run_id'] for s in sources}
    if runs & set(protocol['search_run_ids']):reasons.append('复用了搜索运行')
    artifacts={s['artifact_sha256'] for s in sources}
    if artifacts & set(protocol['search_artifact_sha256']):reasons.append('复用了搜索指标产物')
    if any(r['state']!='verified' for r in reports):reasons.append('确认材料不可比较')
    # Actual input snapshots must contain the frozen candidate files; a plan
    # declaration alone is not execution evidence.
    for src in sources:
        try:
            if src.get('location')=='cloud':
                from .cloud_results import read
                _,frozen,_,_,_=read(store,pid,src['run_id'])
                refs=frozen['expected']['protocols']+frozen['expected'].get('execution_files',[])
                inputs={v['path']:v for v in refs}
            else:
                manifest=store.evidence.verify('local',src['run_id']);inputs=manifest['frozen_inputs']
            if any(inputs.get(v['path'],{}).get('sha256')!=v['sha256'] for v in protocol['candidate_identity']):
                reasons.append('实际运行没有保持所选候选输入身份')
        except (OSError,ValueError,KeyError):reasons.append('实际确认输入证据不可核对')
    return {'ready':not reasons,'issues':list(dict.fromkeys(reasons)),
            'meaning':'仅通过结构/字节检查，仍需判断统计独立、功效和结论适用范围。'}


def numeric_outcome(reports, confirmation=None):
    """Four states for the *frozen numeric claim*, never global model validity."""
    if not reports:return 'inconclusive'
    if any(r['state']!='verified' for r in reports):return 'invalid'
    if confirmation is not None and not confirmation['ready']:return 'inconclusive'
    if any(type(r.get('rules_passed')) is not bool for r in reports):return 'inconclusive'
    return 'supported' if all(r['rules_passed'] for r in reports) else 'refuted'


def preflight(store, sid, plan, purpose='fit'):
    value=payload(plan)
    if not value:return
    if value.get('engine_sha256')!=ENGINE_SHA256 or not enabled(store,sid):
        raise ValueError('研究完整性规则已变化，先显式升级方法并审阅新方案，不沿用旧规则执行')
    if value.get('confirmation_plan'):
        identities=value['confirmation_plan']['candidate_identity']
        files(store,plan['project_id'],[r['path'] for r in identities],{r['path']:r for r in json.loads(plan['evidence'])},
              {r['path']:r for r in identities})
    if purpose!='fit':return  # Diagnostics can resolve an implementation gap.
    readiness={}
    for row in store.rows("SELECT id,source FROM records WHERE project_id=? AND json_extract(source,'$.study_integrity.study_review') IS NOT NULL ORDER BY rowid",(plan['project_id'],)):
        source=json.loads(row['source'])
        if row['id']==plan['id'] or source.get('plan_id')==plan['id']:
            for item in source['study_integrity']['study_review']['readiness']:readiness[item['phase']]=item
    missing=[item['phase'] for item in readiness.values() if item['status']=='needs_evidence']
    if missing:raise ValueError('已声明的研究前提尚缺证据，先补查或修订：'+','.join(missing))


def assess(store, session, record, checked, source):
    if record['kind'] not in {'research','result','decision','review','delivery'}:raise ValueError('科学判断应关联实际结果/评审')
    value=record['scientific_assessment'];pid=session['project_id']
    required={'claim','basis','level','reason','limitations','evidence'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-set(FIELDS['scientific_assessment']['properties']):raise ValueError('科学判断字段不完整')
    for key in ['claim','reason','limitations']:text(value[key])
    if value['level'] not in {'exploratory','confirmatory','diagnostic'}:raise ValueError('科学判断层级未知')
    if value.get('question_id'):record_ref(store,pid,value['question_id'],{'question'})
    if value.get('supersedes'):
        prior=record_ref(store,pid,value['supersedes'])
        previous=payload(prior).get('scientific_assessment')
        if not previous or (previous.get('question_id') or previous['claim'])!=(value.get('question_id') or value['claim']):raise ValueError('修订须对应同一研究问题，保留原判断')
    paths={v['path'] for v in checked}
    if any(path not in paths for path in ids(value['evidence'])):raise ValueError('结论依据必须同时固定在本记录证据中')
    confirmation=None
    if value['basis']=='comparison_rules':
        if 'outcome' in value:raise ValueError('数值四态由冻结规则计算，不能传入想要的outcome')
        from .comparison import read_report
        reports=[read_report(store,pid,cid) for cid in ids(record.get('comparison_ids',[]),maximum=8,required=True)]
        plan_id=record.get('plan_id')
        if any(r['plan_id']!=plan_id for r in reports):raise ValueError('数值结论须针对本方案的确切比较')
        plan=record_ref(store,pid,plan_id,{'plan'})
        if value['level']=='confirmatory':confirmation=confirmation_check(store,pid,plan,reports)
        outcome=numeric_outcome(reports,confirmation)
        meaning='仅裁定冻结数值条件，未满足不证明模型/机制无效；未认证显著性或通用收益。'
    elif value['basis']=='evidence_review':
        outcome=value.get('outcome')
        if outcome not in OUTCOMES:raise ValueError('证据审议需明确四态结论')
        if value['level']=='confirmatory':raise ValueError('不能用人工自报证据审议冒充已核验的确认性数值结论')
        if outcome in {'supported','refuted'} and not value['evidence']:raise ValueError('支持或反对需实际证据，不能由预算/工程状态推出')
        meaning='有来源的研究判断，非程序认证的科学真理；结论可按新证据追加修订。'
    else:raise ValueError('科学判断依据未知')
    return {**value,'outcome':outcome,'label':OUTCOMES[outcome],'confirmation':confirmation,
            'judged_claim':'候选是否满足冻结的全部数值条件' if value['basis']=='comparison_rules' else value['claim'],
            'stop_reason':source.get('stop_reason'),'meaning':meaning,'authorizes_execution':False}


def describe(record):
    value=payload(record)
    # Existing question-level findings remain the source of truth; no duplicate
    # question lifecycle. This is their common display vocabulary.
    questions=[]
    for item in json.loads(record['source']).get('inquiry_outcomes',[]):
        outcome={'unresolved':'inconclusive','deferred':'inconclusive'}.get(item['outcome'],item['outcome'])
        questions.append({**item,'scientific_outcome':outcome,'label':OUTCOMES[outcome]})
    return {**value,'question_assessments':questions} if value or questions else None


def context(store, sid):
    pid=store.session(sid)['project_id'];latest={};assessments=[]
    rows=store.rows("SELECT id,title,source FROM records WHERE project_id=? AND json_extract(source,'$.study_integrity') IS NOT NULL ORDER BY rowid DESC",(pid,))
    for record in rows:
        value=payload(record);choice=value.get('baseline_selection')
        if choice and choice['scope'] not in latest:latest[choice['scope']]={
            'record_id':record['id'],'scope':choice['scope'][:200],'candidate':choice['candidate'][:200],
            'candidate_record_id':choice['candidate_record_id'],'reason':choice['reason'][:300]}
        if value.get('scientific_assessment') and len(assessments)<5:
            a=value['scientific_assessment']
            assessments.append({'record_id':record['id'],'title':record['title'],'claim':a['claim'][:300],
                                'outcome':a['outcome'],'label':a['label'],'level':a['level']})
    return {'enabled':enabled(store,sid),'revision':REVISION,'baselines':list(latest.values())[:12],
            'recent_assessments':assessments,'trust':TRUST,
            'usage':'需要选择基线、确认性结论或关键审查时使用research_record对应字段；普通探索无需填完整SDM表。基线/结论不授予计算权限。'}


def project(store, graph):
    notes={r['id']:payload(r) for r in store.rows("SELECT id,source FROM records WHERE project_id=? AND json_extract(source,'$.study_integrity') IS NOT NULL",(graph['project_id'],))}
    if not notes:return graph
    nodes=[];edges=list(graph['edges'])
    for original in graph['nodes']:
        node=dict(original);value=notes.get(node['id'],{})
        assessment=value.get('scientific_assessment');selection=value.get('baseline_selection')
        if assessment:
            node['importance']='critical'
            node['scientific_outcome']=assessment['outcome']
            node['summary']=('数值条件：' if assessment['basis']=='comparison_rules' else '证据判断：')+assessment['label']+' · '+node['summary']
            if assessment.get('supersedes'):
                edges.append({'id':'assessment-revision:'+node['id'],'source':assessment['supersedes'],
                              'target':node['id'],'kind':'claim_revision','primary':False,'story_visible':True,
                              'basis':'explicit_scientific_assessment_revision'})
        if selection:
            node['importance']='critical'
            node['summary']='基线选择：'+selection['candidate']+' · '+node['summary']
            if selection['previous_selection_id']:
                edges.append({'id':'baseline-update:'+node['id'],'source':selection['previous_selection_id'],
                              'target':node['id'],'kind':'baseline_update','primary':False,'story_visible':True,
                              'basis':'explicit_reference_replacement_not_experiment_causality'})
        if value.get('baseline_ref'):
            edges.append({'id':'baseline-reference:'+node['id'],'source':value['baseline_ref'],
                          'target':node['id'],'kind':'uses','primary':False,'basis':'exact_frozen_reference_selection'})
        nodes.append(node)
    return {**graph,'nodes':nodes,'edges':edges}
