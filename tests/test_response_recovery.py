"""Known response limits may resume offline prepare once, never execution."""
import unittest
from unittest.mock import patch
from tests import test_recovery as fixtures
from lab import response_recovery,recovery
from lab.research import plan_digest
from lab.store import dumps,now


class ResponseRecoveryTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    tearDown=fixtures.RecoveryTests.tearDown
    begin=fixtures.RecoveryTests.begin
    candidate=fixtures.RecoveryTests.candidate
    record=fixtures.RecoveryTests.record

    def prepare_failure(self,message='Incomplete response returned, reason: max_output_tokens',stage='prepare'):
        self.begin()
        with patch.object(self.kernel.pool,'submit'):self.kernel.send(self.sid,'新范围准备，待审核后计算',delivery_id='new-request')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id='new-request'")
        plan=self.candidate(scope_proposal={'objective':'new scope'})
        stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('prepare-delivery',self.sid,'prepare','queue','accepted','failed-prepare',None,stamp,stamp))
        self.store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',('prepare-delivery','background',stamp))
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('prepare-handoff',self.sid,'new-request','executor',plan['id'],plan_digest(plan),plan['id'],'handoff','offline code','prepare-delivery','dispatched',stamp))
        self.store.execute('INSERT INTO research_role_execution_stages VALUES(?,?)',('prepare-handoff',stage))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',
            ('prepare-delivery',self.sid,'executor','fixture-model',dumps(self.kernel.settings.value['agent_roles']),'prepare-handoff',stamp))
        self.store.event(self.sid,'turn/completed',{'turn':{'id':'failed-prepare','status':'failed','error':{'message':message}}})
        self.store.patch_session(self.sid,status='failed',active_turn=None)
        return plan

    def test_bounded_known_failure_keeps_original_role_and_never_replays(self):
        plan=self.prepare_failure();before=self.store.rows('SELECT * FROM research_grants')
        with patch.object(self.kernel.pool,'submit'):
            self.assertTrue(response_recovery.dispatch(self.kernel.continuation,self.sid))
            self.assertTrue(response_recovery.dispatch(self.kernel.continuation,self.sid))
        rows=self.store.rows('SELECT * FROM research_output_recoveries');self.assertEqual(len(rows),1)
        delivery=self.store.one('SELECT * FROM deliveries WHERE id=?',(rows[0]['delivery_id'],))
        self.kernel.check_automatic_delivery(self.sid,delivery['id'])
        route=self.kernel.roles.route(self.sid,delivery,self.kernel.settings.value,[{'model':'fixture-model'}])
        self.assertEqual(route['role'],'executor');self.assertEqual(self.kernel.roles.execution_stage(route['handoff_id']),'prepare')
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
        self.assertEqual(self.store.one("SELECT turn_id FROM deliveries WHERE id='prepare-delivery'")['turn_id'],'failed-prepare')
        # A second definitive failure does not get another recovery identity.
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='failed-again' WHERE id=?",(delivery['id'],))
        self.store.event(self.sid,'turn/completed',{'turn':{'id':'failed-again','status':'failed','error':{'message':'max_output_tokens'}}})
        with patch.object(self.kernel.pool,'submit'):response_recovery.dispatch(self.kernel.continuation,self.sid)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_output_recoveries')),1)
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status WHERE session_id=?',(self.sid,))['state'],'blocked')

    def test_execute_and_quota_errors_never_use_output_recovery(self):
        self.prepare_failure(stage='execute')
        self.assertFalse(response_recovery.dispatch(self.kernel.continuation,self.sid))
        self.assertFalse(self.store.rows('SELECT * FROM research_output_recoveries'))

    def test_pause_and_unknown_writes_block_known_output_recovery(self):
        self.prepare_failure()
        self.r.workflow.pause(self.sid)
        self.assertFalse(response_recovery.dispatch(self.kernel.continuation,self.sid))
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?',(self.sid,))
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id='prepare-delivery'")
        self.assertFalse(response_recovery.dispatch(self.kernel.continuation,self.sid))
        self.assertFalse(self.store.rows('SELECT * FROM research_output_recoveries'))

    def test_quota_failure_remains_blocked_without_switching_model(self):
        self.prepare_failure('当前套餐额度已用尽')
        before=dict(self.kernel.settings.value)
        self.assertFalse(response_recovery.dispatch(self.kernel.continuation,self.sid))
        self.assertFalse(self.store.rows('SELECT * FROM research_output_recoveries'))
        self.assertEqual(self.kernel.settings.value,before)


if __name__=='__main__':unittest.main()
