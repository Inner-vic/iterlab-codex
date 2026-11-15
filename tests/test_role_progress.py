"""Offline role-progress regression. All durable state is owned temporary data."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from lab.continuation import Continuation
from lab.kernel import Kernel
from lab.research import plan_digest
from lab.role_progress import DEFAULT_LIMIT, LIMIT_SETTING, MAX_LIMIT
from lab.roles import DEFAULT_POLICY, Roles, validate_policy
from lab.settings import Settings
from lab.store import Store, dumps, now
from tests.test_core import FakeRPC


class RoleProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='iterlab-role-progress-')
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root)
        self.kernel = Kernel(self.store, FakeRPC)
        self.kernel.settings.save({'channel': 'codex', 'profile': 'default',
            'agent_roles': copy.deepcopy(DEFAULT_POLICY)})
        self.kernel.settings.command = lambda *args: ['isolated-fake-rpc']
        self.verify = patch.object(self.kernel.settings, 'verify_channel', return_value=None)
        self.verify.start()
        # Never start a native host implicitly; dispatch is synchronous and owned.
        self.pool = patch.object(self.kernel.pool, 'submit')
        self.pool.start()
        self.r = self.kernel.research
        self.roles = self.kernel.roles
        self.pid = self.store.create_project('synthetic role progress')['id']
        self.sid = self.store.create_session(self.pid, 'codex')['id']
        self.workspace = Path(self.store.session(self.sid)['workspace'])
        self.catalog = [{'id': m, 'model': m} for m in set(DEFAULT_POLICY['models'].values())]
        self.plan = self.record('plan', content='fixed synthetic evaluation plan')
        self.r.review(self.sid, {'plan_id': self.plan['id'], 'expected_digest': plan_digest(self.plan),
            'decision': 'accept', 'reason': 'offline control fixture', 'allow_local': True,
            'python': sys.executable, 'max_runs': 3, 'max_fits': 2, 'timeout': 3})
        self.sent = []
        self.roles.progress.status(self.sid, self.kernel.settings.value)
        self.start_turn()

    def tearDown(self):
        self.pool.stop()
        self.verify.stop()
        self.kernel.close()
        self.store.close()
        self.temp.cleanup()
        self.assertFalse(self.root.exists(), 'owned test data must be cleaned')

    def record(self, kind, content='repeat review without new evidence', **extra):
        return self.store.add_record(self.sid, {'kind': kind, 'title': kind, 'content': content, **extra},
            {'origin': 'codex-native-tool', 'turn_id': self.store.session(self.sid)['active_turn']})

    def start_turn(self, delivery=None):
        if delivery is None:
            delivery = self.kernel.send(self.sid, 'synthetic human guidance', origin='user')
        turn = 'synthetic-turn:' + uuid.uuid4().hex
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id=? WHERE id=?", (turn, delivery['id']))
        delivery = self.store.one('SELECT * FROM deliveries WHERE id=?', (delivery['id'],))
        self.store.patch_session(self.sid, status='running', active_turn=turn)
        route = self.roles.route(self.sid, delivery, self.kernel.settings.value, self.catalog)
        self.kernel.sdm.input(self.sid, delivery)  # Pin autonomous authority, if any.
        self.route = route
        self.turn = turn
        return route

    def pending(self):
        if self.route['role'] == 'critic':
            record = self.record('research', plan_id=self.plan['id'])
            args = {'target': 'planner', 'record_id': record['id'], 'verdict': 'needs-evidence'}
        else:
            args = {'target': 'critic', 'record_id': self.plan['id'], 'verdict': 'handoff'}
        h = self.roles.handoff(self.sid, self.turn, {**args, 'plan_id': self.plan['id'],
            'reason': 'synthetic role cycle'}, 'call:' + uuid.uuid4().hex, self.r)
        self.store.patch_session(self.sid, status='idle', active_turn=None)
        return h

    def send(self, sid, text, **kwargs):
        self.sent.append(kwargs['delivery_id'])
        return self.kernel.send(sid, text, **kwargs)

    def dispatch(self, h=None, advance=True):
        h = h or self.pending()
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        delivery = self.store.one('SELECT * FROM deliveries WHERE id=?', (h['delivery_id'],))
        if delivery and advance:
            self.start_turn(delivery)
        return h, delivery

    def guard(self):
        return self.roles.status(self.sid, self.kernel.settings.value)['no_progress_guard']

    def stalled(self):
        for index in range(DEFAULT_LIMIT):
            _, delivery = self.dispatch()
            self.assertIsNotNone(delivery)
            self.assertEqual(self.guard()['consecutive_unchanged'], index + 1)
        h = self.pending()
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        self.assertTrue(self.guard()['blocked'])
        return h

    def evidence(self, content='{"offline_probe":1}', path='synthetic-evidence.json'):
        (self.workspace / path).write_text(content, encoding='utf-8')
        return self.record('research', content='observed offline bytes', evidence=[path])

    def enable_autonomy(self):
        self.store.patch_session(self.sid, status='idle', active_turn=None)
        self.r.autonomy.enable(self.sid, self.plan['id'], plan_digest(self.plan),
            {'objective': 'offline orchestration only', 'evaluation': 'fixed synthetic control',
             'data_boundary': 'no real data', 'allowed_changes': 'bounded synthetic plan', 'frozen_files': []},
            'explicit fixture authority')
        self.start_turn()

    def local_run(self, state='completed', receipt=None):
        grant = self.store.one('SELECT id FROM research_grants WHERE project_id=?', (self.pid,))['id']
        id = 'synthetic-run:' + uuid.uuid4().hex
        request = {'purpose': 'fit', 'plan_id': self.plan['id'], 'script': 'synthetic.py'}
        stamp = now()
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (id, 'offline-operation:' + id, self.pid, self.sid, self.plan['id'], grant,
             hashlib.sha256(dumps(request).encode()).hexdigest(), dumps(request), state,
             'synthetic-run-directory', dumps(receipt) if receipt is not None else None, stamp, stamp))
        return id

    def cloud_run(self, state='submitted', result=None):
        id = 'synthetic-cloud:' + uuid.uuid4().hex
        stamp = now()
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (id, self.sid, self.pid, self.plan['id'], self.plan['id'], 'synthetic-component',
             'synthetic-task', '{}', state, dumps(result) if result else None, stamp, stamp))
        return id

    def test_default_eight_then_pending_ninth_is_blocked_without_send(self):
        h = self.stalled()
        for _ in range(5):
            self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        self.assertEqual(len(self.sent), 8)
        self.assertEqual(self.guard()['consecutive_unchanged'], 8)
        self.assertEqual(self.guard()['reason_code'], 'no_progress')
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')
        self.assertIsNone(self.store.one('SELECT id FROM deliveries WHERE id=?', (h['delivery_id'],)))
        check = self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],))
        self.assertEqual((check['counted'], check['state']), (0, 'no_progress'))
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status WHERE session_id=?', (self.sid,))['state'], 'no_progress')
        events = self.store.rows("SELECT seq FROM events WHERE session_id=? AND kind='research.role_no_progress'", (self.sid,))
        self.assertEqual(len(events), 1)
        lifecycle = self.roles.progress.lifecycle_block(self.sid, self.kernel.settings.value)
        self.assertEqual((lifecycle['state'], lifecycle['reason_code'], lifecycle['subject_id']), ('blocked', 'no_progress', h['id']))
        self.assertEqual(self.store.rows('SELECT id FROM research_runs'), [])
        self.assertIsNone(self.kernel.rpc)

    def test_reconstructed_roles_and_real_store_restart_preserve_count(self):
        for _ in range(4):
            self.dispatch()
        self.roles = Roles(self.store, self.kernel.guard_role_dispatch)
        self.kernel.roles = self.roles
        self.assertEqual(self.guard()['consecutive_unchanged'], 4)
        for _ in range(4):
            self.dispatch()
        h = self.stalled_pending_only()
        signature = self.guard()['signature']
        self.pool.stop()
        self.verify.stop()
        self.kernel.close()
        self.store.close()
        self.store = Store(self.root)
        self.kernel = Kernel(self.store, FakeRPC)
        self.kernel.settings.command = lambda *args: ['isolated-fake-rpc']
        self.verify = patch.object(self.kernel.settings, 'verify_channel', return_value=None)
        self.verify.start()
        self.pool = patch.object(self.kernel.pool, 'submit')
        self.pool.start()
        self.r = self.kernel.research
        self.roles = self.kernel.roles
        self.assertEqual(self.guard()['consecutive_unchanged'], 8)
        self.assertEqual(self.guard()['signature'], signature)
        with patch.object(self.kernel, 'send') as send:
            self.kernel.resume_roles(self.sid)
            Continuation(self.kernel).tick(self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')
        self.assertIsNone(self.kernel.rpc)

    def stalled_pending_only(self):
        h = self.pending()
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        self.assertTrue(self.guard()['blocked'])
        return h

    def test_same_content_plan_new_ids_and_empty_reviews_do_not_reset(self):
        for _ in range(3):
            self.dispatch()
        original_signature = self.guard()['progress_signature']
        for index in range(3):
            duplicate = self.record('plan', content=self.plan['content'], title='new title ' + str(index), summary='new summary')
            self.assertNotEqual(duplicate['id'], self.plan['id'])
            self.record('decision', content='still lacks evidence, wording ' + str(index))
            self.record('research', content='no additional observation ' + str(index))
        self.assertEqual(self.guard()['consecutive_unchanged'], 3)
        self.assertEqual(self.guard()['progress_signature'], original_signature)
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 4)

    def test_new_evidence_resets_but_identical_bytes_new_path_does_not(self):
        h = self.stalled()
        grants = self.store.rows('SELECT * FROM research_grants')
        self.evidence()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.assertFalse(self.guard()['blocked'])
        self.dispatch(h)
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.evidence(path='same-bytes-new-path.json')
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'), grants)

    def test_late_cloud_archive_is_progress_but_repeated_collection_metadata_is_not(self):
        self.cloud_run(state='succeeded')
        self.guard() # Observe the completed task before collection arrives.
        self.dispatch() # Consume that legitimate task-completion progress window.
        self.stalled()
        grants=self.store.rows('SELECT * FROM research_grants')
        outcome={'archive_state':'files_archived','manifest_sha256':'a'*64,'evidence_digest':'c'*64,
                 'result_check':{'state':'identity_verified','identity_verified':True},'attempts':1}
        self.store.execute('INSERT INTO company_output_jobs VALUES(?,?,?,?,?,?,?)',
            (self.sid,'synthetic-task','ready',0,dumps(outcome),now(),now()))
        self.assertFalse(self.guard()['blocked'])
        self.assertEqual(self.guard()['consecutive_unchanged'],0)
        self.store.execute("UPDATE research_role_progress SET consecutive_unchanged=2,progress_pending=0 WHERE session_id=?",(self.sid,))
        # Another read of the same ready evidence is not another research finding.
        outcome.update(attempts=2,manifest_sha256='b'*64,report_path='new-copy/REPORT.md')
        self.store.execute('UPDATE company_output_jobs SET outcome=?,updated_at=?',(dumps(outcome),now()))
        self.assertEqual(self.guard()['consecutive_unchanged'],2)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM company_training')['n'],1)
        outcome['evidence_digest']='d'*64
        self.store.execute('UPDATE company_output_jobs SET outcome=?',(dumps(outcome),))
        self.assertEqual(self.guard()['consecutive_unchanged'],0)

    def test_upgrade_baselines_existing_archive_without_reopening_historical_stall(self):
        self.cloud_run(state='succeeded');self.guard();self.dispatch();self.stalled()
        before=self.guard()
        context=json.loads(self.store.one('SELECT context FROM research_role_progress WHERE session_id=?',(self.sid,))['context'])
        context.pop('evidence_readiness_version')
        self.store.execute('UPDATE research_role_progress SET context=? WHERE session_id=?',(dumps(context),self.sid))
        self.store.execute('INSERT INTO company_output_jobs VALUES(?,?,?,?,?,?,?)',
            (self.sid,'synthetic-task','ready',0,dumps({'archive_state':'files_archived','evidence_digest':'a'*64}),now(),now()))
        migrated=self.guard()
        self.assertTrue(migrated['blocked'])
        for key in ('consecutive_unchanged','blocked_handoff_id','reset_reason'):
            self.assertEqual(migrated[key],before[key])
        self.assertEqual(migrated['context']['evidence_readiness_version'],1)
        # Only a later genuinely different result may open a window.
        self.store.execute('UPDATE company_output_jobs SET outcome=?',(dumps({'archive_state':'files_archived','evidence_digest':'b'*64}),))
        self.assertFalse(self.guard()['blocked'])

    def test_human_user_and_review_inputs_open_new_window_not_training_budget(self):
        h = self.stalled()
        for origin in ('user', 'review'):
            grants = self.store.rows('SELECT * FROM research_grants')
            runs = self.store.rows('SELECT * FROM research_runs')
            guidance = self.kernel.send(self.sid, 'synthetic explicit guidance ' + origin, origin=origin)
            self.assertFalse(self.guard()['blocked'])
            self.assertEqual(self.guard()['consecutive_unchanged'], 0)
            self.assertEqual(self.guard()['reset_reason'], 'human_input')
            self.assertEqual(self.guard()['context']['human_input']['origin'], origin)
            self.start_turn(guidance)
            successor = self.pending()
            # First pass keeps existing precedence: supersede the older handoff.
            self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
            self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'superseded')
            self.dispatch(successor)
            self.assertEqual(self.guard()['consecutive_unchanged'], 1)
            self.assertEqual(self.store.rows('SELECT * FROM research_grants'), grants)
            self.assertEqual(self.store.rows('SELECT * FROM research_runs'), runs)
            # The successor already used one slot of the next stalled window.
            for _ in range(7):
                self.dispatch()
            h = self.stalled_pending_only()

    def test_local_run_state_and_receipt_progress_ignore_only_clock_changes(self):
        # Fingerprint/counter unit test, independent of dispatch eligibility.
        # Real waiting dispatch is covered by AutonomyTests.external_wait.
        self.roles.before_dispatch=lambda sid,h:True
        for _ in range(2):
            self.dispatch()
        id = self.local_run('running')
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        receipt = {'state': 'completed', 'exit_code': 0, 'outputs': [], 'seconds': 1, 'observed_at': 'clock-1'}
        self.store.execute('UPDATE research_runs SET state=?,receipt=?,updated_at=? WHERE id=?', ('completed', dumps(receipt), now(), id))
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        receipt.update(seconds=2, observed_at='clock-2')
        self.store.execute('UPDATE research_runs SET receipt=?,updated_at=? WHERE id=?', (dumps(receipt), now(), id))
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 2)
        receipt['outputs'] = [{'path': 'synthetic-metrics.json', 'sha256': '1' * 64, 'size': 11}]
        self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?', (dumps(receipt), id))
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        # Returning to a previously observed state cannot reopen a window.
        self.dispatch()
        self.store.execute("UPDATE research_runs SET state='running',receipt=NULL WHERE id=?", (id,))
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.assertEqual(self.r.status(self.sid)['attempts'], {'runs': 1, 'fits': 1})

    def test_cloud_preparation_is_not_run_but_actual_state_and_result_are_progress(self):
        # Test the progress facts without asking the native host to poll a job.
        self.roles.before_dispatch=lambda sid,h:True
        for _ in range(2):
            self.dispatch()
        id = self.cloud_run('prepared')
        self.assertEqual(self.guard()['consecutive_unchanged'], 2)
        self.store.execute("UPDATE company_training SET state='submitted' WHERE id=?", (id,))
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        result = {'state': 'submitted', 'item': {'uid': 'synthetic-task', 'phase': 'running', 'update_timestamp': 'clock-1'}}
        self.store.execute('UPDATE company_training SET state=?,result=? WHERE id=?', ('running', dumps(result), id))
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        result['item']['update_timestamp'] = 'clock-2'
        self.store.execute('UPDATE company_training SET result=?,updated_at=? WHERE id=?', (dumps(result), now(), id))
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        result['item']['phase'] = 'failed'
        self.store.execute('UPDATE company_training SET result=? WHERE id=?', (dumps(result), id))
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.assertEqual(self.r.status(self.sid)['attempts'], {'runs': 1, 'fits': 1})

    def test_retry_dispatch_does_not_count_or_send_twice_after_partial_save(self):
        h, delivery = self.dispatch(advance=False)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        # Simulate recovery after enqueue but before recording dispatch state.
        self.store.execute("UPDATE research_role_handoffs SET state='pending' WHERE id=?", (h['id'],))
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?", (delivery['id'],))
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.assertEqual(self.sent, [h['delivery_id']])
        self.assertEqual(len(self.store.rows('SELECT * FROM research_role_progress_checks')), 1)

    def test_exception_after_reserved_count_keeps_identity_and_one_slot(self):
        h = self.pending()
        failed_send = Mock(side_effect=ValueError('synthetic enqueue failed before delivery creation'))
        with self.assertRaisesRegex(ValueError, 'synthetic enqueue'):
            self.roles.dispatch(self.sid, failed_send, self.kernel.settings.value)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.assertEqual(self.sent, [h['delivery_id']])

    def test_concurrent_dispatches_share_one_handoff_reservation(self):
        h = self.pending()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: self.roles.dispatch(self.sid, self.send, self.kernel.settings.value), range(6)))
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.assertEqual(self.sent, [h['delivery_id']])

    def test_limit_validation_is_bounded_separate_from_role_model_policy(self):
        settings = self.kernel.settings
        self.assertEqual(settings.value[LIMIT_SETTING], 8)
        policy = copy.deepcopy(settings.value['agent_roles'])
        for invalid in (None, False, True, 0, -1, 65, 1.5, '8', {'limit': 8}):
            previous = copy.deepcopy(settings.value)
            with self.assertRaises(ValueError):
                settings.save({LIMIT_SETTING: invalid})
            self.assertEqual(settings.value, previous)
        for limit in (1, MAX_LIMIT):
            settings.save({LIMIT_SETTING: limit})
            self.assertEqual(Settings(self.root).value[LIMIT_SETTING], limit)
            self.assertEqual(settings.value['agent_roles'], policy)
            self.assertEqual(validate_policy(settings.value['agent_roles']), policy)
        settings.save({LIMIT_SETTING: 2})
        self.dispatch()
        self.dispatch()
        h = self.pending()
        self.roles.dispatch(self.sid, self.send, settings.value)
        self.assertTrue(self.guard()['blocked'])
        self.assertEqual(self.guard()['limit'], 2)
        self.assertEqual(len(self.sent), 2)
        # Loading malformed saved configuration is fail closed, not a silent default.
        (self.root / 'settings.json').write_text(dumps({LIMIT_SETTING: 0}), encoding='utf-8')
        with self.assertRaises(ValueError):
            Settings(self.root)

    def test_policy_generation_branch_and_profile_bind_signature_without_free_reset(self):
        self.enable_autonomy()
        for _ in range(3):
            self.dispatch()
        before = self.guard()
        self.store.execute('UPDATE research_autonomy_policies SET updated_at=? WHERE project_id=?', ('synthetic-generation-2', self.pid))
        after = self.guard()
        self.assertNotEqual(before['signature'], after['signature'])
        self.assertEqual(after['consecutive_unchanged'], 3)
        self.kernel.settings.save({'profile': 'other-profile'})
        after_profile = self.guard()
        self.assertNotEqual(after['signature'], after_profile['signature'])
        self.assertEqual(after_profile['consecutive_unchanged'], 3)
        branch = self.r.autonomy.status(self.sid)['active_branch']
        replacement = {**branch, 'id': 'synthetic-other-branch', 'created_at': now()}
        self.store.execute('INSERT INTO research_branches(' + ','.join(replacement) + ') VALUES(' + ','.join('?' for _ in replacement) + ')', tuple(replacement.values()))
        self.store.execute('UPDATE research_active_branches SET branch_id=? WHERE project_id=?', (replacement['id'], self.pid))
        after_branch = self.guard()
        self.assertNotEqual(after_profile['signature'], after_branch['signature'])
        self.assertEqual(after_branch['consecutive_unchanged'], 3)
        authority = self.store.one('SELECT * FROM research_authorities WHERE id=?', (branch['authority_id'],))
        new_authority = {**authority, 'id': 'synthetic-replacement-authority', 'created_at': now()}
        self.store.execute('INSERT INTO research_authorities(' + ','.join(new_authority) + ') VALUES(' + ','.join('?' for _ in new_authority) + ')', tuple(new_authority.values()))
        self.store.execute('UPDATE research_autonomy_policies SET authority_id=? WHERE project_id=?', (new_authority['id'], self.pid))
        after_authority = self.guard()
        self.assertNotEqual(after_branch['signature'], after_authority['signature'])
        self.assertEqual(after_authority['consecutive_unchanged'], 3)
        h = self.pending()
        with patch.object(self.kernel, 'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')
        self.assertIsNone(self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],)))

    def test_continuation_role_priority_still_stops_at_guard_without_generic_wakeup(self):
        self.enable_autonomy()
        h = self.stalled()
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
            Continuation(self.kernel).tick(self.sid)
        finalizations=self.store.rows("SELECT * FROM deliveries WHERE id LIKE 'research-finalize:%'")
        self.assertEqual(len(finalizations),1)
        self.assertIn('不得新增计算',finalizations[0]['text'])
        self.assertEqual(self.guard()['consecutive_unchanged'], 8)
        self.assertEqual(self.store.rows('SELECT * FROM research_continuations'), [])
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status WHERE session_id=?', (self.sid,))['state'], 'finalizing')

    def test_progress_never_overrides_existing_profile_gate_or_takeover(self):
        self.enable_autonomy()
        h = self.pending()
        self.evidence()
        self.kernel.settings.save({'profile': 'not-authorized'})
        with patch.object(self.kernel, 'send') as send:
            self.kernel.continuation.tick(self.sid)
            send.assert_not_called()
        self.assertIsNone(self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],)))
        schedule = self.store.one('SELECT state,reason FROM research_scheduler_status WHERE session_id=?', (self.sid,))
        self.assertEqual(schedule['state'], 'blocked')
        self.assertIn('profile', schedule['reason'])
        self.kernel.settings.save({'profile': 'default'})
        self.r.autonomy.mode(self.sid, 'manual', 'synthetic explicit takeover')
        with patch.object(self.kernel, 'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')

    def test_progress_never_overrides_revoked_grant_or_rechecks_it_as_new(self):
        self.enable_autonomy()
        h = self.pending()
        self.evidence()
        grant = self.store.one('SELECT * FROM research_grants WHERE project_id=?', (self.pid,))
        self.r.review(self.sid, {'plan_id': self.plan['id'], 'expected_digest': plan_digest(self.plan),
            'decision': 'reject', 'reason': 'synthetic revocation'})
        with patch.object(self.kernel, 'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()
        self.assertIsNone(self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],)))
        self.assertEqual(self.store.one('SELECT * FROM research_grants WHERE id=?', (grant['id'],)), grant)
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status WHERE session_id=?', (self.sid,))['state'], 'blocked')

    def test_pause_and_unknown_are_not_cleared_by_new_evidence(self):
        h = self.pending()
        self.r.workflow.pause(self.sid, 'synthetic human pause')
        self.evidence()
        with patch.object(self.kernel, 'send') as send:
            self.kernel.resume_roles(self.sid)
            self.roles.dispatch(self.sid, send, self.kernel.settings.value)
            send.assert_not_called()
        self.assertTrue(self.r.workflow.paused(self.sid))
        self.assertIsNone(self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],)))
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?', (self.sid,))
        self.store.patch_session(self.sid, status='unknown')
        with patch.object(self.kernel, 'send') as send:
            self.roles.dispatch(self.sid, send, self.kernel.settings.value)
            send.assert_not_called()
        self.store.patch_session(self.sid, status='idle')
        stamp = now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            ('synthetic-unknown-delivery', self.sid, 'uncertain synthetic request', 'queue', 'unknown', None, None, stamp, stamp))
        with patch.object(self.kernel, 'send') as send:
            self.roles.dispatch(self.sid, send, self.kernel.settings.value)
            send.assert_not_called()
        self.assertIsNone(self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],)))

    def test_real_plan_body_change_resets_once_but_return_to_old_body_does_not(self):
        for _ in range(3):
            self.dispatch()
        self.record('plan', content='new synthetic method with a different evaluation action')
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.dispatch()
        self.record('plan', content=self.plan['content'])
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)

    def test_human_guidance_after_actual_fit_does_not_restore_used_fit_budget(self):
        id = self.local_run('completed', {'state': 'completed', 'exit_code': 0})
        self.guard()
        # Consume the one real-progress dispatch before checking the new window.
        self.dispatch()
        h = self.stalled()
        grants = self.store.rows('SELECT * FROM research_grants')
        before = self.r.status(self.sid)['attempts']
        guidance = self.kernel.send(self.sid, 'synthetic next steps after fit', origin='user')
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.assertEqual(self.r.status(self.sid)['attempts'], before)
        self.assertEqual(before, {'runs': 1, 'fits': 1})
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'), grants)
        self.assertIsNotNone(self.store.one('SELECT id FROM research_runs WHERE id=?', (id,)))
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')
        self.start_turn(guidance)

    def test_unknown_role_delivery_remains_once_counted_and_never_replayed(self):
        h = self.pending()
        def uncertain(sid, text, **kwargs):
            delivery = self.send(sid, text, **kwargs)
            self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?", (delivery['id'],))
            return {**delivery, 'state': 'unknown'}
        self.roles.dispatch(self.sid, uncertain, self.kernel.settings.value)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'unknown')
        self.roles.dispatch(self.sid, uncertain, self.kernel.settings.value)
        self.assertEqual(self.sent, [h['delivery_id']])
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        # Even a stale pending handoff marker cannot replay the uncertain delivery.
        self.store.execute("UPDATE research_role_handoffs SET state='pending' WHERE id=?", (h['id'],))
        self.roles.dispatch(self.sid, uncertain, self.kernel.settings.value)
        self.assertEqual(self.sent, [h['delivery_id']])

    def test_generic_continuation_cannot_bypass_existing_stopped_role_guard(self):
        self.enable_autonomy()
        h = self.stalled()
        # Removing the role event from the actionable queue does not make a
        # generic wakeup a liveness reset or an alternative automatic route.
        self.store.execute("UPDATE research_role_handoffs SET state='superseded' WHERE id=?", (h['id'],))
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_finalizations')),1)
        self.assertEqual(self.guard()['consecutive_unchanged'],8)
        self.assertEqual(self.store.rows('SELECT * FROM research_continuations'), [])
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status WHERE session_id=?', (self.sid,))['state'], 'finalizing')

    def test_progress_reset_does_not_clear_existing_authority_block_projection(self):
        self.enable_autonomy()
        h = self.stalled()
        self.kernel.settings.save({'profile': 'not-authorized'})
        with patch.object(self.kernel, 'send') as send:
            self.kernel.resume_roles(self.sid)
            send.assert_not_called()
        schedule = self.store.one('SELECT state,reason FROM research_scheduler_status WHERE session_id=?', (self.sid,))
        self.assertEqual(schedule['state'], 'blocked')
        self.evidence()
        self.assertFalse(self.guard()['blocked'])
        self.assertEqual(self.guard()['consecutive_unchanged'], 0)
        self.assertEqual(self.store.one('SELECT state,reason FROM research_scheduler_status WHERE session_id=?', (self.sid,)), schedule)
        with patch.object(self.kernel, 'send') as send:
            self.kernel.continuation.tick(self.sid)
            send.assert_not_called()
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')
        self.assertIsNone(self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=? AND counted=1', (h['id'],)))

    def test_lifecycle_guard_keeps_exact_handoff_and_live_limit_without_resending(self):
        self.kernel.settings.save({LIMIT_SETTING: 1})
        self.dispatch()
        h = self.pending()
        self.roles.dispatch(self.sid, self.send, self.kernel.settings.value)
        with patch.object(self.kernel, 'send') as send:
            first = self.r.workflow.publish(self.sid)
            count = len(self.store.rows("SELECT seq FROM events WHERE kind='research.lifecycle_changed'"))
            self.assertEqual(self.r.workflow.publish(self.sid), first)
            send.assert_not_called()
        self.assertEqual((first['state'], first['reason_code'], first['subject_kind'], first['subject_id']),
            ('blocked', 'no_progress', 'role_handoff', h['id']))
        self.assertEqual(first['guard']['limit'], 1)
        self.assertFalse(first['guard']['budget_reset'])
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='research.lifecycle_changed'")), count)
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')

    def test_lifecycle_pause_unknown_interrupt_approval_and_turn_precede_role_guard(self):
        self.stalled()
        workflow = self.r.workflow
        workflow.pause(self.sid, 'synthetic pause')
        self.assertEqual(workflow.publish(self.sid)['state'], 'paused')
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?', (self.sid,))
        stamp = now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            ('lifecycle-unknown', self.sid, 'synthetic unknown', 'queue', 'unknown', None, None, stamp, stamp))
        self.assertEqual(workflow.publish(self.sid)['subject_id'], 'lifecycle-unknown')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id='lifecycle-unknown'")
        self.store.execute('INSERT INTO delivery_interrupt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('lifecycle-interrupt', self.sid, None, 'original-thread', 'original-turn', 'unknown',
             'synthetic original interruption', None, None, stamp, stamp))
        self.store.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?)',
            ('lifecycle-approval', self.sid, 'synthetic-generation', 'synthetic-rpc', 'synthetic-method', '{}', 'pending', None, stamp))
        projection = workflow.publish(self.sid)
        self.assertEqual((projection['state'], projection['subject_kind'], projection['subject_id']),
            ('blocked', 'interrupt', 'lifecycle-interrupt'))
        self.assertEqual(self.store.one('SELECT state FROM delivery_interrupt_intents WHERE id=?', ('lifecycle-interrupt',))['state'], 'unknown')
        self.store.execute("UPDATE delivery_interrupt_intents SET state='completed' WHERE id='lifecycle-interrupt'")
        self.assertEqual(workflow.publish(self.sid)['subject_id'], 'lifecycle-approval')
        self.store.execute("UPDATE approvals SET state='expired' WHERE id='lifecycle-approval'")
        self.store.patch_session(self.sid, status='running', active_turn='lifecycle-turn')
        self.assertEqual(workflow.publish(self.sid)['subject_id'], 'lifecycle-turn')
        self.store.patch_session(self.sid, status='idle', active_turn=None)
        self.assertEqual(workflow.publish(self.sid)['reason_code'], 'no_progress')

    def test_completed_role_reconciliation_passes_live_settings_before_dispatch(self):
        h = self.pending()
        self.kernel.settings.save({LIMIT_SETTING: 2})
        with patch.object(self.roles, 'dispatch') as dispatch:
            self.roles.reconcile(self.sid, [{'id': 'unrelated', 'status': 'completed'}], self.send, self.kernel.settings.value)
            dispatch.assert_not_called()
            self.roles.reconcile(self.sid, [{'id': self.turn, 'status': 'completed'}], self.send, self.kernel.settings.value)
            dispatch.assert_called_once_with(self.sid, self.send, self.kernel.settings.value)
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?', (h['id'],))['state'], 'pending')

    def test_before_dispatch_runs_again_even_when_handoff_already_counted(self):
        h = self.pending()
        gate = Mock(return_value=True)
        roles = Roles(self.store, gate)
        with self.assertRaises(ValueError):
            roles.dispatch(self.sid, Mock(side_effect=ValueError('offline send interruption')), self.kernel.settings.value)
        self.assertEqual(self.guard()['consecutive_unchanged'], 1)
        self.evidence()
        gate.return_value = False
        send = Mock()
        roles.dispatch(self.sid, send, self.kernel.settings.value)
        send.assert_not_called()
        self.assertEqual(gate.call_count, 2)
        self.assertEqual(self.store.one('SELECT counted FROM research_role_progress_checks WHERE handoff_id=?', (h['id'],))['counted'], 1)


if __name__ == '__main__':
    unittest.main()
