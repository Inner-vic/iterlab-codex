"""Evidence-backed methods and research-chain boundaries; isolated CPU only."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from lab.store import Store
from lab.research import Research, plan_digest
from lab.research_modules import ResearchModules
from lab.sdm import SDM


class MethodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='iterlab-methods-')
        self.store = Store(Path(self.temp.name))
        self.project = self.store.create_project('isolated method study')
        self.sid = self.store.create_session(self.project['id'], 'codex')['id']
        self.workspace = Path(self.project['workspace'])
        self.r = Research(self.store)
        self.m = ResearchModules(self.store)
        self.counter = 0

    def tearDown(self):
        self.r.close(); self.store.close(); self.temp.cleanup()

    def file(self, name, value):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, allow_nan=True) if not isinstance(value, str) else value, encoding='utf-8')
        return name

    def plan(self, protocol=None, files=(), **relationships):
        self.counter += 1
        args = {'kind': 'plan', 'title': 'Study ' + str(self.counter), 'content': 'Discriminate explanations using fixed evidence', 'evidence': list(files)}
        args.update(relationships)
        if protocol is not None:
            name = self.file('protocol-' + str(self.counter) + '.json', {'schema': 1, **protocol})
            args.update(research_protocol=name, evidence=[name, *files])
        return self.store.add_record(self.sid, args, {'origin': 'isolated-regression'})

    def design(self, **overrides):
        return {'mode': 'exploratory', 'hypothesis': 'representation may limit signal', 'alternatives': ['optimization'],
                'selection_rule': 'fixed final epoch', 'selection_split': 'validation', 'claim': 'controlled_effect',
                'changed_factors': ['width'], 'arms': [{'name': 'baseline', 'budget': 30, 'search_trials': 1},
                                                     {'name': 'candidate', 'budget': 30, 'search_trials': 1}], **overrides}

    def approve(self, plan, runs=3, fits=1):
        return self.r.review(self.sid, {'plan_id': plan['id'], 'expected_digest': plan_digest(plan), 'decision': 'accept',
            'reason': 'Isolated bounded method regression', 'execution': 'local', 'allow_local': True,
            'python': sys.executable, 'max_runs': runs, 'max_fits': fits, 'timeout': 10})

    def request(self, plan, script='check.py', outputs=None):
        return {'plan_id': plan['id'], 'script': script, 'files': [script], 'args': [], 'purpose': 'diagnostic',
                'question': 'Verify the isolated evidence chain', 'expected_information': 'Execution boundary only',
                'outputs': outputs or ['metrics.json']}

    def test_actual_split_overlap_blocks_before_process_or_budget_then_corrected_plan_runs(self):
        self.file('check.py', 'import json\njson.dump({"observed":1},open("metrics.json","w"))\n')
        split = self.file('leak.json', {'train': [1, 2], 'validation': [2, 3]})
        data = {'splits': {'train': {'path': split, 'pointer': '/train'}, 'validation': {'path': split, 'pointer': '/validation'}}}
        first = self.plan({'data': data}, [split, 'check.py'])
        frozen = first['evidence']; self.approve(first)
        with self.assertRaisesRegex(ValueError, '共同样本'):
            self.r.run(self.sid, self.request(first), 'leaking-run')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'), [])
        fixed = self.file('clean.json', {'train': [1, 2], 'validation': [3, 4]})
        revised = copy.deepcopy(data)
        for ref in revised['splits'].values(): ref['path'] = fixed
        report=self.m.check(self.sid,'data',first['id'])
        review=self.store.add_record(self.sid,{'kind':'review','title':'Revise contaminated split',
            'content':'Actual overlap requires a corrected split before computation', 'plan_id':first['id'],
            'method_report_ids':[report['report_id']]},{'origin':'isolated-regression'})
        second = self.plan({'data': revised}, [fixed, 'check.py'], previous_plan_id=first['id'],review_record_id=review['id'])
        self.approve(second)
        result = self.r.run(self.sid, self.request(second), 'corrected-run')
        self.assertEqual(result['state'], 'completed', result)
        self.assertEqual(self.store.one('SELECT evidence FROM records WHERE id=?', (first['id'],))['evidence'], frozen)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')), 1)

    def test_group_leakage_and_missing_group_are_distinct(self):
        data = self.file('groups.json', {'train': [{'id': 1, 'group': 'a'}], 'val': [{'id': 2, 'group': 'a'}]})
        plan = self.plan({'data': {'independence_unit': 'group', 'splits': {key: {'path': data, 'pointer': '/' + key} for key in ['train', 'val']}}}, [data])
        report = self.m.check(self.sid, 'data', plan['id'])
        self.assertEqual(report['state'], 'blocked')
        self.assertIn('group_overlap', [f['code'] for f in report['findings']])
        self.assertNotIn('a', report['facts']['counts'])  # No individual/group labels returned.

    def test_temporal_order_uses_actual_ranges(self):
        name = self.file('times.json', {'train': [{'id': 1, 'time': 10}], 'val': [{'id': 2, 'time': 5}]})
        plan = self.plan({'data': {'independence_unit': 'time', 'time_order': ['train', 'val'],
            'splits': {key: {'path': name, 'pointer': '/' + key} for key in ['train', 'val']}}}, [name])
        self.assertIn('temporal_overlap', [f['code'] for f in self.m.check(self.sid, 'data', plan['id'])['findings']])

    def test_hpo_selection_and_confirmatory_claims_cannot_bypass_preflight(self):
        plan = self.plan({'design': self.design(mode='confirmatory', selection_split='test')})
        result = self.m.check(self.sid, 'design', plan['id'])
        self.assertEqual(result['state'], 'blocked')
        self.assertTrue({'test_selection', 'search_is_not_confirmation'} <= {f['code'] for f in result['findings']})
        with self.assertRaisesRegex(ValueError, '执行冲突'): self.m.preflight(self.sid, plan)

    def test_unequal_search_is_not_a_controlled_effect_but_can_be_exploratory(self):
        arms = [{'name': 'baseline', 'budget': 30, 'search_trials': 1}, {'name': 'candidate', 'budget': 30, 'search_trials': 20}]
        controlled = self.plan({'design': self.design(arms=arms)})
        self.assertEqual(self.m.check(self.sid, 'design', controlled['id'])['state'], 'blocked')
        descriptive = self.plan({'design': self.design(arms=arms, claim='method_comparison')})
        self.assertNotEqual(self.m.check(self.sid, 'design', descriptive['id'])['state'], 'blocked')

    def test_claim_of_confirmation_is_not_proof_even_with_declaration(self):
        plan = self.plan({'design': self.design(mode='confirmatory', independent_confirmation=True)})
        report = self.m.check(self.sid, 'design', plan['id'])
        self.assertEqual(report['state'], 'needs_evidence')
        self.assertIn('confirmation_needs_receipts', [f['code'] for f in report['findings']])

    def test_baseline_identity_must_be_frozen_and_unchanged(self):
        name = self.file('baseline.json', {'seed': 7})
        plan = self.plan({'design': self.design(baseline_files=[name])})
        with self.assertRaisesRegex(ValueError, '冻结证据'): self.m.check(self.sid, 'design', plan['id'])

    def test_training_observations_do_not_prescribe_a_cause(self):
        name = self.file('history.json', [{'epoch': 1, 'train': 3, 'val': 2}, {'epoch': 2, 'train': 2, 'val': 3},
                                          {'epoch': 3, 'train': float('nan'), 'val': 2.5}])
        plan = self.plan({'training': {'history': name, 'step': '/epoch', 'train_loss': '/train', 'validation_loss': '/val'}}, [name])
        report = self.m.check(self.sid, 'training', plan['id'])
        self.assertIn('nonfinite_train_loss', [f['code'] for f in report['findings']])
        self.assertEqual(report['facts']['series']['validation_loss']['increases'], 1)
        self.assertEqual(report['facts']['series']['validation_loss']['decreases'], 1)
        self.assertNotIn('learning_rate', report['facts'])
        json.dumps(report, allow_nan=False)

    def test_training_can_read_new_actual_outputs_but_not_unreceipted_file(self):
        self.file('check.py', 'import json\njson.dump([{"epoch":1,"loss":2},{"epoch":2,"loss":1}],open("history.json","w"))\n')
        plan = self.plan({'training': {'step': '/epoch', 'train_loss': '/loss'}}, ['check.py'])
        self.approve(plan)
        with self.assertRaisesRegex(ValueError, '原运行收据'): self.m.check(self.sid, 'training', plan['id'], 'unowned.json')
        run = self.r.run(self.sid, self.request(plan, outputs=['history.json']), 'history-run')
        path = run['outputs'][0]['path']
        report = self.m.check(self.sid, 'training', plan['id'], path)
        self.assertEqual(report['facts']['series']['train_loss']['decreases'], 1)
        self.assertEqual(self.m.read_report(self.sid, report['report_id'])['report_id'], report['report_id'])

    def test_engineering_checks_real_top_level_entrypoint_and_syntax(self):
        self.file('broken.py', 'def outer():\n    def main(): pass\n')
        plan = self.plan({'engineering': {'python_files': ['broken.py'], 'entrypoints': {'broken.py': ['main']}}}, ['broken.py'])
        self.assertIn('missing_entrypoint', [f['code'] for f in self.m.check(self.sid, 'engineering', plan['id'])['findings']])
        self.file('invalid.py', 'def main(:\n')
        plan = self.plan({'engineering': {'python_files': ['invalid.py']}}, ['invalid.py'])
        self.assertIn('syntax_error', [f['code'] for f in self.m.check(self.sid, 'engineering', plan['id'])['findings']])

    def test_reports_are_idempotent_bound_and_cannot_be_used_after_input_change(self):
        self.file('source.py', 'def main(): pass\n')
        plan = self.plan({'engineering': {'python_files': ['source.py']}}, ['source.py'])
        first = self.m.check(self.sid, 'engineering', plan['id'])
        self.assertEqual(first, self.m.check(self.sid, 'engineering', plan['id']))
        record = {'kind': 'decision', 'title': 'Read actual source', 'content': 'Static checks only', 'plan_id': plan['id'], 'method_report_ids': [first['report_id']]}
        result = self.store.add_record(self.sid, record, {})
        self.assertEqual(json.loads(result['source'])['method_reports'][0]['id'], first['report_id'])
        self.file('source.py', 'def different(): pass\n')
        with self.assertRaisesRegex(ValueError, '已改变'): self.store.add_record(self.sid, record, {})

    def test_report_cannot_cross_plan_project_or_claim_target_with_missing_evidence(self):
        first = self.plan(); report = self.m.check(self.sid, 'design', first['id'])
        second = self.plan()
        with self.assertRaisesRegex(ValueError, '此具体方案'):
            self.store.add_record(self.sid, {'kind': 'decision', 'title': 'wrong', 'content': 'wrong', 'plan_id': second['id'], 'method_report_ids': [report['report_id']]}, {})
        other = self.store.create_project('other'); sid = self.store.create_session(other['id'], 'codex')['id']
        with self.assertRaises(ValueError): self.m.read_report(sid, report['report_id'])
        with self.assertRaisesRegex(ValueError, '证据不足'):
            self.store.add_record(self.sid, {'kind': 'review', 'title': 'success', 'content': 'success', 'plan_id': first['id'], 'stop_reason': 'target_achieved', 'method_report_ids': [report['report_id']]}, {})

    def test_tampered_report_is_rejected_and_survives_reopen(self):
        plan = self.plan(); report = self.m.check(self.sid, 'design', plan['id'])
        self.assertEqual(ResearchModules(self.store).read_report(self.sid, report['report_id'])['state'], 'needs_evidence')
        self.store.execute('UPDATE research_method_reports SET body=? WHERE id=?', ('{}', report['report_id']))
        with self.assertRaises(ValueError): self.m.read_report(self.sid, report['report_id'])

    def test_evidence_report_is_invalidated_by_new_failed_attempt_and_keeps_both(self):
        self.file('check.py', 'import json\njson.dump({"observed":1},open("metrics.json","w"))\n')
        plan = self.plan(files=['check.py']); self.approve(plan)
        self.r.run(self.sid, self.request(plan), 'first')
        first = self.m.check(self.sid, 'evidence', plan['id'])
        self.file('fail.py', 'raise RuntimeError("isolated intended failure")\n')
        self.r.run(self.sid, self.request(plan, script='fail.py'), 'failed')
        with self.assertRaisesRegex(ValueError, '状态已变化'): self.m.read_report(self.sid, first['report_id'])
        current = self.m.check(self.sid, 'evidence', plan['id'])
        self.assertEqual(len(current['facts']['attempts']), 2)
        self.assertEqual(current['state'], 'needs_evidence')

    def test_methods_are_builtin_and_old_binding_requires_explicit_upgrade(self):
        old = SDM(self.store, default_mode='native_skill').binding(self.sid)
        self.assertFalse(self.m.context(self.sid)['available'])
        with self.assertRaisesRegex(ValueError, '显式升级'): self.m.read(self.sid, 'data')
        method = SDM(self.store)
        method.upgrade(self.sid, old['digest'], 'isolated method migration', 'test authority')
        self.assertTrue(self.m.context(self.sid)['available'])
        self.assertIn('单位', self.m.read(self.sid, 'data')['text'])
        self.assertTrue(Path(old['path']).is_file())

    def test_role_and_failure_context_are_not_a_fixed_module_pipeline(self):
        self.plan()
        self.assertEqual([x['module'] for x in self.m.context(self.sid, 'critic')['applicable']], ['design', 'evidence'])
        self.assertEqual([x['module'] for x in self.m.context(self.sid, 'executor')['applicable']], ['engineering', 'training'])

    def test_native_tool_dispatch_exposes_real_method_report(self):
        from lab.kernel import Kernel
        from tests.test_core import FakeRPC
        kernel = Kernel(self.store, FakeRPC)
        try:
            plan = self.plan({'design': self.design(selection_split='test')})
            self.store.patch_session(self.sid, thread_id='method-native')
            rpc = FakeRPC([], self.workspace, lambda _: None, lambda *_: None, lambda *_: None)
            kernel.on_request({'id': 1, 'method': 'item/tool/call', 'params': {'threadId': 'method-native', 'turnId': 't', 'callId': 'c',
                'tool': 'research_method', 'arguments': {'operation': 'check', 'module': 'design', 'plan_id': plan['id']}}}, rpc)
            response = rpc.replies[-1][1]
            self.assertTrue(response['success'], response)
            self.assertEqual(json.loads(response['contentItems'][0]['text'])['state'], 'blocked')
        finally: kernel.close()

    def test_duration_effect_checks_real_schedule_prefix_and_not_best_epoch(self):
        self.file('short.json',[.1,.05]);self.file('long.json',[.2,.1,.05])
        plan=self.plan({'design':self.design(study_type='duration',changed_factors=['duration'],
            learning_rate_schedules={'baseline':'short.json','candidate':'long.json'})},['short.json','long.json'])
        report=self.m.check(self.sid,'design',plan['id'])
        self.assertFalse(report['facts']['duration_comparison']['prefix_equal'])
        self.assertIn('schedule_prefix_mismatch',[f['code'] for f in report['findings']])
        self.assertEqual(report['state'],'blocked')
        self.file('matched-long.json',[.1,.05,.01])
        matched=self.plan({'design':self.design(study_type='duration',changed_factors=['duration'],
            arms=[{'name':'baseline','budget':2,'search_trials':1},{'name':'candidate','budget':3,'search_trials':1}],
            learning_rate_schedules={'baseline':'short.json','candidate':'matched-long.json'})},['short.json','matched-long.json'])
        result=self.m.check(self.sid,'design',matched['id'])
        self.assertTrue(result['facts']['duration_comparison']['prefix_equal'])
        self.assertNotEqual(result['state'],'blocked','Duration is the experimental factor, not an unfair nuisance budget')

    def test_runtime_report_rejects_changed_receipt_identity_even_if_file_matches_new_receipt(self):
        from lab.research_modules import sha
        self.file('check.py','import json\njson.dump([{"step":1,"loss":2}],open("history.json","w"))\n')
        plan=self.plan({'training':{'step':'/step','train_loss':'/loss'}},['check.py']);self.approve(plan)
        run=self.r.run(self.sid,self.request(plan,outputs=['history.json']),'runtime-identity')
        name=run['outputs'][0]['path'];report=self.m.check(self.sid,'training',plan['id'],name)
        self.file(name,[{'step':1,'loss':1}])
        changed=json.loads(self.store.one('SELECT receipt FROM research_runs WHERE id=?',(run['run_id'],))['receipt'])
        raw=(self.workspace/name).read_bytes()
        changed['outputs'][0].update(sha256=sha(raw),size=len(raw))
        self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?',(json.dumps(changed),run['run_id']))
        with self.assertRaisesRegex(ValueError,'原输入身份'):self.m.read_report(self.sid,report['report_id'])

    def test_method_report_survives_actual_store_restart_without_new_execution(self):
        plan=self.plan();report=self.m.check(self.sid,'design',plan['id'])
        self.r.close();self.store.close()
        self.store=Store(Path(self.temp.name));self.r=Research(self.store);self.m=ResearchModules(self.store)
        self.assertEqual(self.m.read_report(self.sid,report['report_id'])['report_id'],report['report_id'])
        self.assertEqual(self.store.rows('SELECT id FROM research_runs'),[])
        self.assertEqual(len(self.store.rows('SELECT id FROM research_method_reports')),1)

    def test_nested_method_snapshot_handles_long_windows_paths_without_shortening_identity(self):
        import shutil
        from lab.sdm import io_path,files_manifest,publish_snapshot,BUILTIN_SOURCE
        root=self.workspace/'long-snapshot'
        target=root/('a'*80)/('b'*80)/('c'*64)/'research'
        manifest=files_manifest(BUILTIN_SOURCE)
        try:
            publish_snapshot(BUILTIN_SOURCE,target,manifest)
            self.assertEqual(files_manifest(target),manifest)
            self.assertIn('领域语义',io_path(target/'modules/data.md').read_text(encoding='utf-8'))
        finally:
            if io_path(root).exists():
                self.assertEqual(root.resolve().parent,self.workspace.resolve())
                shutil.rmtree(io_path(root))


if __name__ == '__main__': unittest.main()
