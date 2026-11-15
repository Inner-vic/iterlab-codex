"""Official pre-training failure recovery; scripted fixtures, never GPU training."""
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import test_recovery as fixtures
from lab import recovery
from lab.store import now


class InitializationRecoveryTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    tearDown=fixtures.RecoveryTests.tearDown
    record=fixtures.RecoveryTests.record
    candidate=fixtures.RecoveryTests.candidate
    critic=fixtures.RecoveryTests.critic
    adopt=fixtures.RecoveryTests.adopt

    def block(self):
        # Only this isolated fixture changes its route. No real platform exists.
        self.kernel.settings.save({'agent_roles':{'version':1,'models':dict.fromkeys(['planner','executor','generator','critic'],'fixture-model')}})
        self.store.execute("UPDATE research_grants SET execution='lpai'")
        stamp=now()
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('init-run',self.sid,self.pid,self.first['id'],'trial-fixture','component-fixture','task-fixture',
             '{}','failed','{}',stamp,stamp))
        self.evidence={'task':{'uid':'task-fixture','phase':'failed','flows':[
            {'name':'dataPreparation','phase':'failed'},
            {'name':'task','phase':'','startedat':0,'scheduledat':0,'jobinitat':0}]},'training_logs':{}}
        self.path=self.workspace/'init-diagnostic.json'
        self.save_diagnostic()
        self.review=self.store.add_record(self.sid,{'kind':'review','title':'initialization blocked',
            'content':'Official initialization did not reach training; no scientific inference.',
            'evidence':['protocol.md'],'plan_id':self.first['id'],'run_ids':[],
            'round_outcome':'stop_requested','stop_reason':'blocked'},
            {'origin':'codex-native-tool','cloud_run_ids':['init-run']})

    def save_diagnostic(self):
        self.path.write_text(json.dumps(self.evidence),encoding='utf-8')
        self.store.execute('INSERT OR REPLACE INTO company_diagnostics VALUES(?,?,?,?,?,?,?,?)',
            ('init-diagnostic',self.sid,'task-fixture','failed','init-diagnostic.json',
             hashlib.sha256(self.path.read_bytes()).hexdigest(),'{}',now()))

    def reserve(self):
        with patch.object(self.kernel.pool,'submit'):
            self.assertTrue(recovery.dispatch(self.kernel.continuation,self.sid))
        self.request=self.store.one('SELECT * FROM research_recoveries')
        self.assertIsNotNone(self.request)
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(self.request['delivery_id'],))

    def test_critic_reviewed_repair_keeps_grant_old_review_and_failed_attempt(self):
        self.block();self.reserve();plan=self.candidate()
        with self.assertRaisesRegex(ValueError,'Critic'):self.adopt(plan)
        self.critic(plan)
        grants=self.store.rows('SELECT * FROM research_grants')
        attempts=self.store.rows('SELECT * FROM company_training')
        original_review=self.review['source']
        self.adopt(plan)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertEqual(self.store.rows('SELECT * FROM company_training'),attempts)
        self.assertEqual(self.store.one('SELECT source FROM records WHERE id=?',(self.review['id'],))['source'],original_review)
        self.assertEqual(self.store.one('SELECT admitted_plan_id FROM research_recoveries')['admitted_plan_id'],plan['id'])
        self.assertFalse(self.store.rows('SELECT * FROM research_finalizations'))

    def test_delivery_is_once_even_if_original_recovery_request_is_unknown(self):
        self.block();self.reserve()
        self.kernel.check_automatic_delivery(self.sid,self.request['delivery_id'])
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(self.request['delivery_id'],))
        before=self.store.rows('SELECT * FROM deliveries')
        with patch.object(self.kernel.pool,'submit'):
            with self.assertRaisesRegex(ValueError,'未知投递'):
                recovery.dispatch(self.kernel.continuation,self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),before)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_recoveries')),1)

    def test_changed_missing_or_foreign_official_bytes_do_not_establish_recovery(self):
        self.block();self.assertIsNotNone(recovery.context(self.store,self.pid))
        self.path.write_text('corrupt',encoding='utf-8')
        self.assertIsNone(recovery.context(self.store,self.pid))

        self.path.unlink()
        self.assertIsNone(recovery.context(self.store,self.pid))
        self.evidence['task']['uid']='different-task';self.save_diagnostic()
        self.assertIsNone(recovery.context(self.store,self.pid))

    def test_absent_platform_evidence_table_fails_closed(self):
        self.block();self.store.execute('DROP TABLE company_diagnostics')
        self.assertIsNone(recovery.context(self.store,self.pid))

    def test_real_training_or_a_pending_original_task_cannot_use_init_recovery(self):
        self.block()
        self.evidence['task']['flows'][1].update(phase='failed',startedat=123)
        self.save_diagnostic();self.assertIsNone(recovery.context(self.store,self.pid))
        self.evidence['task']['flows'][1].update(phase='',startedat=0)
        self.evidence['training_logs']={'worker':{'lines':[{'text':'training started'}]}}
        self.save_diagnostic();self.assertIsNone(recovery.context(self.store,self.pid))
        self.evidence['training_logs']={};self.save_diagnostic()
        self.store.execute("UPDATE company_training SET state='unknown' WHERE id='init-run'")
        self.assertIsNone(recovery.context(self.store,self.pid))

    def test_user_stop_archive_and_disabled_recovery_still_win(self):
        self.block();self.r.workflow.pause(self.sid)
        self.assertIsNone(recovery.context(self.store,self.pid))
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?',(self.sid,))
        self.assertIsNone(recovery.context(self.store,self.pid))
        self.store.execute('DELETE FROM research_user_stops WHERE session_id=?',(self.sid,))
        self.store.execute('UPDATE research_recovery_authorities SET enabled=0')
        self.assertIsNone(recovery.context(self.store,self.pid))


if __name__=='__main__':unittest.main()
