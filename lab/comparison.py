"""Deterministic, evidence-backed numeric checks; never chooses the next model."""
import hashlib
import json
from decimal import Decimal,localcontext
from pathlib import Path

from .store import dumps,now,uid
from . import evaluation_contract as contract

EVALUATOR_VERSION='numeric-check-v3'


def cell(value):
    return str(value).replace('|','\\|').replace('\r',' ').replace('\n',' ')


def decimal_text(value):
    return str(value.normalize()) if value is not None else None


def evaluate(spec,rows):
    """Pure arithmetic; callers separately prove source and frozen protocol."""
    contract.validate(spec)
    expected={contract.scalar(pair):pair for pair in spec['pairs']}
    groups={contract.scalar(spec['candidate']):'candidate'}
    if spec['baseline'] is not None:groups[contract.scalar(spec['baseline'])]='baseline'
    values={'baseline':{},'candidate':{}};ignored=0
    for row in rows:
        group=groups.get(contract.scalar(contract.pointer(row,spec['group_by'])))
        if not group:ignored+=1;continue
        pair=contract.scalar(contract.pointer(row,spec['pair_by']))
        if pair not in expected:raise ValueError('出现未事先声明的配对值，不能选择性扩大样本')
        if pair in values[group]:raise ValueError('同一组别/配对出现重复结果，不能择优')
        values[group][pair]={rule['key']:contract.number(contract.pointer(row,rule['value'])) for rule in spec['rules']}
        for rule in spec['rules']:
            if 'bounds' in rule and not contract.number(rule['bounds'][0])<=values[group][pair][rule['key']]<=contract.number(rule['bounds'][1]):raise ValueError('指标超出固定范围/单位：'+rule['label'])
    for group in groups.values():
        if set(values[group])!=set(expected):raise ValueError('配对结果不全，不能只报告有利种子/评估')
    checks=[]
    with localcontext() as ctx:
        ctx.prec=40
        for rule in spec['rules']:
            pairs=list(expected)
            candidate=[values['candidate'][pair][rule['key']] for pair in pairs]
            baseline=[values['baseline'][pair][rule['key']] for pair in pairs] if spec['baseline'] is not None else None
            cand_mean=sum(candidate,Decimal(0))/len(candidate)
            base_mean=sum(baseline,Decimal(0))/len(baseline) if baseline is not None else None
            direction=1 if rule['direction']=='maximize' else -1;tolerance=contract.number(rule.get('tolerance',0))
            def passed(base,cand):
                if rule['comparison']=='target':return direction*(cand-contract.number(rule['target']))>=0
                delta=direction*(cand-base)
                return delta>tolerance if rule['comparison']=='improve' else delta>=-tolerance
            decisions=[passed(base_mean,cand_mean)] if rule['aggregation']=='mean' else [passed(baseline[i] if baseline is not None else None,value) for i,value in enumerate(candidate)]
            checks.append({'key':rule['key'],'label':rule['label'],'comparison':rule['comparison'],
                'aggregation':rule['aggregation'],'passed':all(decisions),'baseline_mean':decimal_text(base_mean),
                'candidate_mean':decimal_text(cand_mean),'delta_mean':decimal_text(cand_mean-base_mean) if baseline is not None else None,
                'failed_pairs':[] if rule['aggregation']=='mean' else [expected[pairs[i]] for i,ok in enumerate(decisions) if not ok],
                'mean_condition_failed':rule['aggregation']=='mean' and not decisions[0],
                'pair_observations':[{'pair':expected[pair],'baseline':decimal_text(baseline[i]) if baseline is not None else None,
                    'candidate':decimal_text(candidate[i]),'delta':decimal_text(candidate[i]-baseline[i]) if baseline is not None else None,
                    'passed':decisions[i] if rule['aggregation']=='each_pair' else None} for i,pair in enumerate(pairs)]})
    return {'rules_passed':all(check['passed'] for check in checks),
        'targets_met':all(check['passed'] for check in checks) if any(c['comparison']=='target' for c in checks) else None,
        'checks':checks,'ignored_other_groups':ignored,'pair_count':len(expected)}


