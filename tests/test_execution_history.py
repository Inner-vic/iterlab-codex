import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from lab.store import Store,now
from lab.kernel import Kernel
from lab.web import Application
from lab.execution_history import index
from tests.test_core import FakeRPC


class ExecutionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='iterlab-execution-history-')
        self.store=Store(self.temp.name);self.kernel=Kernel(self.store,FakeRPC)
        self.pid=self.store.create_project('isolated attempt projection')['id']
        self.sid=self.store.create_session(self.pid,'ept')['id']
        self.plan=self.record('plan');self.a=self.record('trial',plan_id=self.plan['id'])
        self.b=self.record('trial',plan_id=self.plan['id']);self.c=self.record('trial',plan_id=self.plan['id'])
    def tearDown(self):self.kernel.close();self.store.close();self.temp.cleanup()
    def record(self,kind,source=None,**fields):
        return self.store.add_record(self.sid,{'kind':kind,'title':kind,'content':'fixture; not research evidence',**fields},source or {'origin':'fixture'})
    def attempt(self,name,trial,state,previous=None,sid=None):
        stamp=now();request={'predecessor_uid':previous,'change_summary':'fix only entry path' if previous else '', 'validation':'offline import passed' if previous else ''}
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (name,sid or self.sid,self.pid,self.plan['id'],trial['id'],'fixture-component',name,json.dumps(request),state,None,stamp,stamp))
    def test_failed_retries_group_with_original_trial_and_detail_keeps_all_evidence(self):
        self.attempt('first',self.a,'failed');self.attempt('second',self.b,'failed','first');self.attempt('third',self.c,'succeeded','second')
        diagnostic=self.record('research',{'origin':'system-lpai-diagnostic','task_uid':'first'},parent_id=self.a['id'])
        negative=self.record('result',status='failed',plan_id=self.plan['id'],parent_id=self.c['id'])
        self.store.execute('INSERT INTO company_diagnostics VALUES(?,?,?,?,?,?,?,?)',
            ('proof',self.sid,'first','failed','platform/lpai/first.json','sha',json.dumps({'signals':['missing module']}),now()))
        before=self.store.rows('SELECT * FROM records')
        view=index(self.store,self.pid);self.assertEqual(view['roots'][self.c['id']],self.a['id'])
        graph=self.store.graph(self.pid);nodes={r['id']:r for r in graph['nodes']}
        self.assertEqual(nodes[self.a['id']]['execution_attempt_count'],3)
        self.assertEqual(nodes[self.a['id']]['status'],'blocked','平台成功但归档未核验，不能显示研究执行完成')
        self.assertTrue(nodes[self.b['id']]['execution_detail_only']);self.assertTrue(nodes[diagnostic['id']]['execution_detail_only'])
        self.assertNotIn('execution_detail_only',nodes[negative['id']])
        app=SimpleNamespace(store=self.store,kernel=self.kernel)
        detail=Application.route(app,'GET','/api/records/'+self.a['id'],{},None)
        self.assertEqual([r['任务 UID'] for r in detail['runs']],['first','second','third'])
        self.assertEqual(detail['runs'][0]['失败取证'][0]['诊断文件'],'platform/lpai/first.json')
        self.assertEqual(detail['runs'][1]['修复验证'],'offline import passed')
        # A historical retry's own source remains readable and resolves the same group.
        other=Application.route(app,'GET','/api/records/'+self.c['id'],{},None)
        self.assertEqual(len(other['runs']),3)
        self.assertEqual(self.store.rows('SELECT * FROM records'),before)
    def test_successful_or_unrelated_runs_never_become_repair_groups(self):
        self.attempt('first',self.a,'succeeded');self.attempt('second',self.b,'failed','first');self.attempt('third',self.c,'failed')
        view=index(self.store,self.pid)
        self.assertEqual(view['roots'][self.b['id']],self.b['id']);self.assertEqual(view['roots'][self.c['id']],self.c['id'])
    def test_cross_session_or_ambiguous_uid_does_not_merge(self):
        self.attempt('same',self.a,'failed')
        other=self.store.create_session(self.pid,'ept')['id']
        self.attempt('next',self.b,'failed','same',sid=other)
        self.assertEqual(index(self.store,self.pid)['roots'][self.b['id']],self.b['id'])
    def test_different_plan_without_same_branch_proof_is_not_grouped(self):
        self.attempt('first',self.a,'failed');self.attempt('second',self.b,'failed','first')
        new=self.record('plan')
        self.store.execute('UPDATE company_training SET plan_id=? WHERE id=?',(new['id'],'second'))
        self.assertEqual(index(self.store,self.pid)['roots'][self.b['id']],self.b['id'])
