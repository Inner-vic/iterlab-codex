"""Timed host integration. Scripted peer/platform, not real cloud/scientific validation."""
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid

from lab.store import Store,now
from lab.kernel import Kernel
from lab.roles import default_policy
from lab.research import plan_digest
from tests.test_autonomous_host import ScriptedPeer

ROOT=Path(__file__).resolve().parents[1]


class CloudContinuationHostTests(unittest.TestCase):
    def test_queue_restart_failure_budget_assessment_without_human_or_manual_tick(self):
        root=ROOT/'.evidence/tests'/uuid.uuid4().hex
        store=Store(root);kernel=None;phase={'value':'Queued'};done=threading.Event();errors=[];turns=[];upload_ready=threading.Event()
        def attach(k):
            k.settings.command=lambda *a:['isolated-peer']
            k.settings.verify_channel=lambda:None
            k.company.lpai=lambda *a,**kw:{'item':{'uid':'fixture-task','name':'fixture job','namespace':k.company.config()['namespace'],'phase':phase['value']}}
            k.company.authority=lambda sid:{'fixture':'same-public-authority','revision':k.company.config()['revision']}
            k.company.training.diagnose=lambda *a,**kw:{'id':'fixture-proof','path':'fixture-proof.md','summary':{'signals':[]}}
            def collect(sid,uid,refresh=False):
                attempt=store.one('SELECT COUNT(*) AS n FROM company_output_reads')['n']+1
                state='read_failed' if upload_ready.is_set() else 'directory_empty'
                result={'state':state,'task_uid':uid,'attempt':attempt,'message':'脚本化的产物缺口，仅分析异常'}
                store.execute('INSERT INTO company_output_reads VALUES(?,?,?,?,?,?,?,?,?)',
                    (sid,uid,attempt,k.company.config()['namespace'],'fixture-no-files',state,json.dumps(result),now(),now()))
                return result
            k.company.outputs.collect=collect
            k.transport_factory=lambda *a:make_peer(*a)
        def make_peer(*args):
            peer=ScriptedPeer(*args);peer.scenario=scenario;return peer
        class Scenario:
            def perform(self,peer,turn,params):
                try:
                    deadline=time.monotonic()+5
                    while store.one('SELECT state FROM deliveries WHERE id=?',(params['clientUserMessageId'],))['state']!='accepted':
                        if time.monotonic()>deadline:raise TimeoutError('fixture ACK')
                        time.sleep(.01)
                    turns.append(params)
                    job=store.one('SELECT state,outcome FROM company_output_jobs')
                    assert job['state']=='diagnostic_only',job
                    assert json.loads(job['outcome'])['analysis_scope']=='failure_or_evidence_gap_only'
                    assert '计算预算已到' in str(params['input'])
                    assert params['sandboxPolicy']=={'type':'readOnly'}
                    for index,record in enumerate([
                        {'kind':'result','title':'平台失败的工程分析'},
                        {'kind':'review','title':'预算耗尽后的评审','round_outcome':'stop_requested','stop_reason':'budget'}]):
                        call_id='cloud-analysis:'+str(index)
                        peer.on_request({'id':call_id,'method':'item/tool/call','params':{
                            'threadId':'native-thread','turnId':turn,'callId':call_id,'tool':'research_record',
                            'arguments':{'content':'脚本化模拟：一次失败已用尽预算，仅评审，不声称研究目标达成。',
                                'plan_id':plan['id'],'status':'recorded','evidence':[],
                                'cloud_run_ids':['fixture-submission'],**record}}},peer)
                        reply=next(r for r in peer.replies if r[0]==call_id)
                        if record['kind']=='review':
                            assert not reply[1]['success'],reply
                            assert '预算暂停只盘点结果' in reply[1]['contentItems'][0]['text'],reply
                        else:assert reply[1]['success'],reply
                except BaseException as exc:errors.append(repr(exc))
                finally:
                    peer.on_event({'method':'turn/completed','params':{'threadId':'native-thread','turn':{'id':turn,'status':'completed'}}})
                    done.set()
        scenario=Scenario()
        try:
            kernel=Kernel(store);kernel.settings.save({'channel':'ept','agent_roles':default_policy('ept')});attach(kernel)
            pid=store.create_project('scripted cloud continuation')['id'];sid=store.create_session(pid,'ept')['id']
            workspace=Path(store.session(sid)['workspace']);(workspace/'protocol.md').write_text('Engineering fixture only.',encoding='utf-8')
            plan=store.add_record(sid,{'kind':'plan','title':'隔离方案','content':'仅测试持久后台续接；平台和研究判断都为脚本。','evidence':['protocol.md']},{})
            kernel.research.review(sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept',
                'reason':'隔离工程边界','allow_local':True,'python':sys.executable,'max_runs':1,'max_fits':1,'timeout':5})
            kernel.research.autonomy.enable(sid,plan['id'],plan_digest(plan),{
                'objective':'只测事件接续','evaluation':'脚本化控制','data_boundary':'无实际训练数据',
                'allowed_changes':'不运行新计算','frozen_files':['protocol.md']},'测试操作者')
            store.patch_session(sid,thread_id='native-thread')
            store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('fixture-submission',sid,pid,plan['id'],'fixture-trial','fixture-component','fixture-task','{}','submitted',None,now(),now()))
            kernel.company.configure({'revision':kernel.company.config()['revision'],'lpai_enabled':True})
            kernel.company.watch(sid,'fixture-task',True)
            kernel.company.start_monitor(kernel);kernel.continuation.start()
            self.assertFalse(done.wait(10.5));self.assertEqual(turns,[]);self.assertIsNone(kernel.rpc)
            # Real Store/Kernel restart, original IDs and budget persist.
            kernel.close();store.close();store=Store(root);kernel=Kernel(store);attach(kernel)
            self.assertEqual(store.session(sid)['thread_id'],'native-thread')
            self.assertEqual(store.one('SELECT state FROM company_training')['state'],'submitted')
            phase['value']='Failed'
            store.execute('UPDATE company_watches SET next_check=0') # accelerate only the platform clock in this fixture
            kernel.company.start_monitor(kernel);kernel.continuation.start()
            self.assertFalse(done.wait(11),'must not wake Codex while results are awaiting upload')
            self.assertEqual(turns,[])
            self.assertEqual(store.one('SELECT state FROM company_output_jobs')['state'],'waiting_outputs')
            upload_ready.set()
            store.execute('UPDATE company_output_jobs SET next_check=0') # fixture upload clock only; not manual tick or model send
            store.execute('UPDATE company_watches SET next_check=0')
            self.assertTrue(done.wait(35),'automatic analysis deadline');self.assertEqual(errors,[])
            self.assertEqual(len(turns),1)
            self.assertEqual(store.rows("SELECT id FROM records WHERE kind='review'"),[])
            self.assertEqual(store.one("SELECT plan_id FROM research_grants")['plan_id'],plan['id'])
            self.assertEqual(store.one('SELECT COUNT(*) AS n FROM company_training')['n'],1)
            self.assertEqual(store.rows('SELECT * FROM research_runs'),[])
            self.assertFalse(any(m=='thread/start' for m,_ in kernel.rpc.calls))
            self.assertTrue(any(m=='thread/resume' for m,_ in kernel.rpc.calls))
            self.assertFalse(store.rows("SELECT * FROM research_input_origins WHERE origin IN ('user','review')"))
            self.assertFalse(kernel.rpc.stopped.wait(10.5));self.assertEqual(len(turns),1)
        finally:
            if kernel:kernel.close()
            store.close()


if __name__=='__main__':unittest.main()
