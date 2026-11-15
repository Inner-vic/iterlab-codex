"""Automatic read-back must never turn an uncertain write into a retry."""
from datetime import datetime,timezone
import json
import threading
import unittest
from unittest.mock import patch

from lab import rounds
from tests import test_lpai_recovery as fixtures


class UnknownComponentTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp
    prepared=fixtures.RecoveryTests.prepared
    grant=fixtures.RecoveryTests.grant
    training_row=fixtures.RecoveryTests.training_row

    def original(self):
        prepared=self.prepared();self.grant();self.sdk_unknown=True
        id=prepared['submission_id']
        result=self.company.perform(self.sid,{'action':'lpai_submit','submission_id':id},'unknown-once',True)
        self.assertEqual(result['state'],'unknown');self.assertEqual(self.sdk_calls.count('submit'),1)
        return id

    def reader(self,marker=True):
        def read(args,**kwargs):
            return {'status':'ok','data':{'uid':'new-immutable-task','name':'retry-fixture','namespace':'vc-chassis','project_uid':'component-1',
                'status':'queued','create_timestamp':{'time':datetime.now(timezone.utc).isoformat()},
                'expected_duration_desc':self.submission_echo if marker else 'different request'}}
        return read

    def test_original_echo_recovers_uid_and_wrapper_without_rewriting_unknown_history(self):
        id=self.original();original=self.store.one('SELECT * FROM company_operations WHERE id=?',('unknown-once',))
        with patch.object(self.company,'_run',side_effect=self.reader()):self.company.repair_unknown(100)
        row=self.training_row(id);self.assertEqual(row['task_uid'],'new-immutable-task');self.assertEqual(row['state'],'submitted')
        self.assertEqual(self.sdk_calls.count('submit'),1);self.assertEqual(self.store.one('SELECT * FROM company_operations WHERE id=?',('unknown-once',)),original)
        self.assertIsNone(rounds.pending_platform_operation(self.store,self.project['id']))
        self.assertEqual(rounds.pending_cloud(self.store,self.project['id'])['id'],id)
        self.store.execute("UPDATE company_training SET state='succeeded' WHERE id=?",(id,))
        self.assertIsNone(rounds.pending_platform_operation(self.store,self.project['id']))
        with patch.object(self.company,'_run',side_effect=AssertionError('known UID not re-searched')):self.company.repair_unknown(999)
        self.assertEqual(self.sdk_calls.count('submit'),1)

    def test_name_alone_or_wrong_marker_cannot_attach_or_resubmit(self):
        id=self.original()
        with patch.object(self.company,'_run',side_effect=self.reader(False)):self.company.repair_unknown(100)
        self.assertIsNone(self.training_row(id)['task_uid']);self.assertEqual(self.training_row(id)['state'],'unknown')
        self.assertEqual(self.store.one('SELECT state FROM company_unknown_reads')['state'],'waiting')
        self.assertEqual(self.store.rows('SELECT * FROM company_watches'),[]);self.assertEqual(self.sdk_calls.count('submit'),1)
        with patch.object(self.company,'_run',side_effect=AssertionError('wait for persisted backoff')):self.company.repair_unknown(101)

    def test_crash_after_sdk_write_recovers_requested_identity_after_restart(self):
        from lab.company import Company
        prepared=self.prepared();self.grant();id=prepared['submission_id'];sdk=self.company.training.sdk
        def interrupted(operation,**params):
            response=sdk(operation,**params)
            if operation=='submit':raise SystemExit('fixture process exit before local receipt')
            return response
        with patch.object(self.company.training,'sdk',side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.company.perform(self.sid,{'action':'lpai_submit','submission_id':id},'crashed-submit',True)
        self.assertEqual(self.training_row(id)['state'],'requested')
        original_identity=self.store.one('SELECT * FROM company_training_identity WHERE submission_id=?',(id,))
        self.assertTrue(original_identity['requested_at'])
        wrapper=self.store.one('SELECT * FROM company_operations WHERE id=?',('crashed-submit',))
        restarted=Company(self.store,runner=self.company.runner,locator=lambda name:name)
        self.addCleanup(restarted.close)
        with patch.object(restarted,'_run',side_effect=self.reader()),patch.object(restarted.training,'sdk',side_effect=AssertionError('never resubmit')):
            restarted.repair_unknown(100)
            restarted.repair_unknown(999)
        self.assertEqual(self.training_row(id)['task_uid'],'new-immutable-task')
        self.assertEqual(self.training_row(id)['state'],'submitted')
        self.assertEqual(self.store.one('SELECT * FROM company_operations WHERE id=?',('crashed-submit',)),wrapper)
        self.assertEqual(self.store.one('SELECT requested_at FROM company_training_identity WHERE submission_id=?',(id,))['requested_at'],original_identity['requested_at'])
        self.assertEqual(self.sdk_calls.count('submit'),1)
        self.assertEqual(len(self.store.rows('SELECT * FROM company_watches')),1)
        self.assertIsNone(rounds.pending_platform_operation(self.store,self.project['id']))

    def test_live_submission_owner_prevents_readback_without_blocking_monitor(self):
        self.original();entered=threading.Event();release=threading.Event()
        def owner():
            with self.company.lock:
                entered.set();release.wait(5)
        worker=threading.Thread(target=owner);worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with patch.object(self.company,'_run',side_effect=AssertionError('still in SDK call')):
                self.company.repair_unknown(100)
            self.assertEqual(self.store.rows('SELECT * FROM company_unknown_reads'),[])
        finally:release.set();worker.join(5)
        self.assertFalse(worker.is_alive())

    def test_requested_without_committed_request_time_does_not_guess_or_submit(self):
        prepared=self.prepared();id=prepared['submission_id']
        self.store.execute("UPDATE company_training SET state='requested' WHERE id=?",(id,))
        with patch.object(self.company,'_run',side_effect=AssertionError('no actual request identity')):
            self.company.repair_unknown(100)
        self.assertEqual(self.training_row(id)['state'],'requested')
        check=self.store.one('SELECT * FROM company_unknown_reads')
        self.assertEqual(check['state'],'context_pending');self.assertEqual(check['attempts'],0)
        self.assertNotIn('submit',self.sdk_calls)

    def test_stale_old_contexts_do_not_starve_newer_unknown_submission(self):
        from lab.store import now
        id=self.original()
        for name in ['removed-1','removed-2']:
            self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (name,self.sid,self.project['id'],self.plan['id'],self.trial['id'],'component-1',None,'{}','unknown',None,now(),now()))
        # Model the two stale requests preceding the recoverable request.
        self.store.execute('UPDATE company_training SET rowid=rowid+100 WHERE id=?',(id,))
        original=self.company.training._original_context
        def context(sid,row,*args,**kw):
            if row['id'].startswith('removed-'):raise ValueError('old binding no longer available')
            return original(sid,row,*args,**kw)
        with patch.object(self.company.training,'_original_context',side_effect=context),patch.object(self.company,'_run',side_effect=self.reader()) as read:
            self.company.repair_unknown(100);read.assert_not_called()
            self.company.repair_unknown(101)
        self.assertEqual(self.training_row(id)['task_uid'],'new-immutable-task')
        self.assertEqual(self.sdk_calls.count('submit'),1)
        for row in self.store.rows("SELECT * FROM company_unknown_reads WHERE submission_id LIKE 'removed-%'"):
            self.assertEqual(row['next_check'],700);self.assertEqual(row['attempts'],0)

    def test_platform_name_postfix_uses_bounded_candidates_then_exact_uid_echo(self):
        id=self.original();base=self.reader();calls=[]
        def read(args,**kwargs):
            calls.append(args)
            if '--name' in args:raise ValueError('exact name absent')
            if 'list' in args:return {'status':'ok','data':{'items':[{'uid':'new-immutable-task'}]}}
            return base(args,**kwargs)
        with patch.object(self.company,'_run',side_effect=read):self.company.repair_unknown(100)
        self.assertEqual(self.training_row(id)['task_uid'],'new-immutable-task');self.assertEqual(self.sdk_calls.count('submit'),1)
        listed=next(a for a in calls if 'list' in a)
        self.assertIn('--name-like',listed);self.assertEqual(listed[listed.index('--page-size')+1],'5')

    def test_multiple_original_echo_candidates_remain_unknown_without_guessing(self):
        id=self.original();base=self.reader()
        def read(args,**kwargs):
            if '--name' in args:raise ValueError('exact name absent')
            if 'list' in args:return {'status':'ok','data':{'items':[{'uid':'candidate-1'},{'uid':'candidate-2'}]}}
            result=base(args,**kwargs);result['data']['uid']=args[args.index('--uid')+1];return result
        with patch.object(self.company,'_run',side_effect=read):self.company.repair_unknown(100)
        self.assertEqual(self.training_row(id)['state'],'unknown');self.assertIsNone(self.training_row(id)['task_uid'])
        self.assertEqual(self.sdk_calls.count('submit'),1);self.assertEqual(self.store.rows('SELECT * FROM company_watches'),[])


if __name__=='__main__':unittest.main()
