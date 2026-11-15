"""Actual record/contract/archive paths with small numerical fixtures; no model."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

from lab.store import Store, dumps, now
from lab.research import Research, plan_digest
from lab import evaluation_contract, study_integrity as integrity
from tests.test_comparison import specification, observations

ROOT=Path(__file__).resolve().parents[1]


class StudyIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'.evidence/tests'/uuid.uuid4().hex
        self.store=Store(self.root);self.research=Research(self.store)
        self.pid=self.store.create_project('study-integrity-fixture')['id']
        self.sid=self.store.create_session(self.pid,'ept')['id']
        self.workspace=Path(self.store.session(self.sid)['workspace'])
        (self.workspace/'protocol.json').write_text('{"split":"validation","threshold":0.5}',encoding='utf-8')
        (self.workspace/'model.py').write_text('# Frozen candidate implementation identity\n',encoding='utf-8')
        self.plan=self.record('plan',evaluation_contract=specification(),evidence=['protocol.json','model.py'])

    def tearDown(self):
        self.research.close();self.store.close()
        assert self.root.resolve().is_relative_to((ROOT/'.evidence/tests').resolve())
        shutil.rmtree(self.root)

    def record(self,kind,**fields):
        return self.store.add_record(self.sid,{'kind':kind,'title':'Actual engineering fixture',
            'content':'Evidence is numerical fixture data; not a real research success.',**fields},{'origin':'isolated-integrity-test'})

    def approve(self):
        return self.research.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'accept','reason':'Isolated engineering checks only','execution':'local','allow_local':True,
            'python':sys.executable,'max_runs':10,'max_fits':0,'timeout':3})

    def run_fixture(self,good=True,rows=None,compiled=True,include_candidate=True):
        rid=uuid.uuid4().hex;directory=self.workspace/'runs'/rid;directory.mkdir(parents=True)
        refs=json.loads(self.plan['evidence'])
        inputs={}
        for ref in refs:
            if not include_candidate and ref['path']=='model.py':continue
            if not compiled and ref['path'].startswith('research-contracts/'):continue
            target=directory/ref['path'];target.parent.mkdir(parents=True,exist_ok=True)
            body=(self.workspace/ref['path']).read_bytes();target.write_bytes(body)
            inputs[ref['path']]={'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
        (directory/'input-manifest.json').write_text(dumps(inputs),encoding='utf-8')
        body=json.dumps({'results':observations(good) if rows is None else rows}).encode()
        (directory/'metrics.json').write_bytes(body)
        for name in ['stdout.log','stderr.log']:(directory/name).write_text('',encoding='utf-8')
        request={'plan_id':self.plan['id'],'purpose':'evaluation','outputs':['metrics.json']}
        receipt={'run_id':rid,'plan_id':self.plan['id'],'plan_version':self.plan['version'],'state':'completed',
            'inputs':inputs,'outputs':[{'path':'runs/'+rid+'/metrics.json','sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}],
            'directory':'runs/'+rid,'error':None,'exit_code':0,'trial_id':None,'missing_outputs':[]}
        (directory/'receipt.json').write_text(dumps(receipt),encoding='utf-8')
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (rid,'fixture:'+rid,self.pid,self.sid,self.plan['id'],'fixture-grant',hashlib.sha256(dumps(request).encode()).hexdigest(),
             dumps(request),'completed','runs/'+rid,dumps(receipt),now(),now()))
        self.store.execute('INSERT INTO research_run_context VALUES(?,?,?)',(rid,None,now()))
        self.store.evidence.collect_local(self.sid,rid)
        return rid

    def compare(self,**kwargs):
        rid=self.run_fixture(**kwargs)
        return rid,self.research.comparison.compare(self.sid,self.plan['id'],[rid])['comparison_id']

    def assessment(self,level='exploratory',basis='comparison_rules',**fields):
        return {'claim':'The candidate meets the frozen numerical conditions', 'basis':basis,'level':level,
                'reason':'Read the actual archive and fixed comparison rules.',
                'limitations':'Fixture evidence; no statistical or causal certification.', 'evidence':[],**fields}

    def choice(self,candidate,previous=None):
        return {'scope':'same evaluation goal','candidate_record_id':candidate['id'],'candidate':'candidate A',
                'identity_files':['model.py'],'previous_selection_id':previous,'reason':'Useful starting reference, not global best',
                'comparison_impact':'Old references stay fixed; recheck comparisons if evaluation changes.'}

    def test_no_automatic_baseline_and_explicit_update_preserves_old_references(self):
        candidate=self.record('research',evidence=['model.py'])
        self.assertEqual([],integrity.context(self.store,self.sid)['baselines'])
        first=self.record('decision',evidence=['model.py'],baseline_selection=self.choice(candidate))
        bound=self.record('trial',baseline_ref=first['id'])
        second=self.record('decision',evidence=['model.py'],baseline_selection=self.choice(candidate,first['id']))
        self.assertEqual(second['id'],integrity.context(self.store,self.sid)['baselines'][0]['record_id'])
        self.assertEqual(first['id'],integrity.payload(bound)['baseline_ref'])
        self.assertEqual(first['id'],self.store.one('SELECT id FROM records WHERE id=?',(first['id'],))['id'])
        with self.assertRaises(ValueError):self.record('decision',evidence=['model.py'],baseline_selection=self.choice(candidate,first['id']))

    def test_baseline_requires_real_identity_and_same_project(self):
        candidate=self.record('research',evidence=['model.py'])
        with self.assertRaises(ValueError):self.record('decision',baseline_selection=self.choice(candidate))
        other=self.store.create_project('other')['id'];sid=self.store.create_session(other,'ept')['id']
        foreign=self.store.add_record(sid,{'kind':'research','title':'other','content':'other'}, {})
        with self.assertRaises(ValueError):self.record('decision',evidence=['model.py'],baseline_selection=self.choice(foreign))

    def test_numeric_outcome_uses_rules_instead_of_requested_answer(self):
        self.approve();rid,cid=self.compare(good=False)
        result=self.record('result',plan_id=self.plan['id'],run_ids=[rid],comparison_ids=[cid],scientific_assessment=self.assessment())
        self.assertEqual('refuted',integrity.payload(result)['scientific_assessment']['outcome'])
        with self.assertRaises(ValueError):self.record('result',plan_id=self.plan['id'],comparison_ids=[cid],scientific_assessment=self.assessment(outcome='supported'))
        self.assertEqual('refuted',self.research.snapshot(self.sid,result['id'])['study_integrity']['scientific_assessment']['outcome'])

    def test_search_success_does_not_become_confirmation_without_new_evidence(self):
        self.approve();rid,cid=self.compare()
        result=self.record('result',plan_id=self.plan['id'],run_ids=[rid],comparison_ids=[cid],scientific_assessment=self.assessment('confirmatory'))
        self.assertEqual('inconclusive',integrity.payload(result)['scientific_assessment']['outcome'])

    def confirmation_setup(self):
        self.approve();rid,cid=self.compare()
        choice=self.record('decision',plan_id=self.plan['id'],run_ids=[rid],comparison_ids=[cid],evidence=['model.py'])
        previous=self.plan
        review=self.record('review',plan_id=previous['id'],run_ids=[rid])
        spec=specification();spec['pairs']=[123,456]
        config={'selection_record_id':choice['id'],'search_comparison_ids':[cid],'candidate_files':['model.py'],
                'independence_reason':'Fresh seed IDs fixed before confirmation; statistical validity remains a review duty.'}
        fields={'previous_plan_id':previous['id'],'review_record_id':review['id'],'evaluation_contract':spec,
                'evidence':['protocol.json','model.py'],'confirmation_plan':config}
        return fields

    def test_confirmation_needs_frozen_candidate_new_pairs_and_real_snapshots(self):
        fields=self.confirmation_setup()
        bad=copy.deepcopy(fields);bad['evaluation_contract']['pairs']=[42,7]
        with self.assertRaises(ValueError):self.record('plan',**bad)
        bad=copy.deepcopy(fields);bad['evaluation_contract']['rules'][0]['comparison']='noninferior'
        with self.assertRaises(ValueError):self.record('plan',**bad)
        self.plan=self.record('plan',**fields);self.approve()
        rows=observations(True)
        for row in rows:row['seed']={42:123,7:456}[row['seed']]
        rid,cid=self.compare(rows=rows)
        result=self.record('result',plan_id=self.plan['id'],run_ids=[rid],comparison_ids=[cid],scientific_assessment=self.assessment('confirmatory'))
        value=integrity.payload(result)['scientific_assessment']
        self.assertEqual('supported',value['outcome']);self.assertTrue(value['confirmation']['ready'])

    def test_retrospective_candidate_is_not_confirmation(self):
        fields=self.confirmation_setup();self.plan=self.record('plan',**fields);self.approve()
        rows=observations(True)
        for row in rows:row['seed']={42:123,7:456}[row['seed']]
        rid,cid=self.compare(rows=rows,compiled=False)
        result=self.record('result',plan_id=self.plan['id'],comparison_ids=[cid],scientific_assessment=self.assessment('confirmatory'))
        self.assertEqual('inconclusive',integrity.payload(result)['scientific_assessment']['outcome'])

    def test_reference_only_artifacts_do_not_need_candidate_execution_files(self):
        fields=self.confirmation_setup();self.plan=self.record('plan',**fields);self.approve()
        rows=observations(True)
        for row in rows:row['seed']={42:123,7:456}[row['seed']]
        reference=self.run_fixture(rows=[r for r in rows if r['variant']=='baseline'],include_candidate=False)
        candidate=self.run_fixture(rows=[r for r in rows if r['variant']=='candidate'])
        cid=self.research.comparison.compare(self.sid,self.plan['id'],[reference,candidate])['comparison_id']
        result=self.record('result',plan_id=self.plan['id'],comparison_ids=[cid],scientific_assessment=self.assessment('confirmatory'))
        self.assertTrue(integrity.payload(result)['scientific_assessment']['confirmation']['ready'])

    def test_budget_stop_does_not_create_scientific_refutation(self):
        self.approve()
        result=self.record('review',plan_id=self.plan['id'],round_outcome='stop_requested',stop_reason='budget')
        self.assertNotIn('scientific_assessment',integrity.payload(result))
        with self.assertRaises(ValueError):self.record('decision',scientific_assessment=self.assessment(basis='evidence_review',outcome='refuted'))

    def test_review_reuses_actual_quote_checks_and_never_claims_independent_signing(self):
        review={'readiness':[],'lenses':[{'lens':'baseline_fairness','status':'checked','reason':'Actual protocol examined','checks':[]}]}
        with self.assertRaises(ValueError):self.record('research',study_review=review)
        review['lenses'][0]['checks']=[{'basis':'source','claim':'The source fixes validation','reason':'Read source',
            'source_quotes':[{'path':'protocol.json','text':'"split":"validation"'}],'command_ids':[]}]
        result=self.record('research',evidence=['protocol.json'],study_review=review)
        self.assertEqual('not_attested',integrity.payload(result)['trust']['review_independence'])
        review['lenses'][0]['checks'][0]['source_quotes'][0]['text']='invented quote'
        with self.assertRaises(ValueError):self.record('research',evidence=['protocol.json'],study_review=review)

    def test_invalid_comparison_and_no_evidence_remain_separate_states(self):
        self.approve();_,cid=self.compare(rows=[])
        result=self.record('result',plan_id=self.plan['id'],comparison_ids=[cid],scientific_assessment=self.assessment())
        self.assertEqual('invalid',integrity.payload(result)['scientific_assessment']['outcome'])
        self.assertEqual('inconclusive',integrity.numeric_outcome([]))

    def test_declared_readiness_gap_blocks_fit_but_not_diagnostics_and_can_be_resolved(self):
        review={'readiness':[{'phase':'implementation','status':'needs_evidence','reason':'Interface needs actual inspection','checks':[]}],'lenses':[]}
        result=self.record('research',plan_id=self.plan['id'],study_review=review)
        # Bind the new plan-side semantics explicitly; old plans are not silently migrated.
        plan=self.record('plan',study_review=review)
        with self.assertRaises(ValueError):integrity.preflight(self.store,self.sid,plan)
        integrity.preflight(self.store,self.sid,plan,'diagnostic')
        review['readiness'][0].update(status='checked',checks=[{'basis':'source','claim':'Read exact interface','reason':'Actual source',
            'source_quotes':[{'path':'model.py','text':'Frozen candidate implementation identity'}],'command_ids':[]}])
        self.record('research',plan_id=plan['id'],evidence=['model.py'],study_review=review)
        integrity.preflight(self.store,self.sid,plan)

    def test_state_selection_does_not_hide_failures_or_reopen_unchanged_results(self):
        from lab.round_evidence import packet,analysis_action
        self.approve();rid,cid=self.compare()
        self.assertEqual('result_analysis',packet(self.store,self.pid,selection='actionable')['attempts'][0]['analysis_action'])
        self.record('result',run_ids=[rid],plan_id=self.plan['id'])
        self.assertEqual([],packet(self.store,self.pid,selection='actionable')['attempts'])
        self.assertEqual(1,packet(self.store,self.pid)['attempt_count'])
        self.store.execute("UPDATE research_runs SET state='unknown' WHERE id=?",(rid,))
        self.assertEqual('reconcile',packet(self.store,self.pid,selection='actionable')['attempts'][0]['analysis_action'])
        fact={'state':'failed','collection_state':None,'digest':'same'}
        self.assertEqual('needs_evidence',analysis_action(fact,{'digest':'same'}))
        self.assertEqual('failure_diagnosis',analysis_action({**fact,'analysis_scope':'failure_diagnosis'}))
        self.assertEqual('waiting',analysis_action({'state':'running'}))

    def test_engine_upgrade_is_explicit_and_does_not_rewrite_old_binding(self):
        from lab.sdm import SDM
        before=SDM(self.store).binding(self.sid)
        candidate=self.record('research',evidence=['model.py'])
        with patch.object(integrity,'ENGINE_SHA256','different-loaded-implementation'):
            self.assertFalse(integrity.enabled(self.store,self.sid))
            with self.assertRaises(ValueError):self.record('decision',evidence=['model.py'],baseline_selection=self.choice(candidate))
            self.record('research',content='Legacy unstructured evidence stays usable.')
        self.assertEqual(before,SDM(self.store).binding(self.sid))

    def test_original_21_step_design_has_explicit_existing_paths_and_boundaries(self):
        catalog=json.loads((ROOT/'methods/research/sdm-inheritance.json').read_text(encoding='utf-8'))
        self.assertEqual(list(range(1,22)),[r['step'] for r in catalog['steps']])
        self.assertTrue(all((ROOT/path).is_file() for r in catalog['steps'] for path in r['implementation']))
        self.assertTrue(any('签名' in b for b in catalog['boundaries']))

    def test_tool_descriptions_do_not_turn_every_string_into_a_baseline_reference(self):
        self.assertNotIn('description',integrity.STR)
        self.assertIn('description',integrity.FIELDS['baseline_ref'])
        self.assertNotIn('description',integrity.FIELDS['scientific_assessment']['properties']['reason'])


if __name__=='__main__':unittest.main()
