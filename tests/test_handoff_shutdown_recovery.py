"""Committed role continuation after maintenance, no real model/cloud/compute."""
import unittest
from unittest.mock import Mock,patch
from lab import handoff_recovery
from lab.research import plan_digest
from lab.store import now
from tests import test_roles as fixtures


class ShutdownHandoffTests(unittest.TestCase):
    setUp=fixtures.RoleTests.setUp
    tearDown=fixtures.RoleTests.tearDown
    record=fixtures.RoleTests.record
    activate=fixtures.RoleTests.activate
    handoff=fixtures.RoleTests.handoff
    approve=fixtures.RoleTests.approve

    def interrupted(self):
        self.store.patch_session(self.sid,thread_id='native-thread')
        self.r.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),
            {'objective':'fixture control','evaluation':'fixed protocol','data_boundary':'no real data',
             'allowed_changes':'no training in fixture','frozen_files':[]},'isolated delegated approval')
        self.approve()
        # activate() is an isolated route fixture, not the actual dispatch API.
        # Its preceding successful role transitions are already consumed here.
        self.store.execute("UPDATE research_role_handoffs SET state='dispatched'")
        h=self.handoff('executor')
        self.source=self.store.one('SELECT * FROM deliveries WHERE id=?',(h['source_delivery'],))
        self.store.execute('INSERT INTO delivery_requests VALUES(?,?,?,?,?,?,?,?,?)',
            (self.source['id'],'fixture-request','turn/start','native-thread','fixture-generation',None,self.turn,1,self.source['created_at']))
        stamp=now()
        self.store.execute('INSERT INTO delivery_interrupt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('fixture-shutdown',self.sid,self.source['id'],'native-thread',self.turn,'acknowledged',handoff_recovery.SHUTDOWN_REASON,None,'fixture-generation',stamp,stamp))
        self.store.execute('INSERT INTO delivery_turn_observations VALUES(?,?,?,?,?,?)',
            (self.sid,'native-thread',self.turn,'interrupted','native-terminal-fixture',stamp))
        self.store.event(self.sid,'turn.interrupt_requested',{'reason':handoff_recovery.SHUTDOWN_REASON,
            'origin':'service_shutdown','thread_id':'native-thread','turn_ids':[self.turn]})
        self.store.patch_session(self.sid,status='interrupted',active_turn=None)
        return h

    def test_fresh_terminal_read_dispatches_original_handoff_once_without_changing_grant(self):
        h=self.interrupted();grants=self.store.rows('SELECT * FROM research_grants')
        calls=[]
        def send(sid,text,**kw):
            calls.append(kw);return {'id':kw['delivery_id'],'state':'queued'}
        turns=[{'id':self.turn,'status':'interrupted','items':[]}]
        self.kernel.delivery_control.reconcile(self.sid,'native-thread',turns)
        self.roles.reconcile(self.sid,turns,send,self.kernel.settings.value)
        self.assertEqual([x['delivery_id'] for x in calls],[h['delivery_id']],
            {'session':self.store.session(self.sid)['status'],'handoff':self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(h['id'],)),
             'progress':self.roles.progress.status(self.sid,self.kernel.settings.value),
             'events':self.store.rows('SELECT kind,payload FROM events ORDER BY seq DESC LIMIT 3')})
        self.roles.reconcile(self.sid,turns,send,self.kernel.settings.value)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.store.session(self.sid)['status'],'idle')
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertEqual(self.store.one('SELECT status FROM delivery_turn_observations WHERE turn_id=?',(self.turn,))['status'],'interrupted')
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='research.handoff_recovered_after_shutdown'")),1)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))

    def test_wrong_or_unconfirmed_shutdown_and_native_identity_do_not_release(self):
        self.interrupted()
        for field,value in [('reason','user stop'),('state','unknown'),('thread_id','foreign-thread'),('created_at','2000-01-01')]:
            original=self.store.one('SELECT * FROM delivery_interrupt_intents')[''+field]
            self.store.execute('UPDATE delivery_interrupt_intents SET '+field+'=?',(value,))
            self.assertIsNone(handoff_recovery.pending(self.store,self.sid))
            self.store.execute('UPDATE delivery_interrupt_intents SET '+field+'=?',(original,))
        self.assertFalse(handoff_recovery.release(self.store,self.sid,[{'id':'wrong-turn','status':'interrupted'}]))
        self.assertFalse(handoff_recovery.release(self.store,self.sid,[{'id':self.turn,'status':'inProgress'}]))
        self.assertEqual(self.store.session(self.sid)['status'],'interrupted')

    def test_user_pause_disabled_authority_and_unknown_delivery_still_block(self):
        self.interrupted()
        self.store.execute("UPDATE research_autonomy_policies SET mode='manual'")
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))
        self.store.execute("UPDATE research_autonomy_policies SET mode='autonomous'")
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(self.source['id'],))
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(self.source['id'],))
        self.r.workflow.pause(self.sid,actor='user')
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?',(self.sid,))
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))

    def test_later_human_input_cannot_be_hidden_by_service_shutdown(self):
        self.interrupted();stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            ('later-human',self.sid,'change intent','steer','accepted',self.turn,None,stamp,stamp))
        self.store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',('later-human','user',stamp))
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))

    def test_background_ticks_read_native_state_before_any_dispatch(self):
        self.interrupted()
        with patch.object(self.kernel,'restore') as restore:
            self.kernel.continuation.tick(self.sid)
        restore.assert_called_once_with(self.sid)
        self.assertEqual(self.store.session(self.sid)['status'],'interrupted')

    def test_shutdown_keeps_queued_work_and_user_pause_is_distinct(self):
        self.interrupted();stamp=now()
        for enabled in (0,1):
            self.store.execute('INSERT INTO company_watches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('watch-'+str(enabled),self.sid,'fixture','task-'+str(enabled),'fixture','running','{}',enabled,0,'none',stamp))
        watches=self.store.rows('SELECT * FROM company_watches ORDER BY id')
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            ('queued-work',self.sid,'existing input','queue','queued',None,None,stamp,stamp))
        self.kernel.delivery_control.interrupt(self.sid,handoff_recovery.SHUTDOWN_REASON,origin='service_shutdown')
        self.assertEqual(self.store.one('SELECT state FROM deliveries WHERE id=?',('queued-work',))['state'],'queued')
        self.assertFalse(self.r.workflow.paused(self.sid))
        self.assertEqual(self.store.rows('SELECT * FROM company_watches ORDER BY id'),watches)
        self.kernel.delivery_control.interrupt(self.sid)
        self.assertEqual(self.store.one('SELECT state FROM deliveries WHERE id=?',('queued-work',))['state'],'cancelled')
        self.assertTrue(self.r.workflow.paused(self.sid))
        self.assertFalse(self.store.rows('SELECT id FROM company_watches WHERE auto_resume<>0'))
        self.assertEqual([r['state'] for r in self.store.rows('SELECT state FROM company_watches')],['running','running'])
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))

    def test_legacy_shutdown_pause_requires_exact_receipt_and_fresh_native_terminal(self):
        self.interrupted()
        self.store.execute("DELETE FROM events WHERE kind='turn.interrupt_requested'")
        self.store.event(self.sid,'turn.interrupt_requested',{'reason':handoff_recovery.SHUTDOWN_REASON})
        stamp=self.store.one('SELECT created_at FROM delivery_interrupt_intents')['created_at']
        self.store.execute('INSERT OR REPLACE INTO research_controls VALUES(?,1,?,?)',
            (self.sid,handoff_recovery.SHUTDOWN_REASON,stamp))
        self.assertTrue(handoff_recovery.pending(self.store,self.sid)['legacy_pause'])
        self.assertFalse(handoff_recovery.release(self.store,self.sid,[{'id':self.turn,'status':'inProgress'}]))
        self.assertTrue(handoff_recovery.release(self.store,self.sid,[{'id':self.turn,'status':'interrupted'}]))
        self.assertFalse(self.r.workflow.paused(self.sid))

    def test_shutdown_text_does_not_override_explicit_user_origin(self):
        self.interrupted()
        self.store.event(self.sid,'turn.interrupt_requested',{'reason':handoff_recovery.SHUTDOWN_REASON,
            'origin':'user','thread_id':'native-thread','turn_ids':[self.turn]})
        self.assertIsNone(handoff_recovery.pending(self.store,self.sid))
