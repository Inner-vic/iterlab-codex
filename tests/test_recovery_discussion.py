"""Accepted repair input and system recovery share an exact durable reservation."""
import json,unittest
from unittest.mock import patch
from lab import recovery
from lab.store import now
from tests import test_initialization_recovery as fixtures


class RepairDiscussionTests(unittest.TestCase):
    setUp=fixtures.InitializationRecoveryTests.setUp
    tearDown=fixtures.InitializationRecoveryTests.tearDown
    record=fixtures.InitializationRecoveryTests.record
    candidate=fixtures.InitializationRecoveryTests.candidate
    critic=fixtures.InitializationRecoveryTests.critic
    adopt=fixtures.InitializationRecoveryTests.adopt
    block=fixtures.InitializationRecoveryTests.block
    save_diagnostic=fixtures.InitializationRecoveryTests.save_diagnostic

    def input(self):
        stamp=now();id='accepted-repair-input'
        self.store.patch_session(self.sid,thread_id='native-thread',status='running',active_turn='repair-turn')
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            (id,self.sid,'fixture repair discussion','queue','accepted','repair-turn',None,'2000-01-01',stamp))
        self.store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',(id,'user',stamp))
        self.store.execute('INSERT INTO delivery_requests VALUES(?,?,?,?,?,?,?,?,?)',
            (id,'fixture-rpc','turn/start','native-thread','fixture-generation',None,'repair-turn',1,stamp))
        return id

    def test_accepted_after_report_is_registered_once_without_new_delivery_and_still_needs_critic(self):
        self.block();id=self.input();plan=self.candidate()
        deliveries=self.store.rows('SELECT * FROM deliveries');grants=self.store.rows('SELECT * FROM research_grants')
        request=recovery.reserve_discussion(self.store,self.sid,id,plan,'critic','execute')
        self.assertIsNotNone(request)
        self.assertEqual(request['delivery_id'],id)
        self.assertIsNotNone(recovery.chain(self.store,self.sid,id))
        self.assertIsNone(recovery.reserve_discussion(self.store,self.sid,id,plan,'critic','execute'))
        with patch.object(self.kernel,'send') as send:
            recovery.dispatch(self.kernel.continuation,self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),deliveries)
        with self.assertRaisesRegex(ValueError,'Critic'):self.adopt(plan)
        self.critic(plan);self.adopt(plan)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)

    def test_user_stop_or_unknown_input_never_gets_a_reservation(self):
        self.block();id=self.input();plan=self.candidate()
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(id,))
        self.assertIsNone(recovery.reserve_discussion(self.store,self.sid,id,plan,'critic','execute'))
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(id,))
        self.r.workflow.pause(self.sid,actor='user')
        self.assertIsNone(recovery.reserve_discussion(self.store,self.sid,id,plan,'critic','execute'))
        self.assertFalse(self.store.rows('SELECT * FROM research_recoveries'))

    def test_missing_reference_can_be_reviewed_without_execution_or_new_grant(self):
        self.block();id=self.input();plan=self.candidate()
        incomplete={**plan,'evidence':'[]'}
        before=self.store.rows('SELECT * FROM research_grants')
        self.assertIsNotNone(recovery.reserve_discussion(self.store,self.sid,id,incomplete,'critic','execute'))
        with self.assertRaisesRegex(ValueError,'Critic'):self.adopt(plan)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)

    def test_execute_or_changed_scope_is_not_implicitly_authorized(self):
        self.block();id=self.input();plan=self.candidate()
        self.assertIsNone(recovery.reserve_discussion(self.store,self.sid,id,plan,'executor','execute'))
        changed={**plan,'source':json.dumps({**json.loads(plan['source']),'scope_proposal':{'objective':'different scope'}})}
        self.assertIsNone(recovery.reserve_discussion(self.store,self.sid,id,changed,'critic','execute'))
        self.assertFalse(self.store.rows('SELECT * FROM research_recoveries'))

    def test_accepted_input_can_join_existing_recovery_without_replacing_original(self):
        self.block();plan=self.candidate()
        with patch.object(self.kernel,'send'):
            recovery.dispatch(self.kernel.continuation,self.sid)
        original=self.store.rows('SELECT * FROM research_recoveries')
        id=self.input();grants=self.store.rows('SELECT * FROM research_grants')
        row=recovery.reserve_discussion(self.store,self.sid,id,plan,'critic','execute')
        self.assertEqual(row['id'],original[0]['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_recoveries'),original)
        self.assertEqual(recovery.chain(self.store,self.sid,id)['id'],row['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertIsNone(recovery.reserve_discussion(self.store,self.sid,id,plan,'critic','execute'))
        self.r.workflow.pause(self.sid,actor='user')
        self.assertIsNone(recovery.chain(self.store,self.sid,id))
