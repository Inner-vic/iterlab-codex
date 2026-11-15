"""Initial confirmation must not be starved by a second model review."""
from unittest.mock import patch
from tests import test_roles as role_fixture
from lab.approval_frontier import initial_confirmation
from lab.research import plan_digest


import unittest
class ApprovalFrontierTests(unittest.TestCase):
    setUp=role_fixture.RoleTests.setUp
    tearDown=role_fixture.RoleTests.tearDown
    record=role_fixture.RoleTests.record
    activate=role_fixture.RoleTests.activate
    handoff=role_fixture.RoleTests.handoff

    def candidate(self):
        project=self.store.create_project('unapproved initial')
        self.pid=project['id'];self.sid=self.store.create_session(self.pid,'codex')['id']
        self.plan=self.record('plan',scope_proposal={'objective':'A small initial CPU experiment'})
        self.activate();self.activate(self.handoff('critic'))
        decision=self.record('decision',plan_id=self.plan['id'])
        self.handoff('planner',decision,'approve')
        self.store.execute("UPDATE research_role_handoffs SET state='dispatched' WHERE session_id=?",(self.sid,))
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        return self.plan

    def test_primary_critic_leaves_initial_confirmation_idle_instead_of_spawning_inquiry(self):
        plan=self.candidate();grants=self.store.rows('SELECT * FROM research_grants')
        frontier=initial_confirmation(self.store,self.sid,self.kernel.settings.value)
        self.assertEqual(frontier['plan_id'],plan['id']);self.assertFalse(frontier['execution_authorized'])
        with patch.object(self.roles.inquiries,'advance',return_value=True) as advance:
            self.roles.dispatch(self.sid,self.kernel.send,self.kernel.settings.value)
            advance.assert_not_called()
        self.assertIsNone(self.store.session(self.sid)['active_turn'])
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_rejection_model_change_or_changed_plan_does_not_inherit_old_review(self):
        self.candidate()
        other={**self.kernel.settings.value,'agent_roles':{'version':1,'models':{'critic':'other'}}}
        self.assertIsNone(initial_confirmation(self.store,self.sid,other))
        self.store.execute('UPDATE records SET content=? WHERE id=?',('Changed candidate',self.plan['id']))
        self.assertIsNone(initial_confirmation(self.store,self.sid,self.kernel.settings.value))

    def test_formal_rejection_releases_hold_without_grant_or_automatic_approval(self):
        self.candidate()
        self.r.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
                               'decision':'reject','reason':'Initial human feedback'})
        self.assertIsNone(initial_confirmation(self.store,self.sid,self.kernel.settings.value))

    def test_existing_grant_keeps_normal_scheduling_available(self):
        self.assertIsNone(initial_confirmation(self.store,self.sid,self.kernel.settings.value))
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        with patch.object(self.roles.inquiries,'advance',return_value=True) as advance:
            self.roles.dispatch(self.sid,self.kernel.send,self.kernel.settings.value)
            advance.assert_called_once()

    def test_a_decision_label_alone_is_not_primary_critic_proof(self):
        project=self.store.create_project('no primary review')
        self.pid=project['id'];self.sid=self.store.create_session(self.pid,'codex')['id']
        self.plan=self.record('plan',scope_proposal={'objective':'Initial experiment'})
        self.record('decision',plan_id=self.plan['id'],content='approve')
        self.assertIsNone(initial_confirmation(self.store,self.sid,self.kernel.settings.value))

    def test_reconcile_completed_original_turn_can_use_confirmation_guard(self):
        self.candidate()
        turn='completed-original-turn'
        self.store.execute('UPDATE deliveries SET turn_id=? WHERE id=(SELECT id FROM deliveries WHERE session_id=? ORDER BY rowid DESC LIMIT 1)',(turn,self.sid))
        with patch.object(self.roles.inquiries,'advance',return_value=True) as advance:
            self.roles.reconcile(self.sid,[{'id':turn,'status':'completed'}],self.kernel.send,self.kernel.settings.value)
            advance.assert_not_called()
        self.assertIsNone(self.store.session(self.sid)['active_turn'])
