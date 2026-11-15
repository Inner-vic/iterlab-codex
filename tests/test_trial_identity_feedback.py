"""Wrong IDs fail before platform reads and expose existing exact identities."""
import unittest
from unittest.mock import patch
from tests import test_lpai_recovery as fixtures


class TrialIdentityFeedbackTests(unittest.TestCase):
    setUp=fixtures.RecoveryTests.setUp

    def test_snapshot_names_candidate_identity_despite_historical_parent_declaration(self):
        import json
        before=self.store.one('SELECT * FROM records WHERE id=?',(self.plan['id'],))
        source=json.loads(before['source']);source['plan_id']='historical-parent'
        encoded=json.dumps(source)
        self.store.execute('UPDATE records SET source=? WHERE id=?',(encoded,self.plan['id']))
        snapshot=self.research.snapshot(self.sid,self.plan['id'])
        self.assertEqual(snapshot['plan_id'],self.plan['id'])
        self.assertEqual(snapshot['record_id'],self.plan['id'])
        self.assertEqual(snapshot['relationships']['plan_id'],'historical-parent')
        self.assertEqual(self.store.one('SELECT source FROM records WHERE id=?',(self.plan['id'],))['source'],encoded)

    def test_wrong_plan_or_invented_trial_reuses_available_identity_without_cloud_reads(self):
        other=self.store.add_record(self.sid,{'kind':'plan','title':'other plan','content':'Other experiment ownership','evidence':[]},{})
        wrong=self.store.add_record(self.sid,{'kind':'trial','title':'other trial','content':'No cross-plan reassignment','plan_id':other['id'],'evidence':[]},{})
        request={'plan_id':self.plan['id'],'task_name':'identity-fixture','reason':'verify identity','predecessor_uid':'',
            'diagnostic_id':'','change_summary':'','validation':'','task_information':{'problem_statement':'fixture',
            'solution_approach':'fixture','expected_result':'fixture','expected_duration':1,'expected_duration_desc':'fixture'}}
        calls=list(self.calls);sdk=list(self.sdk_calls);records=self.store.rows('SELECT id FROM records')
        for value in [wrong['id'],'invented-trial']:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,self.trial['id']):
                self.company.training.prepare(self.sid,{**request,'trial_id':value})
        self.assertEqual(self.calls,calls);self.assertEqual(self.sdk_calls,sdk)
        self.assertEqual(self.store.rows('SELECT id FROM records'),records)
        self.assertFalse(self.store.rows('SELECT * FROM company_training'))

    def test_saved_receipt_names_exact_trial_identity_and_plan_for_preparation(self):
        from lab.record_receipt import receipt
        saved=self.store.one('SELECT * FROM records WHERE id=?',(self.trial['id'],))
        result=receipt(saved)
        self.assertEqual(result['record_id'],result['trial_id'])
        self.assertEqual(result['trial_id'],saved['id']);self.assertEqual(result['plan_id'],self.plan['id'])
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(saved['id'],)),saved)
        class ReachedPlatformRead(Exception):pass
        request={'plan_id':result['plan_id'],'trial_id':result['trial_id'],'component_uid':'component-1',
            'task_name':'receipt-fixture','reason':'check saved identity','predecessor_uid':'','diagnostic_id':'','change_summary':'','validation':'',
            'task_information':{'problem_statement':'fixture','solution_approach':'fixture','expected_result':'fixture','expected_duration':1,'expected_duration_desc':'fixture'}}
        with patch.object(self.company.training,'sdk',side_effect=ReachedPlatformRead):
            with self.assertRaises(ReachedPlatformRead):self.company.training.prepare(self.sid,request)
        self.assertFalse(self.store.rows('SELECT * FROM company_training'))
