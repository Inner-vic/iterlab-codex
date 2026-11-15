"""Evidence-limited reporting and irreversible automatic finalization boundaries."""
import json
import unittest
from unittest.mock import patch
from tests import test_autonomy as fixtures


class ClosureBoundaryTests(unittest.TestCase):
    setUp=fixtures.AutonomyTests.setUp
    tearDown=fixtures.AutonomyTests.tearDown
    record=fixtures.AutonomyTests.record
    execute=fixtures.AutonomyTests.execute
    pending_role=fixtures.AutonomyTests.pending_role

    def missing_run(self):
        (self.workspace/'train.py').write_text('pass\n',encoding='utf-8')
        run=self.execute(self.first,'original-missing-output')
        self.assertEqual(run['state'],'artifacts_missing')
        return run

    def tick(self):
        with patch.object(self.kernel.pool,'submit'):self.kernel.continuation.tick(self.sid)

    def test_explicit_limited_closure_is_visible_without_clearing_evidence(self):
        run=self.missing_run()
        review=self.record('review','证据受限收束',plan_id=self.first['id'],run_ids=[run['run_id']],
                           round_outcome='stop_requested',stop_reason='blocked')
        value=self.r.workflow.publish(self.sid)
        self.assertEqual(value['state'],'assessment_complete')
        self.assertEqual(value['subject_id'],review['id'])
        self.assertEqual(value['evidence_pending']['run_id'],run['run_id'])
        self.assertFalse(value['target_verified'])
        self.assertIsNotNone(self.store.evidence.blocking(self.pid))
        with self.assertRaises(ValueError):self.execute(self.first,'cannot-restart')

    def test_missing_original_output_gets_one_limited_report_not_polling(self):
        run=self.missing_run();self.tick();self.tick()
        delivery=self.store.one('SELECT * FROM deliveries')
        self.assertIsNotNone(delivery)
        self.assertTrue(delivery['id'].startswith('research-finalize:'))
        self.assertEqual(self.kernel.continuation.finalization(delivery['id'])['reason'],'blocked')
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)
        self.assertIsNotNone(self.store.evidence.blocking(self.pid))
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)
        self.assertEqual(self.store.one('SELECT state FROM research_runs')['state'],run['state'])

    def test_transient_collection_waits_but_bounded_failed_recovery_reports(self):
        self.execute(self.first,'original-complete')
        self.store.execute("UPDATE run_evidence SET state='partial',analysis_ready=0,attempts=1")
        with patch.object(self.store.evidence,'recover_local'):self.tick()
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.store.execute('UPDATE run_evidence SET attempts=3')
        with patch.object(self.store.evidence,'recover_local'):self.tick()
        self.assertEqual(self.store.one('SELECT reason FROM research_finalizations')['reason'],'blocked')

    def test_missing_evidence_does_not_hide_unknown_original_run(self):
        run=self.missing_run()
        self.store.execute("UPDATE research_runs SET state='unknown',receipt=NULL WHERE id=?",(run['run_id'],))
        self.tick()
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        self.assertEqual(self.store.rows('SELECT * FROM research_finalizations'),[])

    def test_evidence_gap_cannot_be_described_as_target_achieved(self):
        run=self.missing_run()
        with self.assertRaisesRegex(ValueError,'证据'):
            self.record('review','不能编造达标',plan_id=self.first['id'],run_ids=[run['run_id']],
                        round_outcome='stop_requested',stop_reason='target_achieved')

    def test_manual_confirmation_of_limited_stop_does_not_certify_missing_results(self):
        run=self.missing_run();self.r.autonomy.mode(self.sid,'manual','人工处置证据受限结束')
        review=self.record('review','受限结束建议',plan_id=self.first['id'],run_ids=[run['run_id']],
                           round_outcome='stop_requested',stop_reason='blocked')
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'waiting_stop_review')
        self.r.review_stop(self.sid,review['id'],'accept_stop','确认受限退出，不认证目标')
        self.assertEqual(self.r.workflow.publish(self.sid)['state'],'settled')
        self.assertIsNotNone(self.store.evidence.blocking(self.pid))

    def test_finished_report_without_stop_review_cannot_revive_pending_role(self):
        handoff=self.pending_role()
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}),patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        original=self.store.one("SELECT * FROM deliveries WHERE id LIKE 'research-finalize:%'")
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='reported' WHERE id=?",(original['id'],))
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':False}),patch.object(self.kernel,'send') as send:
            self.kernel.resume_roles(self.sid)
        send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(handoff['id'],))['state'],'pending')

    def test_queued_old_handoff_is_blocked_before_native_request(self):
        handoff=self.pending_role()
        from lab.autonomy import policy,active
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.send(self.sid,'已排队的旧交接',delivery_id=handoff['delivery_id'],origin='background')
            self.kernel.continuation.finalize_once(self.sid,policy(self.store,self.pid),active(self.store,self.pid),'no_progress')
        with self.assertRaisesRegex(ValueError,'收尾'):
            self.kernel.check_automatic_delivery(self.sid,handoff['delivery_id'])
        with patch.object(self.kernel,'_deliver') as deliver:
            self.kernel.drain(self.sid)
        deliver.assert_not_called()

    def test_plain_input_after_finalization_does_not_authorize_original_compute(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        original=self.store.one('SELECT * FROM deliveries')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='reported' WHERE id=?",(original['id'],))
        with patch.object(self.kernel.pool,'submit'):self.kernel.send(self.sid,'讨论下一步，但没有追加授权')
        with self.assertRaisesRegex(ValueError,'收尾'):self.execute(self.first,'no-revival-through-dialogue')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_preexisting_successor_cannot_be_adopted_after_reporting_reservation(self):
        review=self.record('review','已有继续评审',plan_id=self.first['id'],run_ids=[])
        from lab.autonomy import policy,active
        from lab.research import plan_digest
        scope=self.r.autonomy.status(self.sid)
        candidate=self.record('plan','已保存的后继',previous_plan_id=self.first['id'],review_record_id=review['id'],
                              authority_id=scope['authority_id'],scope_digest=scope['scope_digest'])
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.finalize_once(self.sid,policy(self.store,self.pid),active(self.store,self.pid),'no_progress')
        with self.assertRaisesRegex(ValueError,'收尾'):
            self.r.autonomy.adopt(self.sid,candidate['id'],plan_digest(candidate),'普通讨论不能解除原收尾')
        self.assertEqual(active(self.store,self.pid)['head_plan_id'],self.first['id'])

    def test_other_background_wakes_cannot_bypass_reporting_or_closure(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        with patch.object(self.kernel.pool,'submit'):
            event=self.kernel.send(self.sid,'原平台终态事件',delivery_id='lpai-terminal:fixture',origin='background')
        with self.assertRaisesRegex(ValueError,'收尾'):self.kernel.check_automatic_delivery(self.sid,event['id'])
        self.record('review','无进展结束',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested',stop_reason='no_progress')
        with self.assertRaises(ValueError):self.kernel.check_automatic_delivery(self.sid,event['id'])

    def test_legacy_autonomous_stop_cannot_be_replaced_with_continue(self):
        review=self.record('review','历史自主结束',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested',stop_reason='no_progress')
        source=json.loads(review['source']);source.pop('closure_mode');source.pop('stop_reason')
        self.store.execute('UPDATE records SET source=? WHERE id=?',(json.dumps(source),review['id']))
        with self.assertRaisesRegex(ValueError,'已自主收束'):
            self.record('review','不能重开历史结束',plan_id=self.first['id'],run_ids=[],round_outcome='continue')

    def test_unknown_start_without_turn_id_keeps_tool_restrictions(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        original=self.store.one('SELECT * FROM deliveries');self.store.patch_session(self.sid,thread_id='native-thread')
        self.kernel.delivery_control.begin(original,'turn/start',{'threadId':'native-thread'},'fixture-generation')
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(original['id'],))
        with self.assertRaisesRegex(ValueError,'收尾'):
            self.kernel.check_finalization_tool(self.sid,None,'research_write',{})

    def test_finalization_review_reason_and_plan_remain_pinned(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        original=self.store.one('SELECT * FROM deliveries')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='final' WHERE id=?",(original['id'],))
        for fields in [{'plan_id':self.first['id'],'stop_reason':'budget'},
                       {'plan_id':'other-plan','stop_reason':'no_progress'},
                       {'plan_id':self.first['id'],'stop_reason':'target_achieved'}]:
            with self.subTest(fields=fields),self.assertRaisesRegex(ValueError,'收尾'):
                self.kernel.check_finalization_tool(self.sid,'final','research_record',
                    {'kind':'review','round_outcome':'stop_requested',**fields})

    def test_generic_stalled_continuation_gets_one_readonly_report(self):
        self.kernel.settings.value['agent_roles']=None
        self.tick();original=self.store.one('SELECT * FROM deliveries')
        self.assertTrue(original['id'].startswith('research-continue:'))
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='stalled' WHERE id=?",(original['id'],))
        self.tick();self.tick()
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),2)
        self.assertEqual(self.store.one('SELECT reason FROM research_finalizations')['reason'],'no_progress')
        self.assertEqual(len(self.store.rows('SELECT * FROM research_continuations')),1)

    def test_report_dispatch_rechecks_other_unresolved_work(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        original=self.store.one('SELECT * FROM deliveries')
        from lab.store import now,dumps
        request={'plan_id':self.first['id'],'purpose':'fit'}
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('unknown-run','unknown-op',self.pid,self.sid,self.first['id'],'fixture-grant','hash',dumps(request),
             'unknown','runs/unknown-run',None,now(),now()))
        with self.assertRaises(ValueError):self.kernel.check_automatic_delivery(self.sid,original['id'])
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)

    def test_changed_frozen_plan_evidence_can_only_receive_limited_reporting(self):
        (self.workspace/'protocol.md').write_text('changed after original authorization',encoding='utf-8')
        self.tick();self.tick()
        original=self.store.one("SELECT * FROM deliveries WHERE id LIKE 'research-finalize:%'")
        self.assertIsNotNone(original)
        self.assertEqual(self.kernel.continuation.finalization(original['id'])['reason'],'blocked')
        self.kernel.check_automatic_delivery(self.sid,original['id'])
        with self.assertRaisesRegex(ValueError,'收尾'):self.execute(self.first,'changed-protocol-not-a-new-grant')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),1)

    def test_original_frozen_evidence_change_cannot_be_declared_qualitative_success(self):
        (self.workspace/'protocol.md').write_text('changed frozen protocol',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'证据'):
            self.record('review','不可认证改变的评价',plan_id=self.first['id'],run_ids=[],
                        round_outcome='stop_requested',stop_reason='target_achieved')

    def test_reporting_reservation_is_visible_not_ready_for_compute(self):
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        original=self.store.one("SELECT * FROM deliveries WHERE id LIKE 'research-finalize:%'")
        value=self.r.workflow.publish(self.sid)
        self.assertEqual(value['state'],'finalizing')
        self.assertEqual(value['finalization']['delivery_id'],original['id'])
        self.assertEqual(value['finalization']['plan_id'],self.first['id'])
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='reported' WHERE id=?",(original['id'],))
        value=self.r.workflow.publish(self.sid)
        self.assertEqual(value['state'],'blocked')
        self.assertIn('收尾',value['label'])
        self.assertEqual(value['finalization']['delivery_state'],'accepted')

    def test_snapshot_exposes_exact_execution_dependencies_and_scientific_basis(self):
        predecessor=self.record('trial','前置准备',plan_id=self.first['id'])
        basis=self.record('decision','研究判断依据',plan_id=self.first['id'])
        trial=self.record('trial','后继提案',plan_id=self.first['id'],basis_record_id=basis['id'],
                          depends_on_trial_ids=[predecessor['id']])
        before=self.store.one('SELECT * FROM records WHERE id=?',(trial['id'],))
        value=self.r.snapshot(self.sid,trial['id'])
        self.assertEqual(value['relationships']['plan_id'],self.first['id'])
        self.assertEqual(value['relationships']['basis_record_id'],basis['id'])
        self.assertEqual(value['relationships']['depends_on_trial_ids'],[predecessor['id']])
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(trial['id'],)),before)

    def test_unrecognized_nonterminal_cloud_phase_cannot_be_hidden_by_stop(self):
        from lab.store import dumps,now
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('scheduling-cloud',self.sid,self.pid,self.first['id'],'fixture-trial','fixture-component',
             'fixture-task',dumps({'purpose':'fit'}),'Scheduling',None,now(),now()))
        with self.assertRaisesRegex(ValueError,'云任务'):
            self.record('review','不能掩盖排队',plan_id=self.first['id'],run_ids=[],cloud_run_ids=['scheduling-cloud'],
                        round_outcome='stop_requested',stop_reason='blocked')
        with self.assertRaisesRegex(ValueError,'云任务'):self.execute(self.first,'cannot-overlap-cloud')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.assertIn(self.r.workflow.publish(self.sid)['state'],{'blocked','waiting_external'})

    def test_rejected_stale_wake_reschedules_known_unsent_human_message(self):
        self.kernel.settings.value['agent_roles']=None
        with patch.object(self.kernel.roles.progress,'status',return_value={'blocked':True}):self.tick()
        self.r.autonomy.mode(self.sid,'manual','人工讨论，旧收尾不重放')
        with patch.object(self.kernel.pool,'submit') as submit:
            human=self.kernel.send(self.sid,'普通讨论，不启动计算',delivery_id='human-after-stale-wake')
            submit.reset_mock()
            self.kernel.drain(self.sid)
            submit.assert_called_with(self.kernel.drain,self.sid)
        self.assertEqual(self.store.one('SELECT state FROM deliveries WHERE id=?',(human['id'],))['state'],'queued')
        self.assertEqual(self.store.one("SELECT state FROM deliveries WHERE id LIKE 'research-finalize:%'")['state'],'rejected')
        self.assertIsNone(self.kernel.rpc)

    def test_analysis_record_does_not_disable_later_archive_integrity_check(self):
        run=self.execute(self.first,'already-analyzed')
        self.record('result','已分析记录',run_ids=[run['run_id']])
        manifest=self.store.evidence.verify('local',run['run_id'])
        entry=next(item for item in manifest['files'] if item['name']=='metrics.json')
        (self.workspace/'research-evidence/local'/run['run_id']/entry['object']).write_bytes(b'tampered')
        self.assertIsNotNone(self.store.evidence.blocking(self.pid,verify_ready=True))
        self.assertEqual(self.store.evidence.summary('local',run['run_id'])['state'],'corrupt')


if __name__=='__main__':unittest.main()
