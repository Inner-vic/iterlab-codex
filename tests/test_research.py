import json
import os
from pathlib import Path
import sys
import unittest
import uuid

from lab.store import Store
from lab.research import Research,plan_digest
from lab.sdm import SDM

ROOT=Path(__file__).resolve().parents[1]

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        self.project=self.store.create_project('research-fixture')
        self.session=self.store.create_session(self.project['id'],'ept')
        self.sid=self.session['id'];self.workspace=Path(self.project['workspace'])
        self.research=Research(self.store)
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'具体方案','content':'固定数据与评价，实验用于区分解释；CPU短验收'}, {})
        (self.workspace/'train.py').write_text("import json;json.dump({'count':2},open('metrics.json','w'))",encoding='utf-8')
        self.request={'plan_id':self.plan['id'],'script':'train.py','files':['train.py'],'args':[],
            'purpose':'fit','question':'输入是否可执行','expected_information':'只验证受控运行边界，不验证研究质量','outputs':['metrics.json']}
    def tearDown(self):
        self.research.close();self.store.close()
    def approve(self,**extra):
        return self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'accept','reason':'验收操作者检查具体短CPU范围','actor':'用户委托开发助手审批',
            'authority':'本轮用户授权的有界研究验收','execution':'local','allow_local':True,'python':sys.executable,
            'max_runs':2,'max_fits':1,'timeout':3,**extra})
    def test_review_binds_exact_version_and_requires_local_choice(self):
        with self.assertRaises(ValueError):self.approve(expected_digest='wrong')
        with self.assertRaises(ValueError):self.approve(allow_local=False)
        with self.assertRaises(ValueError):self.research.run(self.sid,self.request,'unapproved')
        self.approve()
        with self.assertRaises(ValueError):self.approve()
        other=self.store.add_record(self.sid,{'kind':'plan','title':'新方案','content':'另一版研究协议'}, {})
        with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'plan_id':other['id']},'other-plan')
    def test_actual_run_is_frozen_once_and_budget_is_cumulative(self):
        self.approve()
        first=self.research.run(self.sid,self.request,'original-call')
        self.assertEqual(first['state'],'completed',first)
        self.assertTrue((self.workspace/first['directory']/'metrics.json').is_file())
        collection=self.store.evidence.summary('local',first['run_id'])
        self.assertTrue(collection['analysis_ready'],collection)
        self.assertTrue((self.workspace/collection['summary_path']).is_file())
        packet=self.research.status(self.sid)['round_evidence']
        self.assertEqual(packet['attempts'][0]['collection']['id'],collection['id'])
        (self.workspace/'train.py').write_text('raise RuntimeError("new code")')
        self.assertEqual(self.research.run(self.sid,self.request,'original-call'),first)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)
        with self.assertRaises(ValueError):self.research.run(self.sid,self.request,'second-fit')
        self.assertIn('未判定',first['research_quality'])
        self.assertNotEqual((self.workspace/first['directory']/'train.py').read_text(),(self.workspace/'train.py').read_text())
    def test_frozen_input_change_invalidates_computation(self):
        self.approve()
        (self.workspace/'data.txt').write_text('original')
        (self.workspace/'train.py').write_text("open('data.txt','w').write('changed')")
        result=self.research.run(self.sid,{**self.request,'files':['train.py','data.txt']},'changed-input')
        self.assertEqual(result['state'],'invalid_inputs')
        self.assertEqual((self.workspace/'data.txt').read_text(),'original')
    def test_empty_declared_output_is_not_accepted_as_completed_result(self):
        self.approve(timeout=15)
        (self.workspace/'train.py').write_text("open('metrics.json','w').close()",encoding='utf-8')
        request={**self.request,'purpose':'diagnostic'}
        result=self.research.run(self.sid,request,'empty-result')
        self.assertEqual(result['state'],'artifacts_missing',result)
        self.assertEqual(result['empty_outputs'],['metrics.json']);self.assertIn('为空',result['error'])
        evidence=self.store.evidence.summary('local',result['run_id'])
        self.assertTrue(evidence['analysis_ready']);self.assertEqual(evidence['analysis_scope'],'failure_diagnosis')
        self.assertEqual(self.research.run(self.sid,request,'empty-result'),result)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)

    def test_timeout_counts_and_original_identity_is_not_replayed(self):
        self.approve(timeout=1)
        (self.workspace/'train.py').write_text('import time; time.sleep(15)')
        result=self.research.run(self.sid,self.request,'timeout')
        self.assertEqual(result['state'],'timed_out')
        self.assertTrue(result['timing']['timeout_observed'])
        self.assertEqual(result['timing']['wait_limit_seconds'],1)
        self.assertGreater(result['timing']['process_wall_seconds'],0)
        self.assertIn('计入累计',result['error'])
        self.assertEqual(self.research.run(self.sid,self.request,'timeout'),result)
        with self.assertRaises(ValueError):self.research.run(self.sid,self.request,'retry')
    def test_unknown_original_blocks_new_run_after_restart(self):
        self.approve()
        result=self.research.run(self.sid,self.request,'original')
        self.store.execute("UPDATE research_runs SET state='running',receipt=NULL WHERE id=?",(result['run_id'],))
        recovered=Research(self.store)
        self.assertEqual(recovered.run(self.sid,self.request,'original')['state'],'unknown')
        with self.assertRaises(ValueError):recovered.run(self.sid,{**self.request,'purpose':'diagnostic'},'new-identity')
        recovered.close()
    def test_focus_has_no_fixed_pipeline_and_cross_project_refs_rejected(self):
        for phase in ['assess','plan','understand','conduct']:
            self.research.focus(self.sid,phase,'真实证据要求改变当前工作',self.plan['id'])
        with self.assertRaises(ValueError):self.research.focus(self.sid,'assess','明确理由','foreign-record')
        self.assertEqual(self.research.status(self.sid)['focus']['phase'],'conduct')
    def test_input_escape_is_not_an_execution_attempt(self):
        self.approve()
        with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'files':['../outside.py']},'escape')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
    def test_utf8_write_does_not_depend_on_shell_quoting_or_overwrite_receipts(self):
        source='print("诊断：$value 和 `literal` 保持原样")\n'
        result=self.research.write(self.sid,'src/inspect.py',source)
        self.assertEqual((self.workspace/'src/inspect.py').read_text(encoding='utf-8'),source)
        self.assertEqual(len(result['sha256']),64)
        for bad in ['../outside.py','runs/original/receipt.json','.env']:
            with self.assertRaises(ValueError):self.research.write(self.sid,bad,'changed')
    def test_plan_evidence_change_does_not_reuse_old_approval(self):
        (self.workspace/'protocol.md').write_text('frozen protocol')
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'冻结协议','content':'按固定协议执行','evidence':['protocol.md']},{})
        self.approve()
        (self.workspace/'protocol.md').write_text('changed protocol')
        with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'plan_id':self.plan['id']},'changed-protocol')
    def test_changed_proposal_evidence_can_be_rejected_but_not_approved(self):
        path=self.workspace/'candidate.md';path.write_text('原方案文件',encoding='utf-8')
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'待审具体方案','content':'原方案正文','evidence':['candidate.md']},{})
        self.assertTrue(self.research.evidence_integrity(self.plan)['valid'])
        path.write_text('保存后补版本文字',encoding='utf-8')
        self.assertFalse(self.research.evidence_integrity(self.plan)['valid'])
        with self.assertRaises(ValueError):self.approve()
        rejected=self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'reject','reason':'引用文件变动，请保留旧版并修订'})
        self.assertEqual(rejected['decision'],'reject')
        self.assertEqual(self.store.one('SELECT content FROM records WHERE id=?',(self.plan['id'],))['content'],'原方案正文')
    def test_configured_venv_launcher_is_owned_and_supported(self):
        python=os.environ.get('ITERLAB_TEST_COMPUTE_PYTHON')
        if not python:self.skipTest('Optional real compute interpreter not configured')
        self.approve(python=python,timeout=15)
        result=self.research.run(self.sid,self.request,'venv-launch')
        self.assertEqual(result['state'],'completed',result)

class MethodTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        self.project=self.store.create_project('method-fixture');self.sid=self.store.create_session(self.project['id'],'ept')['id']
        # Keep all original strict native-Skill assertions in the legacy mode.
        self.sdm=SDM(self.store,default_mode='native_skill')
    def tearDown(self):self.store.close()
    def test_pinned_method_tamper_detected(self):
        first=self.sdm.binding(self.sid)
        self.assertNotEqual(Path(first['path']).parent,ROOT/'skills/sdm-research')
        Path(first['path']).write_text('tampered')
        with self.assertRaises(ValueError):self.sdm.binding(self.sid)
    def test_native_skill_input_and_delivery_provenance(self):
        stamp='2026-10-28T00:00:00Z'
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('original',self.sid,'研究任务','queue','queued',None,None,stamp,stamp))
        result=self.sdm.input(self.sid,{'id':'original','text':'研究任务'})
        self.assertEqual(result[1]['type'],'skill')
        self.assertEqual(result[1]['name'],'sdm-research')
        self.assertEqual(self.store.one('SELECT delivery_id FROM method_invocations')['delivery_id'],'original')
    def test_missing_native_discovery_does_not_claim_loaded(self):
        class RPC:
            generation='test'
            def request(self,*args):return {'data':[]}
        with self.assertRaises(ValueError):self.sdm.prepare(self.sid,RPC())
        self.assertEqual(self.store.rows("SELECT * FROM events WHERE kind='sdm.native_bound'"),[])

if __name__=='__main__':unittest.main()
