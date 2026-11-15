import unittest
from unittest.mock import patch
from tests import test_core as core_fixture
from lab.research import plan_digest
from tests import test_roles as role_fixture


class ResearchResumeTests(unittest.TestCase):
    setUp=core_fixture.CoreTests.setUp
    tearDown=core_fixture.CoreTests.tearDown
    record=core_fixture.CoreTests.record

    def approve(self):
        self.plan=self.record()
        sid=self.session['id']
        self.kernel.research.review(sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'accept','reason':'fixture approval','allow_local':True,'max_runs':4,'max_fits':4,'timeout':180})
        self.store.patch_session(sid,thread_id='original-thread',status='idle',active_turn=None)
        self.kernel.research.workflow.pause(sid,'fixture pause')

    def test_explicit_resume_is_idempotent_control_not_budget_or_user_message(self):
        self.approve();sid=self.session['id']
        before=self.store.rows('SELECT * FROM research_grants')
        with patch.object(self.kernel.pool,'submit') as dispatch:
            first=self.kernel.resume_research(sid,'original-resume-1','用户明确继续原任务',True)
            again=self.kernel.resume_research(sid,'original-resume-1','用户明确继续原任务',True)
            self.assertEqual(first['id'],again['id']);self.assertEqual(dispatch.call_count,1)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertFalse(self.kernel.research.workflow.paused(sid))
        self.assertEqual(self.store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?',(first['id'],))['origin'],'review')
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
        self.assertEqual(self.store.session(sid)['thread_id'],'original-thread')

    def test_unknown_or_unconfirmed_resume_has_no_side_effects(self):
        self.approve();sid=self.session['id']
        for status,confirmed in [('idle',False),('unknown',True),('running',True)]:
            self.store.patch_session(sid,status=status)
            with self.assertRaises(ValueError):self.kernel.resume_research(sid,'blocked-resume-1','continue',confirmed)
        self.assertFalse(self.store.rows("SELECT id FROM deliveries WHERE id LIKE 'research-resume:%'"))
        self.assertTrue(self.kernel.research.workflow.paused(sid))


class OriginalRoleResumeTests(unittest.TestCase):
    setUp=role_fixture.RoleTests.setUp
    tearDown=role_fixture.RoleTests.tearDown
    record=role_fixture.RoleTests.record
    activate=role_fixture.RoleTests.activate
    handoff=role_fixture.RoleTests.handoff
    approve=role_fixture.RoleTests.approve

    def interrupted_executor(self):
        from lab.store import now
        self.approve();trial=self.record('trial',plan_id=self.plan['id'])
        h=self.handoff('executor',trial);original=self.activate(h)
        self.store.execute("UPDATE research_role_handoffs SET state='dispatched' WHERE session_id=?",(self.sid,))
        source=self.store.one('SELECT * FROM deliveries WHERE id=?',(original['delivery_id'],))
        self.store.patch_session(self.sid,thread_id='original-thread',status='idle',active_turn=None)
        self.store.execute('INSERT INTO delivery_turn_observations VALUES(?,?,?,?,?,?)',
            (self.sid,'original-thread',source['turn_id'],'interrupted','fixture exact native observation',now()))
        self.r.workflow.pause(self.sid,'fixture maintenance')
        return original

    def test_explicit_continue_preserves_known_interrupted_executor_stage(self):
        original=self.interrupted_executor()
        before=self.store.rows('SELECT * FROM research_grants')
        with patch.object(self.kernel.pool,'submit'):
            delivery=self.kernel.resume_research(self.sid,'resume-original-executor','user explicitly continues',True)
        route=self.roles.route(self.sid,delivery,self.kernel.settings.value,self.catalog)
        self.assertEqual(route['role'],'executor');self.assertEqual(route['handoff_id'],original['handoff_id'])
        self.assertEqual(self.roles.execution_stage(route['handoff_id']),'execute')
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))

    def test_changed_original_terminal_fact_blocks_route_replay(self):
        self.interrupted_executor()
        with patch.object(self.kernel.pool,'submit'):
            delivery=self.kernel.resume_research(self.sid,'resume-fact-checked','user explicitly continues',True)
        self.store.execute("UPDATE delivery_turn_observations SET status='running' WHERE session_id=?",(self.sid,))
        with self.assertRaisesRegex(ValueError,'恢复事实已变化'):
            self.roles.route(self.sid,delivery,self.kernel.settings.value,self.catalog)
