"""Numeric checks over real fixture archives. No model or scientific training."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
import uuid

from lab.store import Store,dumps,now
from lab.research import Research,plan_digest
from lab import evaluation_contract as contract
from lab.comparison import evaluate
from lab.kernel import Kernel
from tests.test_core import FakeRPC

ROOT=Path(__file__).resolve().parents[1]


def specification(target=False):
    rules=[{'key':'f1','label':'验证F1','value':'/f1','direction':'maximize','aggregation':'each_pair','comparison':'improve','bounds':[0,1]},
           {'key':'recall','label':'关键类别召回','value':'/recall','direction':'maximize','aggregation':'mean','comparison':'noninferior','bounds':[0,1]},
           {'key':'loss','label':'验证损失','value':'/loss','direction':'minimize','aggregation':'mean','comparison':'noninferior','bounds':[0,100]}]
    if target:rules.append({'key':'target','label':'目标F1','value':'/f1','direction':'maximize','aggregation':'each_pair','comparison':'target','target':.9,'bounds':[0,1]})
    return {'schema':1,'title':'事先固定配对条件','artifact':'metrics.json','rows':'/results','group_by':'/variant','pair_by':'/seed',
            'baseline':'baseline','candidate':'candidate','pairs':[42,7],'protocol_files':['protocol.json'],'rules':rules}


def observations(good=False):
    return [{'variant':variant,'seed':seed,'f1':(.91 if good else .81) if variant=='candidate' else .8,
             'recall':.91 if variant=='baseline' or good else .89,'loss':.4 if variant=='candidate' else .5}
            for seed in [42,7] for variant in ['baseline','candidate']]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex);self.research=Research(self.store)
        self.pid=self.store.create_project('numeric-check-fixture')['id'];self.sid=self.store.create_session(self.pid,'ept')['id']
        self.workspace=Path(self.store.session(self.sid)['workspace'])
        (self.workspace/'protocol.json').write_text('{"split":"fixed-validation","threshold":0.5}',encoding='utf-8')
        self.spec=specification();self.plan=self.make_plan(self.spec)

    def tearDown(self):self.research.close();self.store.close()

    def make_plan(self,spec,**extra):
        return self.store.add_record(self.sid,{'kind':'plan','title':'固定数值条件的方案','content':'先解释科学假设，条件由程序附在正文供审核。',
            'evidence':['protocol.json'],'evaluation_contract':spec,**extra},{'origin':'isolated-fixture'})

    def approve(self,plan=None,autonomous=False):
        plan=plan or self.plan
        scope={'objective':'隔离数值核验','evaluation':'固定方案条件','data_boundary':'无真实数据','allowed_changes':'不训练，仅固定样本',
               'frozen_files':[ref['path'] for ref in json.loads(plan['evidence'])]}
        return self.research.review(self.sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept','reason':'工程测试操作者核对规则',
            'allow_local':True,'python':sys.executable,'max_runs':10,'max_fits':5,'timeout':3,'autonomous':autonomous,'research_scope':scope})

    def make_run(self,rows=None,compiled=True,protocol_body=None,raw=None):
        id=uuid.uuid4().hex;directory=self.workspace/'runs'/id;directory.mkdir(parents=True)
        value=contract.load(self.store,self.plan);inputs={}
        paths=['protocol.json']+([value['reference']['path']] if compiled else [])
        for name in paths:
            body=(self.workspace/name).read_bytes()
            if name=='protocol.json' and protocol_body is not None:body=protocol_body
            target=directory/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
            inputs[name]={'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
        (directory/'input-manifest.json').write_text(dumps(inputs),encoding='utf-8')
        body=raw if raw is not None else json.dumps({'results':observations() if rows is None else rows}).encode()
        (directory/'metrics.json').write_bytes(body)
        for name in ['stdout.log','stderr.log']:(directory/name).write_text('',encoding='utf-8')
        request={'plan_id':self.plan['id'],'purpose':'evaluation','outputs':['metrics.json']}
        receipt={'run_id':id,'plan_id':self.plan['id'],'plan_version':self.plan['version'],'state':'completed','inputs':inputs,
            'outputs':[{'path':'runs/'+id+'/metrics.json','sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}],
            'directory':'runs/'+id,'error':None,'exit_code':0,'trial_id':None,'missing_outputs':[]}
        (directory/'receipt.json').write_text(dumps(receipt),encoding='utf-8')
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (id,'fixture:'+id,self.pid,self.sid,self.plan['id'],'fixture-grant',hashlib.sha256(dumps(request).encode()).hexdigest(),dumps(request),'completed','runs/'+id,dumps(receipt),now(),now()))
        self.store.execute('INSERT INTO research_run_context VALUES(?,?,?)',(id,None,now()))
        self.store.evidence.collect_local(self.sid,id)
        return id

    def test_score_gain_does_not_hide_failed_recall_and_report_is_reused(self):
        self.approve();id=self.make_run();before=self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt']
        result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.assertEqual(result['state'],'verified');self.assertFalse(result['rules_passed'])
        self.assertEqual([r['key'] for r in result['failed_rules']],['recall'])
        self.assertTrue(result['prospective']);self.assertFalse(result['authorizes_execution'])
        self.assertIn('未满足',(self.workspace/result['report_path']).read_text(encoding='utf-8'))
        self.assertEqual(self.research.comparison.compare(self.sid,self.plan['id'],[id]),result)
        self.assertEqual(self.store.one('SELECT count(*) n FROM research_comparisons')['n'],1)
        self.assertEqual(self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt'],before)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_pair_observations_preserve_negative_evidence_without_changing_mean_rule(self):
        rows=observations(good=True)
        for row in rows:
            if row['variant']=='candidate':row['recall']=.9 if row['seed']==42 else .92
        result=evaluate(self.spec,rows)
        recall=next(c for c in result['checks'] if c['key']=='recall')
        self.assertTrue(recall['passed']);self.assertEqual(recall['delta_mean'],'0')
        self.assertEqual([r['delta'] for r in recall['pair_observations']],['-0.01','0.01'])
        self.assertTrue(all(r['passed'] is None for r in recall['pair_observations']))
        self.approve();id=self.make_run(rows);compared=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        report=(self.workspace/compared['report_path']).read_text(encoding='utf-8')
        self.assertIn('逐对观测',report);self.assertIn('-0.01',report);self.assertIn('按均值判断',report)
        report=self.research.comparison.read(self.pid,compared['comparison_id'])
        self.assertEqual(report['evaluator_version'],'numeric-check-v3')
        self.assertTrue(report['sources'][0]['contains_candidate'])

    def test_protocol_change_and_unpaired_results_are_not_algorithm_failures(self):
        self.approve()
        for args in [{'protocol_body':b'{"split":"changed"}'},{'rows':observations()[:-1]},{'rows':observations()+observations()[:1]}]:
            with self.subTest(args=list(args)):
                id=self.make_run(**args);result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
                self.assertEqual(result['state'],'not_comparable');self.assertIsNone(result['rules_passed'])

    def test_non_finite_boolean_duplicate_json_and_bad_units_are_rejected(self):
        self.approve()
        for metric in [float('nan'),float('inf'),True,1.5,'0.99']:
            rows=observations();rows[1]['f1']=metric
            result=self.research.comparison.compare(self.sid,self.plan['id'],[self.make_run(rows)])
            self.assertEqual(result['state'],'not_comparable',metric)
        raw=b'{"results":[],"results":[]}'
        result=self.research.comparison.compare(self.sid,self.plan['id'],[self.make_run(raw=raw)])
        self.assertEqual(result['state'],'not_comparable')

    def test_contract_must_be_approved_and_original_bytes_preserved(self):
        id=self.make_run()
        with self.assertRaisesRegex(ValueError,'审核'):self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.approve();ref=json.loads(self.plan['source'])['evaluation_contract']
        (self.workspace/ref['path']).write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.research.comparison.compare(self.sid,self.plan['id'],[id])

    def test_numeric_goal_requires_prospective_report_not_posthoc_or_relative_gain(self):
        self.plan=self.make_plan(specification(target=True));self.approve()
        late=self.make_run(observations(good=True),compiled=False)
        result=self.research.comparison.compare(self.sid,self.plan['id'],[late])
        self.assertTrue(result['targets_met']);self.assertFalse(result['prospective'])
        with self.assertRaisesRegex(ValueError,'事先'):
            self.store.add_record(self.sid,{'kind':'review','title':'不能这样结束','content':'只检查门禁，不代表真实训练。',
                'plan_id':self.plan['id'],'run_ids':[late],'comparison_ids':[result['comparison_id']],
                'round_outcome':'stop_requested','stop_reason':'target_achieved'}, {})
        current=self.make_run(observations(good=True));good=self.research.comparison.compare(self.sid,self.plan['id'],[current])
        review=self.store.add_record(self.sid,{'kind':'review','title':'固定数值条件满足的工程例','content':'不声称真实科学目标成功。',
            'plan_id':self.plan['id'],'run_ids':[late,current],'comparison_ids':[good['comparison_id']],
            'round_outcome':'stop_requested','stop_reason':'target_achieved'}, {})
        self.assertEqual(json.loads(review['source'])['stop_reason'],'target_achieved')

    def test_contract_without_target_cannot_be_used_as_goal_achieved(self):
        self.approve();id=self.make_run(observations(good=True));result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.assertTrue(result['rules_passed']);self.assertIsNone(result['targets_met'])
        with self.assertRaises(ValueError):self.store.add_record(self.sid,{'kind':'review','title':'相对改进不是目标','content':'fixture','plan_id':self.plan['id'],
            'run_ids':[id],'comparison_ids':[result['comparison_id']],'round_outcome':'stop_requested','stop_reason':'target_achieved'}, {})
        review=self.store.add_record(self.sid,{'kind':'review','title':'预算结束','content':'fixture预算退出不是成功','plan_id':self.plan['id'],
            'run_ids':[id],'round_outcome':'stop_requested','stop_reason':'budget'}, {})
        self.assertEqual(json.loads(review['source'])['stop_reason'],'budget')

    def test_successor_inherits_contract_and_autonomy_cannot_change_or_remove_it(self):
        self.approve(autonomous=True)
        review=self.store.add_record(self.sid,{'kind':'review','title':'形成后继','content':'fixture边界澄清，不虚构实验','plan_id':self.plan['id'],'run_ids':[]}, {})
        scope=self.research.autonomy.status(self.sid)
        common={'previous_plan_id':self.plan['id'],'review_record_id':review['id'],'authority_id':scope['authority_id'],'scope_digest':scope['scope_digest']}
        inherited=self.store.add_record(self.sid,{'kind':'plan','title':'继承条件','content':'保持评价只改方法','evidence':[],**common}, {})
        self.assertEqual(json.loads(inherited['source'])['evaluation_contract'],json.loads(self.plan['source'])['evaluation_contract'])
        changed=copy.deepcopy(self.spec);changed['rules'][1]['tolerance']=.1
        candidate=self.make_plan(changed,**common)
        with self.assertRaisesRegex(ValueError,'重新审核'):self.research.autonomy.adopt(self.sid,candidate['id'],plan_digest(candidate),'不能放宽召回')
        removed=self.make_plan(None,**common)
        with self.assertRaisesRegex(ValueError,'重新审核'):self.research.autonomy.adopt(self.sid,removed['id'],plan_digest(removed),'不能去掉条件')
        self.assertTrue(self.research.autonomy.adopt(self.sid,inherited['id'],plan_digest(inherited),'条件不变')['adopted'])

    def test_same_contract_key_order_is_not_a_new_protocol(self):
        reordered=dict(reversed(list(self.spec.items())))
        other=self.make_plan(reordered)
        self.assertEqual(json.loads(other['source'])['evaluation_contract'],json.loads(self.plan['source'])['evaluation_contract'])
        self.assertIn('关键类别召回',other['content']);self.assertNotIn('JSON Pointer',other['content'])

    def test_trial_links_real_assessment_and_cannot_cite_foreign_report(self):
        self.approve();id=self.make_run();result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        decision=self.store.add_record(self.sid,{'kind':'decision','title':'优先查新增错误','content':'分数改善与类别损失需要区分，先看已有预测。',
            'plan_id':self.plan['id'],'run_ids':[id],'comparison_ids':[result['comparison_id']]},{})
        trial=self.store.add_record(self.sid,{'kind':'trial','title':'不训练的错误诊断','content':'用已有预测区分边界偏移与远阈值错误。',
            'plan_id':self.plan['id'],'basis_record_id':decision['id'],'comparison_ids':[result['comparison_id']]},{})
        self.assertEqual(json.loads(trial['source'])['basis_record_id'],decision['id'])
        self.assertIn(result['report_path'],[ref['path'] for ref in json.loads(trial['evidence'])])
        with self.assertRaises(ValueError):self.store.add_record(self.sid,{'kind':'trial','title':'伪依据','content':'fixture','basis_record_id':'unknown'}, {})
        other=self.store.create_project('other')['id'];sid=self.store.create_session(other,'ept')['id']
        with self.assertRaises(ValueError):self.store.add_record(sid,{'kind':'decision','title':'跨项目','content':'fixture','comparison_ids':[result['comparison_id']]},{})

    def test_report_tamper_and_source_archive_tamper_are_not_trusted(self):
        self.approve();id=self.make_run();result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        report=self.workspace/result['report_path'];body=report.read_bytes();report.write_text('fake success')
        with self.assertRaises(ValueError):self.research.comparison.read(self.pid,result['comparison_id'])
        report.write_bytes(body)
        manifest=self.store.evidence.verify('local',id);entry=next(e for e in manifest['files'] if e['name']=='metrics.json')
        (self.workspace/'research-evidence/local'/id/entry['object']).write_text('fake')
        with self.assertRaises(ValueError):self.research.comparison.read(self.pid,result['comparison_id'])

    def test_no_baseline_for_target_only_and_decimal_equality(self):
        spec=specification(target=True);spec['baseline']=None;spec['rules']=[spec['rules'][-1]]
        spec['rules'][0]['target']=.9
        rows=[row for row in observations(good=True) if row['variant']=='candidate']
        rows[0]['f1']=.9;rows[1]['f1']=.9
        result=evaluate(spec,rows);self.assertTrue(result['targets_met'])
        spec['rules'][0]['target']=.900001;self.assertFalse(evaluate(spec,rows)['targets_met'])
        spec['rules'][0]['tolerance']=.01
        with self.assertRaises(ValueError):evaluate(spec,rows)

    def test_actual_native_tool_and_execution_freeze_contract_without_second_model(self):
        # This standard-library process checks engineering integration, not training.
        self.approve()
        script="import json\njson.dump("+repr({'results':observations()})+",open('metrics.json','w'))\n"
        (self.workspace/'evaluate.py').write_text(script,encoding='utf-8')
        result=self.research.run(self.sid,{'plan_id':self.plan['id'],'script':'evaluate.py','files':['evaluate.py'],
            'args':[],'purpose':'evaluation','question':'固定评价条件是否进入确切执行快照',
            'expected_information':'仅验证程序衔接，不验证训练质量','outputs':['metrics.json']},'comparison-native-integration')
        self.assertEqual(result['state'],'completed',result)
        evaluation=contract.load(self.store,self.plan)
        self.assertIn(evaluation['reference']['path'],result['inputs']);self.assertIn('protocol.json',result['inputs'])
        # A known native callback invokes the deterministic host service directly.
        kernel=Kernel(self.store,FakeRPC)
        try:
            kernel.load_thread(self.sid)
            before=len(kernel.rpc.calls)
            kernel.on_request({'id':900,'method':'item/tool/call','params':{
                'threadId':'native-thread','callId':'numeric-check','tool':'research_compare',
                'arguments':{'plan_id':self.plan['id'],'run_ids':[result['run_id']]}}},kernel.rpc)
            reply=kernel.rpc.replies[-1][1];self.assertTrue(reply['success'],reply)
            checked=json.loads(reply['contentItems'][0]['text'])
            self.assertEqual(checked['state'],'verified');self.assertFalse(checked['rules_passed'])
            self.assertEqual(len(kernel.rpc.calls),before)
            self.assertEqual(self.store.one('SELECT COUNT(*) n FROM research_runs')['n'],1)
            self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        finally:kernel.close()

    def test_contract_cannot_be_omitted_from_original_autonomous_scope(self):
        with self.assertRaisesRegex(ValueError,'评价合同'):
            self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
                'decision':'accept','reason':'scope omission fixture','allow_local':True,'python':sys.executable,
                'max_runs':2,'max_fits':1,'timeout':3,'autonomous':True,'research_scope':{
                    'objective':'fixture','evaluation':'fixture','data_boundary':'fixture','allowed_changes':'fixture',
                    'frozen_files':['protocol.json']}})
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),[])
        self.assertEqual(self.store.rows('SELECT * FROM research_reviews'),[])

    def test_empty_summary_is_diagnostic_only_and_cannot_certify_metrics(self):
        self.approve();id=self.make_run(raw=b'')
        result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.assertEqual(result['state'],'not_comparable');self.assertIsNone(result['rules_passed'])
        self.assertEqual(self.store.evidence.summary('local',id)['analysis_scope'],'failure_diagnosis')

    def test_foreign_run_and_oversized_metric_summary_do_not_certify_success(self):
        self.approve()
        with self.assertRaisesRegex(ValueError,'当前项目'):
            self.research.comparison.compare(self.sid,self.plan['id'],['foreign'])
        id=self.make_run(raw=b' '*(2*1024*1024+1))
        result=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.assertEqual(result['state'],'not_comparable');self.assertIsNone(result['targets_met'])
        self.assertIn('2MiB',str(result['issues']))

    def test_old_plans_and_unarchived_runs_require_no_silent_migration(self):
        old=self.store.add_record(self.sid,{'kind':'plan','title':'旧版无合同','content':'保持原记录'}, {})
        before=old['content'];result=self.research.comparison.compare(self.sid,old['id'],['not-read'])
        self.assertEqual(result['state'],'unconfigured');self.assertEqual(self.store.one('SELECT content FROM records WHERE id=?',(old['id'],))['content'],before)
        self.approve();id=self.make_run();receipt=self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt']
        self.store.execute('DELETE FROM run_evidence WHERE run_id=?',(id,))
        result=self.research.comparison.compare(self.sid,self.plan['id'],[id]);self.assertEqual(result['state'],'not_comparable')
        self.assertIsNone(self.store.evidence.summary('local',id))
        result=self.research.comparison.compare(self.sid,self.plan['id'],[id],collect_missing=True)
        self.assertEqual(result['state'],'verified');self.assertEqual(self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt'],receipt)


if __name__=='__main__':unittest.main()
