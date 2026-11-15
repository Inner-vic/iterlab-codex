import json
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid

from lab.store import Store,now
from lab.research import Research,plan_digest

ROOT=Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        self.project=self.store.create_project('workflow-fixture')
        self.sid=self.store.create_session(self.project['id'],'ept')['id']
        self.workspace=Path(self.project['workspace']);self.research=Research(self.store)
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'有边界的执行方案','content':'仅验证程序行为，不验证研究质量'}, {})
        self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),'decision':'accept',
            'reason':'隔离程序回归','allow_local':True,'execution':'local','python':sys.executable,'max_runs':3,'max_fits':2,'timeout':3})
        (self.workspace/'probe.py').write_text("open('metrics.json','w').write('{}')",encoding='utf-8')
        self.request={'plan_id':self.plan['id'],'script':'probe.py','files':['probe.py'],'args':[],'purpose':'diagnostic',
                      'question':'程序接口是否按合同工作','expected_information':'仅工程回归，不是科学结论','outputs':['metrics.json']}
    def tearDown(self):self.research.close();self.store.close()
    def add_delivery(self,id):
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(id,self.sid,'真实输入','queue','accepted',None,None,now(),now()))
    def test_exit_zero_missing_artifacts_is_blocked_and_not_replayed(self):
        (self.workspace/'probe.py').write_text('pass',encoding='utf-8')
        result=self.research.run(self.sid,self.request,'missing')
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['state'],'artifacts_missing')
        self.assertEqual(result['missing_outputs'],['metrics.json'])
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'blocked')
        self.assertEqual(self.research.run(self.sid,self.request,'missing'),result)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)
    def test_explicit_trial_and_assessment_links_project_status_without_rewriting_history(self):
        trial=self.store.add_record(self.sid,{'kind':'trial','title':'执行提案','content':'尚未运行','status':'proposed'}, {})
        original=self.store.one('SELECT * FROM records WHERE id=?',(trial['id'],))
        result=self.research.run(self.sid,{**self.request,'trial_id':trial['id']},'linked')
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'awaiting_assessment')
        node=next(n for n in self.store.graph(self.project['id'])['nodes'] if n['id']==trial['id'])
        self.assertEqual((node['status'],node['recorded_status'],node['status_source']),('completed','proposed','exact_run_link'))
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(trial['id'],)),original)
        self.store.add_record(self.sid,{'kind':'decision','title':'结果处置','content':'工程产物已读，不声称科学成功','run_ids':[result['run_id']]}, {})
        self.assertNotEqual(self.research.workflow.publish(self.sid)['state'],'awaiting_assessment')
    def test_foreign_and_non_trial_references_are_rejected_before_attempt(self):
        foreign=self.store.create_project('foreign');sid=self.store.create_session(foreign['id'],'ept')['id']
        trial=self.store.add_record(sid,{'kind':'trial','title':'外项目实验','content':'不是当前项目'}, {})
        for invalid in [trial['id'],self.plan['id']]:
            with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'trial_id':invalid},invalid)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        run=self.research.run(self.sid,self.request,'owned')
        with self.assertRaises(ValueError):self.store.add_record(sid,{'kind':'result','title':'错误引用','content':'外项目','run_ids':[run['run_id']]},{})
    def test_brief_keeps_consensus_reference_and_omits_large_receipt(self):
        alignment=self.store.add_record(self.sid,{'kind':'alignment','title':'重要边界','content':'不得改变划分'}, {})
        for n in range(8):self.store.add_record(self.sid,{'kind':'research','title':str(n),'content':'过程资料'}, {})
        run=self.research.run(self.sid,self.request,'brief')
        self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?',(json.dumps({'large':'x'*100000}),run['run_id']))
        brief=self.research.status(self.sid,'brief')
        self.assertEqual(brief['default_python'],sys.executable)
        self.assertEqual(brief['latest_alignment_record']['id'],alignment['id'])
        self.assertEqual(brief['attempts'],{'runs':1,'fits':0})
        self.assertLess(len(json.dumps(brief)),12000)
        self.assertNotIn('receipt',brief['recent_runs'][0])
        full=self.research.snapshot(self.sid,alignment['id'])
        self.assertEqual(full['content'],'不得改变划分')
    def test_projection_is_idempotent_and_historical_runs_do_not_create_new_work(self):
        run=self.research.run(self.sid,self.request,'history')
        self.store.execute('DELETE FROM research_run_context WHERE run_id=?',(run['run_id'],))
        self.research.focus(self.sid,'deliver','既有历史已收束')
        first=self.research.workflow.publish(self.sid)
        count=len(self.store.rows("SELECT * FROM events WHERE kind='research.lifecycle_changed'"))
        for _ in range(3):self.assertEqual(self.research.workflow.publish(self.sid),first)
        self.assertEqual(len(self.store.rows("SELECT * FROM events WHERE kind='research.lifecycle_changed'")),count)
        self.assertEqual(first['state'],'idle')
    def test_pause_keeps_submitted_worker_running_and_background_cannot_unpause(self):
        (self.workspace/'probe.py').write_text("import time; time.sleep(0.3); open('metrics.json','w').write('{}')",encoding='utf-8')
        receipts=[];errors=[]
        def execute():
            try:receipts.append(self.research.run(self.sid,self.request,'paused-worker'))
            except Exception as exc:errors.append(str(exc))
        worker=threading.Thread(target=execute);worker.start()
        try:
            for _ in range(150):
                if self.research.processes:break
                time.sleep(.01)
            self.assertTrue(self.research.processes,{'reason':'worker did not start','errors':errors,'receipts':receipts})
            with self.research.lock:
                process=next(iter(self.research.processes.values()))
                self.research.pause(self.sid)
                self.assertIsNone(process.poll())
                self.assertEqual(self.research.cancelled,set())
            worker.join(5)
            if worker.is_alive():
                import faulthandler
                faulthandler.dump_traceback()
            self.assertFalse(worker.is_alive(),{'errors':errors,'runs':self.store.rows('SELECT id,state,receipt FROM research_runs')})
            self.assertEqual(errors,[])
            self.assertEqual(receipts[0]['state'],'completed')
            self.assertEqual(self.research.workflow.publish(self.sid)['state'],'paused')
            self.add_delivery('background');self.research.workflow.input(self.sid,'background','background')
            self.assertTrue(self.research.workflow.paused(self.sid))
            with self.assertRaises(ValueError):self.research.workflow.input(self.sid,'background','user')
            with self.assertRaises(ValueError):self.research.run(self.sid,self.request,'not-authorized-resume')
            self.add_delivery('human');self.research.workflow.input(self.sid,'human','user')
            self.assertFalse(self.research.workflow.paused(self.sid))
            self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)
        finally:
            self.research.close();worker.join(5)
    def test_inputs_cannot_be_claimed_as_outputs_or_fit_without_evidence(self):
        with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'outputs':['probe.py']},'input-is-output')
        with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'purpose':'fit','outputs':[]},'no-evidence')
    def test_preexisting_plan_does_not_silently_become_new_review_work(self):
        old=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        try:
            project=old.create_project('old-history');sid=old.create_session(project['id'],'ept')['id']
            old.add_record(sid,{'kind':'plan','title':'迁移前旧方案','content':'旧研究记录，不能重建执行'}, {})
            research=Research(old)
            self.assertEqual(research.workflow.publish(sid)['state'],'idle')
            old.add_record(sid,{'kind':'plan','title':'迁移后新方案','content':'本轮新提案'}, {})
            self.assertEqual(research.workflow.publish(sid)['state'],'waiting_review')
            research.close()
        finally:old.close()


if __name__=='__main__':unittest.main()