def read_report(store,project_id,id):
    row=store.one('SELECT * FROM research_comparisons WHERE id=? AND project_id=?',(id,project_id)) if store.one("SELECT name FROM sqlite_master WHERE name='research_comparisons'") else None
    if not row:raise ValueError('数值核验记录不属于当前项目')
    result=json.loads(row['result'])
    for field,hash_field in [('json_path','json_digest'),('markdown_path','markdown_digest')]:
        path=store.safe_path(project_id,row[field])
        if not path.is_file() or path.is_symlink() or path.stat().st_size>512*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=row[hash_field]:raise ValueError('数值核验报告已改变，不能引用伪造结论')
        if field=='json_path' and contract.json_text(path.read_text(encoding='utf-8'))!=result:raise ValueError('核验报告与原保存结果不一致')
    plan=store.one('SELECT * FROM records WHERE id=? AND project_id=?',(row['plan_id'],project_id))
    value=contract.load(store,plan) if plan else None
    if not value or value['reference']['sha256']!=row['contract_digest']:raise ValueError('数值核验的原评价合同不再可核对')
    for source in result['sources']:
        if source.get('location')=='cloud':
            from .cloud_results import read
            _,_,_,_,archive=read(store,project_id,source['run_id'])
            if archive['manifest_sha256']!=source['manifest_digest']:raise ValueError('原云结果归档来源已改变')
            continue
        evidence=store.one("SELECT manifest_digest FROM run_evidence WHERE location='local' AND run_id=? AND project_id=?",(source['run_id'],project_id))
        if not evidence or evidence['manifest_digest']!=source['manifest_digest']:raise ValueError('原结果归档来源已改变')
        store.evidence.verify('local',source['run_id'])
    return result


