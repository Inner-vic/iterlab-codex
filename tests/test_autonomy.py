import json
from pathlib import Path
import sys
import tempfile
import unittest
import time
from unittest.mock import patch

from lab.store import Store,now
from lab.research import Research,plan_digest
from lab.kernel import Kernel
from lab import rounds
from tests.test_core import FakeRPC


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='iterlab-autonomy-')
        self.store=Store(self.temp.name)
        self.kernel=Kernel(self.store,FakeRPC);self.kernel.settings.value['channel']='ept'
        self.kernel.settings.command=lambda *args:['fixture']
        self.kernel.settings.verify_channel=lambda:None
        self.r=self.kernel.research;self.pid=self.store.create_project('autonomy-isolated')['id']
        self.sid=self.store.create_session(self.pid,'ept')['id'];self.workspace=Path(self.store.session(self.sid)['workspace'])
        (self.workspace/'protocol.md').write_text('Synthetic control fixture: preserve evaluation protocol.',encoding='utf-8')
        (self.workspace/'train.py').write_text("import json\nw=0.0\nfor _ in range(30):w-=0.01*sum(2*x*(w*x-2*x) for x in [1,2,3])/3\njson.dump({'weight':w,'loss':sum((w*x-2*x)**2 for x in [1,2,3])/3},open('metrics.json','w'))\n",encoding='utf-8')
        self.first=self.record('plan','初始方案')
        self.r.review(self.sid,{'plan_id':self.first['id'],'expected_digest':plan_digest(self.first),'decision':'accept','reason':'隔离工程验收',
            'allow_local':True,'python':sys.executable,'max_runs':4,'max_fits':3,'timeout':5})
        self.scope={'objective':'验证研究编排，不代表工业研究','evaluation':'固定合成样例损失','data_boundary':'三个本地标量样本','allowed_changes':'允许调整标量优化参数','frozen_files':['protocol.md']}
        self.r.autonomy.enable(self.sid,self.first['id'],plan_digest(self.first),self.scope,'用户隔离授权')
        self.request={'script':'train.py','files':['train.py','protocol.md'],'args':[],'purpose':'fit','question':'有界标量训练能否完成',
            'expected_information':'仅用于检验跨轮执行和证据身份','outputs':['metrics.json']}

    def tearDown(self):self.kernel.close();self.store.close();self.temp.cleanup()

    def test_executor_write_rejects_approved_evidence_but_accepts_trial_configuration(self):
        original=(self.workspace/'protocol.md').read_bytes()
        with self.assertRaisesRegex(ValueError,'冻结'):self.r.write(self.sid,'./protocol.md','bad rewrite')
        self.assertEqual((self.workspace/'protocol.md').read_bytes(),original)
        result=self.r.write(self.sid,'implementation/config.json','{"seed":42}')
        self.assertEqual(result['path'],'implementation/config.json')
        self.r.check_plan_evidence(self.first)
    def record(self,kind,title,**fields):
        return self.store.add_record(self.sid,{'kind':kind,'title':title,'content':'隔离工程控制用例；不代表真实模型自主研究。','evidence':['protocol.md'],**fields},{'origin':'isolated-fixture'})
    def execute(self,plan,operation):return self.r.run(self.sid,{**self.request,'plan_id':plan['id']},operation)
    def close_round(self,plan,run=None):return self.record('review','本轮证据评审',plan_id=plan['id'],run_ids=[run['run_id']] if run else [])
    def next(self,plan,review,**fields):
        a=self.r.autonomy.status(self.sid)
        return self.record('plan','后继方案',**{'previous_plan_id':plan['id'],'review_record_id':review['id'],'authority_id':a['authority_id'],'scope_digest':a['scope_digest'],**fields})
    def adopt(self,plan):return self.r.autonomy.adopt(self.sid,plan['id'],plan_digest(plan),'已分析本轮证据，按授权选择最小后继')

    def test_actual_fits_cross_round_without_faking_human_review_or_resetting_budget(self):
        first=self.execute(self.first,'fit-1');review=self.close_round(self.first,first);candidate=self.next(self.first,review)
        self.assertEqual(rounds.state(self.store,self.pid)['state'],'waiting_plan_adoption')
        with self.assertRaises(ValueError):self.execute(candidate,'not-adopted')
        self.adopt(candidate);second=self.execute(candidate,'fit-2')
        self.assertEqual([first['state'],second['state']],['completed','completed'])
        self.assertEqual(first['plan_id'],self.first['id']);self.assertEqual(second['plan_id'],candidate['id'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_reviews')['n'],1)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_grants')['n'],1)
        self.assertEqual(self.r.status(self.sid)['attempts'],{'runs':2,'fits':2})
        self.assertIn('weight',json.loads((self.workspace/second['directory']/'metrics.json').read_text()))
        self.close_round(candidate,second)
        with self.assertRaises(ValueError):self.execute(self.first,'return-to-old')
    def test_newly_adopted_plan_is_ready_not_old_round_assessment(self):
        run=self.execute(self.first,'fit-1');review=self.close_round(self.first,run);candidate=self.next(self.first,review)
        self.record('result','旧轮结果',run_ids=[run['run_id']])
        self.r.focus(self.sid,'deliver','旧轮已有阶段报告')
        self.adopt(candidate)
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'ready')
        self.assertEqual(self.r.workflow.publish(self.sid)['subject_id'],candidate['id'])

    def test_scope_identity_frozen_protocol_and_revocation_are_enforced(self):
        review=self.close_round(self.first);candidate=self.next(self.first,review,scope_digest='0'*64)
        with self.assertRaisesRegex(ValueError,'授权和固定边界'):self.adopt(candidate)
        good=self.next(self.first,review);(self.workspace/'protocol.md').write_text('changed protocol',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'证据已变化'):self.adopt(good)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_plan_admissions')),0)

    def test_explicit_plan_snapshot_reports_bytes_and_candidate_references_without_mutation(self):
        original=self.store.one('SELECT * FROM records WHERE id=?',(self.first['id'],))
        proof=self.r.snapshot(self.sid,self.first['id'])['frozen_evidence']
        self.assertTrue(proof['bytes_match_authority'])
        self.assertTrue(proof['candidate_covers_authority'])
        self.assertEqual(proof['files'][0]['state'],'matched')
        self.assertEqual(proof['files'][0]['candidate_reference'],'matched')
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(self.first['id'],)),original)
        with patch.object(self.r,'verify_frozen_files') as verify:
            self.r.snapshot(self.sid)
            verify.assert_not_called()

    def test_explicit_plan_snapshot_distinguishes_omitted_reference_from_changed_bytes(self):
        candidate=self.store.add_record(self.sid,{'kind':'plan','title':'缺引用','content':'工程fixture，不代表真实方案','evidence':[]},{'origin':'isolated-fixture'})
        proof=self.r.snapshot(self.sid,candidate['id'])['frozen_evidence']
        self.assertTrue(proof['bytes_match_authority'])
        self.assertFalse(proof['candidate_covers_authority'])
        self.assertEqual(proof['files'][0]['candidate_reference'],'missing')
        (self.workspace/'protocol.md').write_text('actual changed bytes',encoding='utf-8')
        proof=self.r.snapshot(self.sid,self.first['id'])['frozen_evidence']
        self.assertFalse(proof['bytes_match_authority'])
        self.assertTrue(proof['candidate_covers_authority'])
        self.assertEqual(proof['files'][0]['state'],'changed')
        (self.workspace/'protocol.md').unlink()
        self.assertEqual(self.r.snapshot(self.sid,self.first['id'])['frozen_evidence']['files'][0]['state'],'unavailable')

    def test_rejected_metadata_revision_reports_actual_round_not_candidate_or_critic(self):
        review=self.close_round(self.first)
        candidate=self.next(self.first,review)
        critic=self.record('decision','Critic fixture')
        for previous_id,review_id in [(candidate['id'],review['id']),(self.first['id'],critic['id'])]:
            with self.assertRaises(ValueError) as error:
                self.record('plan','错误元信息修订',previous_plan_id=previous_id,review_record_id=review_id)
            self.assertIn('previous_plan_id='+self.first['id'],str(error.exception))
            self.assertIn('review_record_id='+review['id'],str(error.exception))
            self.assertIn('Critic的decision不是轮次review',str(error.exception))
        self.assertEqual(rounds.approved_plan(self.store,self.pid)['id'],self.first['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_plan_admissions'),[])

    def test_scope_restatement_is_rejected_with_autonomous_metadata_repair_path(self):
        review=self.close_round(self.first)
        proposal={key:self.scope[key] for key in ['objective','evaluation','data_boundary','allowed_changes']}
        proposal['objective']='A more specific sub-question, not a new grant'
        candidate=self.next(self.first,review,scope_proposal=proposal)
        with self.assertRaisesRegex(ValueError,'字段不同：objective.*另存元信息修订.*省略scope_proposal.*Critic'):
            self.adopt(candidate)

    def test_new_execution_route_is_not_misdiagnosed_as_scope_restatement(self):
        review=self.close_round(self.first)
        from lab.execution_request import select
        select(self.store,self.sid,'lpai','后续明确改用组件库')
        proposal={key:self.scope[key] for key in ['objective','evaluation','data_boundary','allowed_changes']}
        candidate=self.next(self.first,review,scope_proposal=proposal)
        before=self.store.rows('SELECT * FROM research_grants')
        with self.assertRaisesRegex(ValueError,'真实的新执行范围.*保留完整scope_proposal.*首次正式.*不得省略'):
            self.adopt(candidate)
        omitted=self.next(self.first,review)
        with self.assertRaisesRegex(ValueError,'首次正式方案审核'):self.adopt(omitted)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertFalse(self.store.rows('SELECT * FROM research_plan_admissions'))
        self.assertEqual(rounds.approved_plan(self.store,self.pid)['id'],self.first['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_plan_admissions'),[])

    def test_cumulative_fits_not_reset_and_unknown_prevents_adoption(self):
        run=self.execute(self.first,'original');review=self.close_round(self.first,run);candidate=self.next(self.first,review)
        self.store.execute("UPDATE research_runs SET state='unknown' WHERE id=?",(run['run_id'],))
        with self.assertRaisesRegex(ValueError,'未知计算'):self.adopt(candidate)
        self.store.execute("UPDATE research_runs SET state='completed' WHERE id=?",(run['run_id'],));self.adopt(candidate)
        self.store.execute('UPDATE research_grants SET max_fits=1')
        with self.assertRaisesRegex(ValueError,'累计'):self.execute(candidate,'cannot-reset-fit-budget')

    def test_human_fork_keeps_history_candidate_does_not_steal_mainline(self):
        original=dict(self.first)
        fork=self.r.autonomy.fork(self.sid,self.first['id'],'人工替代方案','保留固定评价，检查另一项优化参数。','人工提出替代方向')
        self.assertEqual(rounds.approved_plan(self.store,self.pid)['id'],self.first['id'])
        self.r.autonomy.switch(self.sid,fork['branch_id'],'用户选择替代方向')
        self.assertEqual(rounds.approved_plan(self.store,self.pid)['id'],fork['plan_id'])
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(self.first['id'],)),original)
        with self.assertRaisesRegex(ValueError,'活动分支'):self.execute(self.first,'wrong-branch')
        graph=self.store.graph(self.pid)
        self.assertTrue(any(e['kind']=='branches' and e['basis']=='explicit_human_intervention' and e['source']==self.first['id'] and e['target']==fork['plan_id'] for e in graph['edges']))
        self.assertEqual(graph['active_branch_id'],fork['branch_id'])
        repeated=self.r.autonomy.fork(self.sid,self.first['id'],'人工替代方案','保留固定评价，检查另一项优化参数。','人工提出替代方向')
        self.assertEqual(repeated['branch_id'],fork['branch_id'])
    def test_human_major_revision_forms_downstream_series(self):
        fork=self.r.autonomy.fork(self.sid,self.first['id'],'人工新路线','仍在原目标与评价范围，采用另一条路线。','原路径存在反例，需要新路线',revision_scope='major')
        self.assertEqual(fork['version'],'2.0')
        self.assertEqual(rounds.approved_plan(self.store,self.pid)['id'],self.first['id'])
        meta=self.store.one('SELECT * FROM research_plan_lineage WHERE plan_id=?',(fork['plan_id'],))
        self.assertEqual(meta['predecessor_series_id'],self.first['id'])
    def test_revoked_root_authority_blocks_automatic_dispatch(self):
        review=self.close_round(self.first);candidate=self.next(self.first,review);self.adopt(candidate)
        self.r.review(self.sid,{'plan_id':self.first['id'],'expected_digest':plan_digest(self.first),'decision':'reject','reason':'用户撤回原授权'})
        with self.assertRaisesRegex(ValueError,'退回'):self.execute(candidate,'revoked-original')
        self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status')['state'],'blocked')
    def test_profile_change_blocks_automatic_model_use(self):
        self.kernel.settings.value['profile']='another-profile'
        self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.assertIn('profile',self.store.one('SELECT reason FROM research_scheduler_status')['reason'])

    def test_pause_and_stopped_mode_do_not_self_resume(self):
        review=self.close_round(self.first);candidate=self.next(self.first,review)
        self.r.pause(self.sid)
        with self.assertRaisesRegex(ValueError,'已暂停'):self.adopt(candidate)
        self.kernel.continuation.tick(self.sid);self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.r.autonomy.mode(self.sid,'autonomous','用户明确恢复');self.adopt(candidate)
        self.r.autonomy.mode(self.sid,'manual','人工接管')
        with self.assertRaisesRegex(ValueError,'停用'):self.execute(candidate,'revoked-scope')

    def pending_role(self):
        from lab.roles import default_policy
        self.kernel.settings.value['agent_roles']=default_policy('ept')
        with patch.object(self.kernel.pool,'submit'):
            delivery=self.kernel.send(self.sid,'隔离自动事件',origin='background')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='role-turn' WHERE id=?",(delivery['id'],))
        self.store.patch_session(self.sid,status='running',active_turn='role-turn')
        catalog=[{'model':m} for m in default_policy('ept')['models'].values()]
        self.kernel.roles.route(self.sid,delivery,self.kernel.settings.value,catalog)
        self.kernel.sdm.input(self.sid,delivery) # Pin the actual authority of the source turn.
        h=self.kernel.roles.handoff(self.sid,'role-turn',{'target':'critic','plan_id':self.first['id'],
            'record_id':self.first['id'],'verdict':'handoff','reason':'隔离待续交接'},'pinned-role-call',self.r)
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        return h

    def test_manual_takeover_blocks_pending_autonomous_role_chain(self):
        h=self.pending_role()
        self.r.autonomy.mode(self.sid,'manual','用户接管，停止自主推进')
        with patch.object(self.kernel,'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(h['id'],))['state'],'pending')

    def test_profile_change_blocks_pending_autonomous_role_chain(self):
        self.pending_role();self.kernel.settings.value['profile']='other-profile'
        with patch.object(self.kernel,'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()

    def test_revoked_authority_blocks_pending_autonomous_role_chain(self):
        self.pending_role()
        self.r.review(self.sid,{'plan_id':self.first['id'],'expected_digest':plan_digest(self.first),
            'decision':'reject','reason':'用户撤回确切初始授权'})
        with patch.object(self.kernel,'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()

    def test_queued_role_rechecks_revocation_before_loading_native_host(self):
        h=self.pending_role()
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.resume_roles(self.sid)
        self.r.autonomy.mode(self.sid,'manual','交接已入队后由用户接管')
        with patch.object(self.kernel,'load_thread') as load:
            self.kernel.drain(self.sid)
            load.assert_not_called()
        delivery=self.store.one('SELECT * FROM deliveries WHERE id=?',(h['delivery_id'],))
        self.assertEqual(delivery['state'],'rejected')
        self.assertIn('授权已停用',delivery['error'])
        self.assertIsNone(self.kernel.rpc)

    def test_cancelled_background_event_is_not_sent_after_native_restore(self):
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        delivery=self.store.one('SELECT * FROM deliveries')
        def restore_then_takeover(sid):
            self.r.autonomy.mode(sid,'manual','原生恢复期间用户接管')
            return self.store.session(sid)
        with patch.object(self.kernel,'load_thread',side_effect=restore_then_takeover), \
             patch.object(self.kernel,'_deliver') as deliver:
            self.kernel.drain(self.sid)
            deliver.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM deliveries WHERE id=?',(delivery['id'],))['state'],'cancelled')

    def test_provider_quota_failure_is_durable_block_not_replay_or_scientific_closure(self):
        self.store.event(self.sid,'turn/completed',{'turn':{'id':'original-turn','status':'failed',
            'error':{'message':'{"errmsg":"当前套餐额度已用尽，访问被阻止。 provider detail"}'}}})
        self.store.patch_session(self.sid,status='failed')
        with patch.object(self.kernel,'send') as send:
            self.kernel.continuation.tick(self.sid)
            self.kernel.continuation.tick(self.sid)
            send.assert_not_called()
        value=self.store.one('SELECT * FROM research_scheduler_status WHERE session_id=?',(self.sid,))
        self.assertEqual(value['state'],'blocked')
        self.assertIn('额度已用尽',value['reason'])
        self.assertNotIn('provider detail',value['reason'])
        self.assertEqual(self.store.rows('SELECT * FROM research_finalizations'),[])
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.assertEqual(len(self.store.rows("SELECT * FROM events WHERE kind='research.scheduler_state'")),1)

    def test_continuation_is_durable_idempotent_and_stops_without_progress(self):
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid);self.kernel.continuation.tick(self.sid)
        deliveries=self.store.rows('SELECT * FROM deliveries')
        self.assertEqual(len(deliveries),1);self.assertTrue(deliveries[0]['id'].startswith('research-continue:'))
        self.assertEqual(self.store.one('SELECT origin FROM research_input_origins')['origin'],'background')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(deliveries[0]['id'],))
        self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.store.one('SELECT state FROM research_continuations')['state'],'no_progress')
        from lab.continuation import Continuation
        Continuation(self.kernel).tick(self.sid)
        all_deliveries=self.store.rows('SELECT * FROM deliveries')
        self.assertEqual(len(all_deliveries),2)
        self.assertEqual(all_deliveries[0]['id'],deliveries[0]['id'])
        self.assertTrue(all_deliveries[1]['id'].startswith('research-finalize:'))
        self.assertEqual(len(self.store.rows('SELECT * FROM research_continuations')),1)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def cloud(self,state='queued'):
        from lab.store import now
        trial=self.record('trial','隔离云任务',plan_id=self.first['id'])
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('cloud-run',self.sid,self.pid,self.first['id'],trial['id'],'component-fixture','task-fixture','{}',state,None,now(),now()))

    def test_external_wait_blocks_role_dispatch_without_calling_model(self):
        h=self.pending_role();self.cloud()
        with patch.object(self.kernel,'send') as send:
            for _ in range(5):self.kernel.continuation.tick(self.sid)
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(h['id'],))['state'],'pending')
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'waiting_external')

    def test_cloud_terminal_is_evidence_to_analyze_not_readiness_for_new_training(self):
        self.cloud()
        for terminal in ['Failed','Stopped','Succeeded','Canceled','Error']:
            self.store.execute('UPDATE company_training SET state=?',(terminal,))
            lifecycle=self.r.workflow.publish(self.sid)
            self.assertEqual((lifecycle['state'],lifecycle['subject_kind']),('awaiting_assessment','cloud_run'))
            self.assertFalse(self.r.workflow.paused(self.sid))
            self.assertEqual(self.r.workflow.location(self.sid,self.store.graph(self.pid))['focus_node_id'],
                             self.store.one('SELECT trial_id FROM company_training')['trial_id'])
        self.record('result','实际分析的结果',cloud_run_ids=['cloud-run'])
        self.assertNotEqual(self.r.workflow.publish(self.sid)['state'],'awaiting_assessment')

    def test_reserved_event_recovers_missing_local_enqueue_with_original_identity(self):
        with patch.object(self.kernel,'send',side_effect=RuntimeError('before local enqueue')):
            with self.assertRaises(RuntimeError):self.kernel.continuation.tick(self.sid)
        reserved=self.store.one('SELECT * FROM research_continuations')
        self.assertEqual(reserved['state'],'reserved')
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid);self.kernel.continuation.tick(self.sid)
        self.assertEqual([r['id'] for r in self.store.rows('SELECT * FROM deliveries')],[reserved['delivery_id']])

    def test_restart_drains_original_unsent_queue_without_creating_replacement_event(self):
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one('SELECT * FROM deliveries')
        from lab.continuation import Continuation
        with patch.object(self.kernel.pool,'submit') as pool,patch.object(self.kernel,'send') as send:
            Continuation(self.kernel).tick(self.sid)
            pool.assert_called_once_with(self.kernel.drain,self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[original])

    def test_queued_human_guidance_can_continue_while_remote_compute_keeps_waiting(self):
        self.cloud('queued')
        with patch.object(self.kernel.pool,'submit'):
            original=self.kernel.send(self.sid,'先讨论后续方向，不要停止已提交任务',origin='user')
        with patch.object(self.kernel.pool,'submit') as pool:
            self.kernel.continuation.tick(self.sid)
            pool.assert_called_once_with(self.kernel.drain,self.sid)
        self.assertEqual(self.store.one('SELECT state FROM company_training')['state'],'queued')
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[original])

    def test_budget_boundary_still_wakes_planner_for_final_evidence_assessment(self):
        self.pending_role();self.cloud('Failed')
        self.store.execute('UPDATE research_grants SET max_runs=1')
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid);self.kernel.continuation.tick(self.sid)
        deliveries=self.store.rows("SELECT * FROM deliveries WHERE id LIKE 'research-finalize:%'")
        self.assertEqual(len(deliveries),1)
        self.assertIn('计算预算已到',deliveries[0]['text'])
        self.assertIn('不得新增计算',deliveries[0]['text'])
        self.assertEqual(len(self.store.rows('SELECT * FROM company_training')),1)
        self.assertEqual(self.store.one('SELECT reason FROM research_finalizations')['reason'],'budget_pause')
        self.assertIsNone(rounds.round_review(self.store,self.pid,self.first['id']))
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(deliveries[0]['id'],))
        budget=self.r.resources(self.first)['budget']
        self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],budget['revision'],add_runs=3,add_fits=1)
        self.assertEqual(self.r.resources(self.first)['used_runs'],1)
        self.assertEqual(self.r.resources(self.first)['remaining_runs'],3)
        self.assertEqual(self.store.one('SELECT max_runs FROM research_grants')['max_runs'],1)
        with self.assertRaisesRegex(ValueError,'预算已更新'):
            self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],budget['revision'],add_runs=3)

    def test_submission_only_budget_unifies_fit_allowance_without_rewriting_grant_or_usage(self):
        original=dict(self.store.one('SELECT * FROM research_grants'))
        budget=self.r.resources(self.first)['budget']
        self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],budget['revision'],add_runs=2)
        resources=self.r.resources(self.first)
        self.assertEqual(resources['budget']['max_runs'],6)
        self.assertEqual(resources['budget']['max_fits'],6)
        self.assertEqual(resources['used_runs'],0)
        self.assertEqual(resources['used_fits'],0)
        self.assertEqual(self.store.one('SELECT * FROM research_grants'),original)

    def test_submission_extension_preserves_explicit_zero_fit_limit(self):
        self.store.execute('UPDATE research_grants SET max_fits=0')
        budget=self.r.resources(self.first)['budget']
        self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],budget['revision'],add_runs=2)
        resources=self.r.resources(self.first)
        self.assertEqual(resources['budget']['max_runs'],6)
        self.assertEqual(resources['budget']['max_fits'],0)

    def test_verified_cloud_results_continue_analysis_roles_without_more_compute(self):
        handoff=self.pending_role();self.cloud('succeeded')
        self.store.execute('UPDATE research_grants SET max_runs=1')
        with patch('lab.budgets.cloud_assessment_ready',return_value=True),patch.object(self.kernel.pool,'submit'):
            self.assertTrue(self.kernel.guard_role_dispatch(self.sid,handoff))
            self.assertFalse(self.kernel.guard_role_dispatch(self.sid,{**handoff,'target':'executor'}))
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM research_finalizations'),[])
        delivery=self.store.one('SELECT * FROM deliveries WHERE id=?',(handoff['delivery_id'],))
        self.assertIsNotNone(delivery)
        self.assertEqual(self.r.resources(self.first)['remaining_runs'],0)
        self.assertEqual(len(self.store.rows('SELECT * FROM company_training')),1)

    def test_budget_can_append_after_continue_review_without_rewriting_it(self):
        review=self.close_round(self.first);original=dict(self.store.one('SELECT * FROM records WHERE id=?',(review['id'],)))
        budget=self.r.resources(self.first)['budget']
        self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],budget['revision'],add_runs=2,add_fits=1)
        self.assertEqual(self.r.resources(self.first)['budget']['max_runs'],6)
        self.assertEqual(self.store.one('SELECT max_runs FROM research_grants')['max_runs'],4)
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(review['id'],)),original)

    def test_budget_cannot_reopen_autonomously_stopped_round(self):
        run=self.execute(self.first,'last-fit-before-stop')
        self.store.execute('UPDATE research_grants SET max_runs=1,max_fits=1')
        self.record('review','预算边界结束',plan_id=self.first['id'],run_ids=[run['run_id']],round_outcome='stop_requested',stop_reason='budget')
        budget=self.r.resources(self.first)['budget']
        with self.assertRaisesRegex(ValueError,'收尾评审'):
            self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],budget['revision'],add_runs=1,add_fits=1)
        self.assertEqual(self.store.rows('SELECT * FROM research_budget_changes'),[])

    def test_auto_budget_adds_one_bounded_batch_and_preserves_manual_pause(self):
        self.cloud('Failed')
        self.store.execute('UPDATE research_grants SET max_runs=1,max_fits=1')
        budget=self.r.resources(self.first)['budget']
        self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],0,auto=True,hard_runs=2,hard_fits=2)
        self.r.pause(self.sid)
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.r.resources(self.first)['budget']['revision'],1)
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.send(self.sid,'继续研究')
        # Clear the fixture's human delivery as if it has been processed, without native inference.
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE session_id=?",(self.sid,))
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid);self.kernel.continuation.tick(self.sid)
        updated=self.r.resources(self.first)
        self.assertEqual((updated['remaining_runs'],updated['remaining_fits']),(1,1))
        self.assertEqual(updated['budget']['source'],'automatic_preapproved')
        self.assertEqual(updated['budget']['revision'],2)
        self.assertEqual(len(self.store.rows('SELECT * FROM company_training')),1)
        with self.assertRaisesRegex(ValueError,'硬上限'):
            self.r.change_budget(self.sid,self.first['id'],budget['grant_id'],2,add_runs=1,auto=True,hard_runs=2,hard_fits=2)

    def test_watch_and_scheduler_share_one_terminal_consumer_even_for_interruption(self):
        self.cloud('submitted')
        company=self.kernel.company
        company.configure({'revision':company.config()['revision'],'lpai_enabled':True})
        item={'uid':'task-fixture','name':'isolated','namespace':company.config()['namespace'],'phase':'Running'}
        proof={'id':'isolated-proof','path':'diagnostic.md','summary':{'signals':[]}}
        with patch.object(company,'lpai',side_effect=lambda *a,**kw:{'item':dict(item)}), \
             patch.object(company,'authority',return_value={'fixture':'same-authority','revision':company.config()['revision']}), \
             patch.object(company.training,'diagnose',return_value=proof), \
             patch.object(company.outputs,'collect',return_value={'state':'read_failed','task_uid':'task-fixture','message':'脚本化证据缺口'}):
            company.watch(self.sid,'task-fixture',True)
            item['phase']='Stopped'
            with patch.object(self.kernel,'send') as send:
                company.poll_watches(self.kernel,clock=10**12)
                send.assert_not_called() # scheduler is the sole automatic consumer
            self.assertEqual(self.store.one('SELECT wake_state FROM company_watches')['wake_state'],'research_scheduler')
            self.assertFalse(self.r.workflow.paused(self.sid))
            with patch.object(self.kernel.pool,'submit'):
                self.kernel.continuation.tick(self.sid)
                company.poll_watches(self.kernel,clock=10**12+1000)
                self.kernel.continuation.tick(self.sid)
            self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)

    def test_restart_waits_without_native_restore_then_restores_original_pending_handoff(self):
        self.pending_role();self.cloud('queued')
        self.store.patch_session(self.sid,thread_id='original-native-thread',status='disconnected')
        def restored(sid):
            self.store.patch_session(sid,status='idle');return self.store.session(sid)
        with patch.object(self.kernel,'restore',side_effect=restored) as restore, \
             patch.object(self.kernel,'resume_roles') as resume:
            self.kernel.continuation.tick(self.sid)
            restore.assert_not_called();resume.assert_not_called()
            self.store.execute("UPDATE company_training SET state='Failed'")
            self.kernel.continuation.tick(self.sid)
            restore.assert_called_once_with(self.sid);resume.assert_called_once_with(self.sid)
        self.assertEqual(self.store.session(self.sid)['thread_id'],'original-native-thread')

    def test_restart_reads_original_unknown_delivery_without_replaying_it(self):
        with patch.object(self.kernel.pool,'submit'):
            delivery=self.kernel.send(self.sid,'原研究请求',origin='user')
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(delivery['id'],))
        self.store.patch_session(self.sid,thread_id='native-thread',status='disconnected')
        self.kernel.continuation.tick(self.sid)
        self.assertTrue(any(method=='thread/read' for method,_ in self.kernel.rpc.calls))
        self.assertFalse(any(method in {'turn/start','thread/start'} for method,_ in self.kernel.rpc.calls))
        self.assertEqual(self.store.one('SELECT state FROM deliveries WHERE id=?',(delivery['id'],))['state'],'unknown')
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)

    def test_missing_watch_repairs_known_submission_with_backoff_without_resubmission(self):
        self.cloud('submitted');company=self.kernel.company
        company.configure({'revision':company.config()['revision'],'lpai_enabled':True})
        item={'uid':'task-fixture','name':'isolated','namespace':company.config()['namespace'],'phase':'Queued'}
        stamp=time.time()
        with patch.object(company,'lpai',side_effect=[TimeoutError('temporary read failure'),{'item':item}]) as read, \
             patch.object(company,'authority',return_value={'fixture':'same-authority'}), \
             patch.object(self.kernel,'send') as send:
            company.poll_watches(self.kernel,clock=stamp)
            self.assertEqual(self.store.one('SELECT state FROM company_watch_repairs')['state'],'waiting')
            company.poll_watches(self.kernel,clock=stamp+30);self.assertEqual(read.call_count,1)
            company.poll_watches(self.kernel,clock=stamp+61)
            self.assertEqual(self.store.one('SELECT state FROM company_watch_repairs')['state'],'attached')
            self.assertEqual(self.store.one('SELECT task_uid FROM company_watches')['task_uid'],'task-fixture')
            self.assertEqual(read.call_count,2);send.assert_not_called()
        self.assertEqual(len(self.store.rows('SELECT * FROM company_training')),1)
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])

    def test_delayed_old_terminal_watch_does_not_duplicate_analysis_after_plan_adoption(self):
        self.cloud('Succeeded');company=self.kernel.company
        company.configure({'revision':company.config()['revision'],'lpai_enabled':True})
        item={'uid':'task-fixture','name':'isolated','namespace':company.config()['namespace'],'phase':'Running'}
        with patch.object(company,'lpai',side_effect=lambda *a,**kw:{'item':dict(item)}), \
             patch.object(company,'authority',return_value={'fixture':'same-authority'}):
            company.watch(self.sid,'task-fixture',True)
            item.update(phase='Succeeded',watch_authority={'fixture':'same-authority'})
            self.store.execute("UPDATE company_watches SET state='Succeeded',observation=?",(json.dumps(item),))
            review=self.record('review','旧任务已经评审',plan_id=self.first['id'],cloud_run_ids=['cloud-run'])
            next_plan=self.next(self.first,review);self.adopt(next_plan)
            with patch.object(self.kernel,'send') as send:
                company.poll_watches(self.kernel,clock=10**12)
                send.assert_not_called()
            self.assertEqual(self.store.one('SELECT wake_state FROM company_watches')['wake_state'],'research_scheduler')

    def test_current_user_message_required_for_interpreted_fork(self):
        with self.assertRaisesRegex(ValueError,'实际用户消息'):
            self.r.autonomy.fork(self.sid,self.first['id'],'不应成立','这是模型自己想的','不能伪造人工',user_delivery_id='invented',origin='interpreted_user_request')
    def test_actual_native_dispatch_unknown_is_not_replayed(self):
        self.kernel.settings.command=lambda *a:['isolated-fake-rpc']
        with patch.object(self.kernel.settings,'verify_channel'):
            self.kernel.ensure('ept');self.kernel.rpc.unknown=True
            self.kernel.continuation.tick(self.sid)
            for _ in range(150):
                if self.store.one("SELECT id FROM deliveries WHERE state='unknown'"):break
                time.sleep(.01)
        self.assertEqual(len([c for c in self.kernel.rpc.calls if c[0]=='turn/start']),1)
        self.assertEqual(self.store.one('SELECT state FROM deliveries')['state'],'unknown')
        self.kernel.continuation.tick(self.sid);self.kernel.continuation.tick(self.sid)
        self.assertEqual(len([c for c in self.kernel.rpc.calls if c[0]=='turn/start']),1)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
    def test_restart_keeps_scope_branch_and_cumulative_fits(self):
        run=self.execute(self.first,'persisted-fit');review=self.close_round(self.first,run);candidate=self.next(self.first,review);self.adopt(candidate)
        before=self.r.autonomy.status(self.sid);directory=self.store.root
        self.kernel.close();self.store.close()
        self.store=Store(directory);self.kernel=Kernel(self.store,FakeRPC);self.r=self.kernel.research
        after=self.r.autonomy.status(self.sid)
        self.assertEqual(after['authority_id'],before['authority_id']);self.assertEqual(after['active_branch'],before['active_branch'])
        self.assertEqual(self.r.status(self.sid)['attempts'],{'runs':1,'fits':1})
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'ready')
    def test_initial_review_can_explicitly_enable_autonomy_and_bad_scope_is_atomic(self):
        pid=self.store.create_project('new-authority')['id'];sid=self.store.create_session(pid,'ept')['id']
        plan=self.store.add_record(sid,{'kind':'plan','title':'新研究','content':'用户确认这份范围'}, {})
        request={'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept','reason':'明确委托范围',
                 'allow_local':True,'max_runs':5,'max_fits':3,'timeout':5,'autonomous':True,'research_scope':{}}
        with self.assertRaises(ValueError):self.r.review(sid,request)
        self.assertEqual(self.store.rows('SELECT * FROM research_reviews WHERE project_id=?',(pid,)),[])
        scope={**self.scope,'frozen_files':[]};self.r.review(sid,{**request,'research_scope':scope})
        self.assertEqual(self.r.autonomy.status(sid)['mode'],'autonomous')

    def test_autonomy_policy_suffix_is_pinned_and_not_shown_as_user_text(self):
        with patch.object(self.kernel.pool,'submit'):
            delivery=self.kernel.send(self.sid,'请围绕现有方案继续。')
        self.store.patch_session(self.sid,thread_id='thread-test')
        raw=self.kernel.sdm.input(self.sid,delivery)[0]['text']
        self.assertIn('当前研究授权策略',raw)
        self.kernel.save_item(self.sid,'turn-test',{'id':'native-user','type':'userMessage','content':[{'type':'text','text':raw}]},'completed')
        messages=self.store.rows("SELECT text FROM messages WHERE role='user'")
        self.assertEqual([m['text'] for m in messages],['请围绕现有方案继续。'])


if __name__=='__main__':unittest.main()
