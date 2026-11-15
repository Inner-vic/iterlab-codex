"""Goal-loop closure and resource boundaries; no real model/platform calls."""
import json
from unittest.mock import patch
import unittest
from tests import test_autonomy as fixtures
from lab import rounds


class LoopClosureTests(unittest.TestCase):
    setUp=fixtures.AutonomyTests.setUp
    tearDown=fixtures.AutonomyTests.tearDown
    record=fixtures.AutonomyTests.record
    execute=fixtures.AutonomyTests.execute
    pending_role=fixtures.AutonomyTests.pending_role

    def stopped(self,reason='budget'):
        return self.record('review','自主研究收束',plan_id=self.first['id'],run_ids=[],
                           round_outcome='stop_requested',stop_reason=reason)

    def test_new_autonomous_stop_requires_explicit_reason(self):
        with self.assertRaisesRegex(ValueError,'结束原因'):
            self.record('review','缺少原因',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested')

    def test_autonomous_closure_reports_reason_without_human_approval(self):
        review=self.stopped()
        value=self.r.workflow.publish(self.sid)
        self.assertEqual(value['state'],'assessment_complete')
        self.assertEqual(value['stop_reason'],'budget')
        self.assertFalse(value['target_verified'])
        self.assertEqual(self.store.rows('SELECT * FROM research_round_decisions'),[])
        self.assertEqual(rounds.state(self.store,self.pid)['review_record_id'],review['id'])

    def test_closed_round_cannot_dispatch_pending_role_or_reopen_with_plain_input(self):
        h=self.pending_role()
        self.stopped()
        with patch.object(self.kernel,'send') as send:
            self.kernel.resume_roles(self.sid)
            self.kernel.continuation.tick(self.sid)
        send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(h['id'],))['state'],'pending')
        with patch.object(self.kernel.pool,'submit'):
            delivery=self.kernel.send(self.sid,'只讨论已有结论，不扩大预算')
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'assessment_complete')
        with self.assertRaises(ValueError):
            self.execute(self.first,'closed-cannot-run')

    def test_no_progress_allows_only_one_readonly_finalization(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'无新增研究事实'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
            self.kernel.continuation.tick(self.sid)
        rows=self.store.rows('SELECT * FROM deliveries')
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['id'].startswith('research-finalize:'))
        self.assertIn('不得新增计算',rows[0]['text'])
        # Pending/unknown finalization cannot be retried with a new identity.
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(rows[0]['id'],))
        with patch.object(self.kernel,'restore'),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)

    def test_exhausted_compute_preserves_verified_result_analysis(self):
        run=self.execute(self.first,'only-fit')
        self.store.execute('UPDATE research_grants SET max_fits=1')
        remaining=self.r.resources(self.first)
        self.assertEqual((remaining['remaining_runs'],remaining['remaining_fits']),(3,0))
        self.assertFalse(remaining['computation_exhausted'])
        self.assertTrue(remaining['fit_exhausted'])
        self.store.execute('UPDATE research_grants SET max_runs=1')
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        delivery=self.store.one('SELECT * FROM deliveries')
        # Verified local/SSH evidence, like a cloud archive, must still reach
        # analysis at the compute ceiling. A reporting-only closure here would
        # prevent the result/decision phase that this grant already authorizes.
        self.assertTrue(delivery['id'].startswith('research-continue:'))
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'awaiting_assessment')
        self.assertTrue(self.r.resources(self.first)['computation_exhausted'])
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)
        with self.assertRaisesRegex(ValueError,'累计运行／拟合边界'):
            self.execute(self.first,'must-not-compute-again')

    def test_finalization_rejects_write_run_handoff_and_next_plan_without_roles(self):
        self.kernel.settings.value['agent_roles']=None
        self.store.execute('UPDATE research_grants SET max_runs=1')
        self.execute(self.first,'one')
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        delivery=self.store.one('SELECT * FROM deliveries')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='final-turn' WHERE id=?",(delivery['id'],))
        self.store.patch_session(self.sid,status='running',active_turn='final-turn')
        for name,args in [('research_run',{}),('research_write',{}),('research_adopt',{}),('company_action',{}),
                          ('research_handoff',{}),('research_record',{'kind':'plan'}),
                          ('research_record',{'kind':'review','round_outcome':'continue'})]:
            with self.subTest(tool=name),self.assertRaisesRegex(ValueError,'收尾'):
                self.kernel.check_finalization_tool(self.sid,'final-turn',name,args)
        self.kernel.check_finalization_tool(self.sid,'final-turn','research_status',{})
        with self.assertRaisesRegex(ValueError,'预算暂停只盘点结果'):
            self.kernel.check_finalization_tool(self.sid,'final-turn','research_record',{'kind':'review','plan_id':self.first['id'],'round_outcome':'stop_requested','stop_reason':'budget'})
        self.kernel.check_finalization_tool(self.sid,'final-turn','research_record',{'kind':'result','plan_id':self.first['id']})

    def test_pause_and_external_wait_do_not_spend_finalization_turn(self):
        self.r.pause(self.sid)
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.r.autonomy.mode(self.sid,'autonomous','明确恢复，不扩额度')
        with patch.object(self.r.workflow,'external_pending',return_value=True),patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_legacy_stop_read_does_not_rewrite_missing_reason(self):
        review=self.stopped()
        source=json.loads(review['source']);source.pop('stop_reason')
        self.store.execute('UPDATE records SET source=? WHERE id=?',(json.dumps(source),review['id']))
        before=self.store.one('SELECT * FROM records WHERE id=?',(review['id'],))
        value=self.r.workflow.publish(self.sid)
        self.assertEqual(value['state'],'assessment_complete')
        self.assertEqual(value['stop_reason'],'legacy_unspecified')
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(review['id'],)),before)

    def test_steered_human_input_does_not_remove_finalization_permissions(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one('SELECT * FROM deliveries')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='final' WHERE id=?",(original['id'],))
        self.store.patch_session(self.sid,status='running',active_turn='final')
        with patch.object(self.kernel.pool,'submit'):
            steering=self.kernel.send(self.sid,'先说明预算情况，不要计算',mode='steer')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='final' WHERE id=?",(steering['id'],))
        with self.assertRaisesRegex(ValueError,'收尾'):
            self.kernel.check_finalization_tool(self.sid,'final','research_run',{})
        self.kernel.check_finalization_tool(self.sid,'final','research_snapshot',{})

    def test_callback_before_start_receipt_stays_readonly(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one('SELECT * FROM deliveries')
        self.store.patch_session(self.sid,thread_id='native-thread')
        self.kernel.delivery_control.begin(original,'turn/start',{'threadId':'native-thread'},'fixture-generation')
        with self.assertRaisesRegex(ValueError,'收尾'):
            self.kernel.check_finalization_tool(self.sid,'early-native-turn','research_write',{})

    def test_closed_round_cannot_be_reopened_by_replacing_review(self):
        self.stopped()
        with self.assertRaisesRegex(ValueError,'已自主收束'):
            self.record('review','尝试重新开轮',plan_id=self.first['id'],run_ids=[],round_outcome='continue')
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'assessment_complete')

    def test_completed_finalization_cannot_resume_generic_chain_after_guard_changes(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one('SELECT * FROM deliveries')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='reported' WHERE id=?",(original['id'],))
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':False}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)
        self.assertEqual(self.store.rows('SELECT * FROM research_continuations'),[])

    def test_autonomous_closure_keeps_mode_after_manual_takeover(self):
        self.stopped()
        self.r.autonomy.mode(self.sid,'manual','仅接管讨论，不重启旧方案')
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'assessment_complete')

    def test_finalization_is_revalidated_before_native_dispatch(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one('SELECT * FROM deliveries')
        self.kernel.settings.value['profile']='changed-profile'
        with self.assertRaises(ValueError):self.kernel.check_automatic_delivery(self.sid,original['id'])
        self.assertEqual(self.store.one('SELECT state FROM deliveries')['state'],'queued')

    def test_native_permission_expansion_is_denied_in_readonly_finalization(self):
        from tests.test_core import FakeRPC
        self.kernel.settings.value['agent_roles']=None
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True,'reason':'停滞'}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one('SELECT * FROM deliveries')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='final' WHERE id=?",(original['id'],))
        self.store.patch_session(self.sid,thread_id='native-thread',status='running',active_turn='final')
        peer=FakeRPC([],self.store.root,lambda *a:None,lambda *a:None,lambda *a:None)
        for method in ['item/permissions/requestApproval','item/fileChange/requestApproval','item/commandExecution/requestApproval']:
            self.kernel.on_request({'id':method,'method':method,'params':{'threadId':'native-thread','turnId':'final'}},peer)
            self.assertIsNotNone(peer.replies[-1][2])
        self.assertEqual(self.store.rows('SELECT * FROM approvals'),[])


if __name__=='__main__':unittest.main()