class Comparison:
    def __init__(self,research):
        self.research=research;self.store=research.store
        with self.store.lock,self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_comparisons(
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),session_id TEXT NOT NULL,
              plan_id TEXT NOT NULL REFERENCES records(id),contract_digest TEXT NOT NULL,run_ids TEXT NOT NULL,
              result TEXT NOT NULL,json_path TEXT NOT NULL,json_digest TEXT NOT NULL,
              markdown_path TEXT NOT NULL,markdown_digest TEXT NOT NULL,created_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(16,datetime('now'));
            ''')

    def _approval(self,plan):
        from .research import plan_digest
        exact=plan_digest(plan)
        review=self.store.one('SELECT * FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
        if review and review['decision']=='reject':raise ValueError('该具体方案已被退回，不能认证其评价条件')
        if review and review['decision']=='accept' and review['plan_digest']==exact:return review['created_at']
        row=self.store.one('SELECT * FROM research_plan_admissions WHERE plan_id=?',(plan['id'],))
        if row and row['plan_digest']==exact:return row['created_at']
        raise ValueError('评价合同尚未随具体方案通过审核/正式采纳；不按临时口径宣称通过')

    def read(self,project_id,id):
        return read_report(self.store,project_id,id)

    def index(self,pid):
        result=[]
        for row in self.store.rows('SELECT id,plan_id,result,markdown_path FROM research_comparisons WHERE project_id=? ORDER BY rowid DESC LIMIT 5',(pid,)):
            value=json.loads(row['result'])
            result.append({'id':row['id'],'plan_id':row['plan_id'],'report_path':row['markdown_path'],**{key:value[key] for key in ['state','rules_passed','targets_met','prospective']}})
        return result

    def compare(self,sid,plan_id,run_ids,collect_missing=False):
        plan=self.research.plan(sid,plan_id);pid=plan['project_id'];value=contract.load(self.store,plan)
        if not value:return {'state':'unconfigured','rules_passed':None,'meaning':'原方案没有固定机器可读评价合同，不推测其目标，也不改写旧记录'}
        approved_at=self._approval(plan);spec=value['spec'];ref=value['reference']
        if type(collect_missing) is not bool or not isinstance(run_ids,list) or not 1<=len(run_ids)<=20 or any(not isinstance(id,str) for id in run_ids) or len(set(run_ids))!=len(run_ids):raise ValueError('明确引用1—20个不重复的原运行')
        rows=[];sources=[];issues=[];prospective=True
        for id in sorted(run_ids):
            run=self.store.one('SELECT * FROM research_runs WHERE id=? AND project_id=?',(id,pid))
            cloud=None
            if not run:
                if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
                    cloud=self.store.one('SELECT * FROM company_training WHERE id=? AND project_id=?',(id,pid))
                if not cloud:raise ValueError('运行不属于当前项目')
            try:
                if cloud:
                    from .cloud_results import read
                    run,frozen,raw,root,archive=read(self.store,pid,id)
                    refs=frozen['expected']['protocols']+([frozen['expected']['evaluation_contract']] if frozen['expected']['evaluation_contract'] else [])
                    manifest={'frozen_inputs':{r['path']:r for r in refs},'files':[dict(e,role='output') for e in raw['files']]}
                    summary={'manifest_path':archive['manifest_path']};manifest_digest=archive['manifest_sha256']
                else:
                    if run['state']!='completed':raise ValueError('非成功运行不能参加数值比较；仍需在本轮评审中分析')
                    summary=self.store.evidence.summary('local',id)
                    if not summary and collect_missing:
                        self.store.evidence.collect_local(run['session_id'],id);summary=self.store.evidence.summary('local',id)
                    if not summary or not summary['analysis_ready'] or summary['analysis_scope']!='result_analysis':raise ValueError('缺少可核对的本地结果归档；明确回收后再核验')
                    manifest=self.store.evidence.verify('local',id)
                    root=self.store.safe_path(pid,'research-evidence/local/'+id)
                    manifest_digest=self.store.one("SELECT manifest_digest FROM run_evidence WHERE location='local' AND run_id=?",(id,))['manifest_digest']
                for protocol in value['protocols']:
                    if manifest['frozen_inputs'].get(protocol['path'],{}).get('sha256')!=protocol['sha256']:raise ValueError('数据划分/评价协议输入未保持相同：'+protocol['path'])
                entries=[entry for entry in manifest['files'] if entry['role']=='output' and entry['name']==spec['artifact']]
                if len(entries)!=1:raise ValueError('原运行没有声明并归档所需指标文件')
                entry=entries[0];path=root/entry['object']
                if entry.get('partial'):raise ValueError('指标文件不完整，不能比较日志尾部')
                if entry['size']>2*1024*1024:raise ValueError('指标摘要超过2MiB；不加载全部预测/日志作为上下文')
                data=contract.json_text(path.read_text(encoding='utf-8'));selected=contract.pointer(data,spec['rows'])
                selected=[selected] if isinstance(selected,dict) else selected
                if not isinstance(selected,list) or len(selected)>400 or any(not isinstance(row,dict) for row in selected):raise ValueError('指标结果需为有界对象行，未猜测文件方言')
                candidate_present=any(contract.scalar(contract.pointer(row,spec['group_by']))==contract.scalar(spec['candidate']) for row in selected)
                bound=manifest['frozen_inputs'].get(ref['path'],{}).get('sha256')==ref['sha256']
                if candidate_present and (run['created_at']<approved_at or not bound):prospective=False
                rows.extend(selected)
                sources.append({'run_id':id,'session_id':run['session_id'],'manifest_path':summary['manifest_path'],
                    'contains_candidate':candidate_present,
                    'manifest_digest':manifest_digest,
                    'artifact':entry['name'],'artifact_sha256':entry['sha256'],'contract_in_execution_snapshot':bound})
                if cloud:sources[-1]['location']='cloud';sources[-1]['task_uid']=run['task_uid']
            except (ValueError,OSError,KeyError,TypeError,IndexError) as exc:issues.append({'run_id':id,'reason':str(exc)[:300]})
        if len(rows)>400:issues.append({'reason':'合计指标行超过400，需明确缩小比较范围'})
        checked={'rules_passed':None,'targets_met':None,'checks':[]}
        if not issues:
            try:checked=evaluate(spec,rows)
            except (ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:issues.append({'reason':str(exc)[:300]})
        payload={'version':2,'evaluator_version':EVALUATOR_VERSION,'plan_id':plan_id,'plan_version':plan['version'],'contract':ref,'run_ids':sorted(run_ids),
            'state':'verified' if not issues else 'not_comparable','prospective':prospective if not issues else None,
            **checked,'issues':issues,'sources':sources,'authorizes_execution':False,
            'meaning':'仅核对归档数值、固定协议字节和声明条件；不认证评价代码正确、统计显著性或工业泛化。不改变授权/预算。'}
        request_key={'plan':plan_id,'contract':ref['sha256'],'runs':sorted(run_ids),'sources':sources,'issues':issues,'evaluator_version':EVALUATOR_VERSION}
        id=hashlib.sha256(dumps(request_key).encode()).hexdigest()
        if self.store.one('SELECT id FROM research_comparisons WHERE id=?',(id,)):
            prior=self.read(pid,id)
            if prior!=payload:raise ValueError('同一来源的数值核验结果不一致，未覆盖历史')
        else:
            directory='research-comparisons/'+id[:24];json_path=directory+'/report.json';markdown_path=directory+'/report.md'
            text='# 数值评价核验\n\n'+('材料不可比较，暂不能判断候选是否改进。' if issues else '候选满足已声明的全部数值条件。' if checked['rules_passed'] else '候选未满足已声明的全部数值条件，不能只挑上涨的指标。')
            text+='\n\n| 条件 | 对照均值 | 候选均值 | 变化 | 判定 |\n|---|---|---|---|---|'
            for check in checked['checks']:
                text+='\n| '+check['label']+' | '+str(check['baseline_mean'] or '—')+' | '+check['candidate_mean']+' | '+str(check['delta_mean'] or '—')+' | '+('通过' if check['passed'] else '未通过')+' |'
            if checked.get('ignored_other_groups'):text+='\n\n另有'+str(checked['ignored_other_groups'])+'行属于其他组别；未纳入本次预定对照与候选比较。'
            if checked['checks']:
                text+='\n\n## 逐对观测\n\n均值条件按均值判定；逐对数值用于检查变化是否一致，不将均值通过写成每对均通过。'
                text+='\n\n| 指标 | 配对 | 对照 | 候选 | 候选−对照 | 逐对判定 |\n|---|---|---|---|---|---|'
                for check in checked['checks']:
                    for observation in check['pair_observations']:
                        verdict='按均值判断' if observation['passed'] is None else '通过' if observation['passed'] else '未通过'
                        text+='\n| '+' | '.join(cell(v) for v in [check['label'],observation['pair'],observation['baseline'] if observation['baseline'] is not None else '—',observation['candidate'],observation['delta'] if observation['delta'] is not None else '—',verdict])+' |'
            if not prospective:text+='\n\n候选没有在本评价合同冻结后的执行快照中产生：仅作事后复算，不冒充预先约定的独立验证。'
            for issue in issues:text+='\n\n待补证：'+issue['reason']
            text+='\n\n'+payload['meaning']+'\n'
            bodies=[(json_path,json.dumps(payload,ensure_ascii=False,indent=2).encode()),(markdown_path,text.encode())]
            for name,body in bodies:
                path=self.store.safe_path(pid,name);path.parent.mkdir(parents=True,exist_ok=True)
                if path.exists():
                    if path.read_bytes()!=body:raise ValueError('原数值核验报告已改变，未覆盖')
                else:
                    temporary=path.with_name(uid()+'.tmp')
                    try:temporary.write_bytes(body);temporary.replace(path)
                    finally:
                        if temporary.exists():temporary.unlink()
            self.store.execute('INSERT OR IGNORE INTO research_comparisons VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (id,pid,sid,plan_id,ref['sha256'],dumps(sorted(run_ids)),dumps(payload),json_path,hashlib.sha256(bodies[0][1]).hexdigest(),
                 markdown_path,hashlib.sha256(bodies[1][1]).hexdigest(),now()))
            self.store.event(sid,'research.compared',{'id':id,'plan_id':plan_id,'state':payload['state'],'rules_passed':payload['rules_passed'],'report':markdown_path})
        report=self.store.one('SELECT markdown_path FROM research_comparisons WHERE id=?',(id,))
        return {key:payload[key] for key in ['state','rules_passed','targets_met','prospective','issues','authorizes_execution','meaning']}|{
            'comparison_id':id,'report_path':report['markdown_path'],
            'failed_rules':[{'key':check['key'],'label':check['label'],'failed_pairs':check['failed_pairs']} for check in checked['checks'] if not check['passed']]}
