"""Actionable errors without accepting a different numerical contract."""
import json
import unittest
from tests import test_comparison as fixtures
from lab import evaluation_contract
from lab.research import plan_digest


class ContractFeedbackTests(unittest.TestCase):
    setUp=fixtures.ComparisonTests.setUp
    tearDown=fixtures.ComparisonTests.tearDown
    make_plan=fixtures.ComparisonTests.make_plan
    approve=fixtures.ComparisonTests.approve

    def previous(self):
        self.approve(autonomous=True)
        review=self.store.add_record(self.sid,{'kind':'review','title':'fixture protocol review','content':'Original protocol remains fixed.',
            'plan_id':self.plan['id'],'run_ids':[],'round_outcome':'continue'},{'origin':'isolated-fixture'})
        return {'previous_plan_id':self.plan['id'],'review_record_id':review['id'],'revision_reason':'fixture evidence update'}

    def test_snapshot_reference_or_archive_is_not_a_new_specification(self):
        parent=evaluation_contract.load(self.store,self.plan)
        previous=self.previous()
        before=self.store.rows('SELECT id FROM records')
        for wrong in [parent['reference'],{k:v for k,v in parent.items() if k!='reference'},json.dumps(parent['spec'])]:
            with self.subTest(kind=type(wrong).__name__),self.assertRaisesRegex(ValueError,'省略evaluation_contract'):
                self.make_plan(wrong,**previous)
        self.assertEqual(self.store.rows('SELECT id FROM records'),before)

    def test_unchanged_successor_inherits_exact_contract_without_retyping(self):
        previous=self.previous()
        successor=self.store.add_record(self.sid,{'kind':'plan','title':'same protocol successor','content':'Only the research experiment changes.',
            **previous,'evidence':['protocol.json']},{'origin':'isolated-fixture'})
        self.assertEqual(json.loads(successor['source'])['evaluation_contract'],json.loads(self.plan['source'])['evaluation_contract'])
        self.assertEqual(evaluation_contract.load(self.store,successor)['spec'],self.spec)

    def test_freeze_paths_reject_digest_objects_with_field_level_guidance(self):
        with self.assertRaisesRegex(ValueError,'frozen_files须为相对路径字符串数组'):
            self.make_plan(self.spec,scope_proposal={'frozen_files':[{'path':'protocol.json','sha256':'a'*64}]})

    def test_saved_plan_body_roundtrip_does_not_duplicate_program_appendix(self):
        previous=self.previous()
        successor=self.store.add_record(self.sid,{'kind':'plan','title':'metadata repair','content':self.plan['content'],
            **previous,'evidence':['protocol.json']},{'origin':'isolated-fixture'})
        self.assertEqual(successor['content'],self.plan['content'])
        self.assertEqual(json.loads(successor['source'])['evaluation_contract'],json.loads(self.plan['source'])['evaluation_contract'])

    def test_snapshot_exposes_current_digest_after_version_assignment(self):
        original=self.research.snapshot(self.sid,self.plan['id'])['plan_digest']
        self.assertEqual(original,plan_digest(self.plan))
        # Isolated unapproved record models the host assigning a release number.
        self.store.execute('UPDATE records SET version=? WHERE id=?',('1.0.99',self.plan['id']))
        current=self.research.snapshot(self.sid,self.plan['id'])['plan_digest']
        self.assertNotEqual(current,original)
        self.assertEqual(current,plan_digest({**self.plan,'version':'1.0.99'}))
