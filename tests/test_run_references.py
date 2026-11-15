"""Actual record/round guards with deterministic identities, no model/cloud/fit."""
import json,unittest
from lab.company import Company
from lab.store import now
from tests import test_rounds as fixtures


class RunReferenceTests(unittest.TestCase):
    setUp=fixtures.RoundTests.setUp
    tearDown=fixtures.RoundTests.tearDown
    record=fixtures.RoundTests.record
    approve=fixtures.RoundTests.approve

    def cloud(self,id='cloud',uid='official-task',state='failed',pid=None):
        Company(self.store)
        trial=self.record('trial','fixture experiment',plan_id=self.first['id'])
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (id,self.sid,pid or self.pid,self.first['id'],trial['id'],'component',uid,'{}',state,None,now(),now()))

    def test_cloud_in_general_run_list_is_canonical_and_round_coverage_remains_strict(self):
        self.cloud();self.cloud('second','task-two')
        with self.assertRaisesRegex(ValueError,'不能遗漏'):
            self.record('review','missing attempt',plan_id=self.first['id'],run_ids=['official-task'])
        review=self.record('review','all attempts',plan_id=self.first['id'],run_ids=['official-task','second'])
        source=json.loads(review['source'])
        self.assertEqual(source['cloud_run_ids'],['cloud','second'])
        self.assertEqual(source['run_reference_resolution'][0]['declared_id'],'official-task')
        self.assertEqual(len(source['indexed_cloud_runs']),2)
        self.assertFalse(self.store.rows('SELECT * FROM research_record_runs'))

    def test_explicit_cloud_task_uid_is_resolved_without_mutating_declared_input(self):
        self.cloud()
        fields={'cloud_run_ids':['official-task']}
        result=self.record('result','known task',**fields)
        self.assertEqual(fields,{'cloud_run_ids':['official-task']})
        self.assertEqual(json.loads(result['source'])['cloud_run_ids'],['cloud'])

    def test_unknown_foreign_or_prepared_identity_never_becomes_run_evidence(self):
        self.cloud(state='prepared')
        foreign=self.store.create_project('foreign')['id'];self.cloud('foreign','foreign-task',pid=foreign)
        for ref in ('cloud','official-task','foreign','foreign-task','invented'):
            with self.subTest(ref=ref),self.assertRaisesRegex(ValueError,'本项目'):
                self.record('result','invalid',run_ids=[ref])
        self.assertFalse(self.store.rows("SELECT id FROM records WHERE kind='result'"))

    def test_duplicate_platform_uid_requires_unambiguous_submission_identity(self):
        self.cloud();self.cloud('second','official-task')
        with self.assertRaisesRegex(ValueError,'歧义'):
            self.record('result','ambiguous',run_ids=['official-task'])
        result=self.record('result','exact original',cloud_run_ids=['cloud'])
        self.assertEqual(json.loads(result['source'])['cloud_run_ids'],['cloud'])
