"""Offline lifecycle read fixtures; no credential contents, platform or model."""
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest.mock import patch
import uuid

from lab.company import Company
from lab.store import Store, dumps, now


class SpaceObserverTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).resolve().parents[1] / '.evidence/tests' / uuid.uuid4().hex
        self.store = Store(self.directory)
        self.project = self.store.create_project('isolated-space-observer')
        self.sid = self.store.create_session(self.project['id'], 'ept')['id']
        self.marker = 'credential-metadata-fixture'
        for name in ('lab.space_observer.credential_revision', 'lab.platform_health.credential_revision'):
            mock = patch(name, side_effect=lambda kind: self.marker)
            mock.start()
            self.addCleanup(mock.stop)
        self.phase = 'Stopped'
        self.uid = 'original-space'
        self.name = 'fixture-space'
        self.namespace = 'vc-chassis'
        self.failure = None
        self.on_read = None
        self.on_write = None
        self.calls = []
        self.company = Company(self.store, runner=self.runner, locator=lambda name: name)
        self.company.configure({'revision': 0, 'lpai_enabled': True})
        self.company.bind_space(self.sid, self.name, self.uid)

    def tearDown(self):
        self.company.close()
        self.store.close()

    def runner(self, args, **kwargs):
        self.calls.append(args)
        self.assertIn('devspaces', args)
        self.assertFalse(self.store.lock._is_owned(), 'network must not hold the global store lock')
        if 'get' in args:
            callback, self.on_read = self.on_read, None
            if callback:
                callback()
            if self.failure:
                if isinstance(self.failure, BaseException):
                    raise self.failure
                return subprocess.CompletedProcess(args, 1, '', self.failure)
            value = {'status': 'ok', 'data': {'metadata': {'uid': self.uid, 'name': self.name, 'namespace': self.namespace},
                                              'status': {'phase': self.phase}, 'spec': {}}}
        else:
            self.assertTrue('start' in args or 'stop' in args)
            if self.on_write:
                self.on_write()
            value = {'status': 'pending_approval'}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), '')

    def start(self, operation_id='original-start'):
        return self.company.perform(self.sid, {'action': 'lpai_start'}, operation_id, True)

    def operation(self, operation_id='original-start'):
        return self.store.one('SELECT * FROM company_operations WHERE id=?', (operation_id,))

    def read(self, operation_id='original-start'):
        return self.store.one('SELECT * FROM company_space_reads WHERE operation_id=?', (operation_id,))

    def bound(self):
        return self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'", (self.project['id'],))

    def test_monitor_reconciles_original_start_and_stop_with_only_exact_gets(self):
        self.assertEqual(self.start()['state'], 'pending_approval')
        self.phase = 'Ready'
        self.calls.clear()
        kernel = types.SimpleNamespace(send=lambda *a, **kw: self.fail('space observer cannot wake an agent'))
        self.company.monitor_tick(kernel)
        self.assertEqual(self.operation()['state'], 'observed_active')
        self.assertEqual(json.loads(self.bound()['observation'])['phase'], 'Ready')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][-6:], ['devspaces', 'get', '--namespace', 'vc-chassis', '--name', 'fixture-space'])
        result = self.company.stop_space(self.sid, self.uid, 'original-stop', True, self.company.authority(self.sid))
        self.assertEqual(result['state'], 'pending_approval')
        self.phase = 'Stopped'
        self.calls.clear()
        self.company.monitor_tick(kernel)
        self.assertEqual(self.operation('original-stop')['state'], 'observed_stopped')
        observation = self.company.context(self.sid)['devspace_observation']
        self.assertEqual(observation['operation_id'], 'original-stop')
        self.assertEqual(observation['state'], 'observed_stopped')
        self.assertTrue(observation['last_checked_at'])
        self.assertTrue(observation['last_success_at'])
        self.assertEqual(len(self.calls), 1)

    def test_requested_crash_recovery_survives_restart_without_resending(self):
        self.start()
        self.store.execute("UPDATE company_operations SET state='requested',result=NULL WHERE id='original-start'")
        identity = self.read()['identity']
        self.company.close()
        self.company = Company(self.store, runner=self.runner, locator=lambda name: name)
        self.phase = 'Running'
        self.calls.clear()
        self.company.space_observer.poll(100)
        self.assertEqual(self.operation()['state'], 'observed_active')
        self.assertEqual(self.read()['identity'], identity)
        self.assertEqual(len(self.calls), 1)
        self.assertIn('get', self.calls[0])

    def test_changed_environment_auth_process_identity_does_not_reconcile(self):
        with patch('lab.space_observer.process_auth_epoch', return_value='original-process'):
            self.start()
        self.calls.clear()
        self.phase = 'Running'
        with patch('lab.space_observer.process_auth_epoch', return_value='replacement-process'):
            self.company.space_observer.poll(100)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.operation()['state'], 'pending_approval')
        self.assertEqual(self.read()['state'], 'authority_changed')

    def test_only_explicit_manual_readback_can_confirm_facts_after_credentials_change(self):
        self.company.perform(self.sid, {'action': 'lpai_start'}, 'original-start', True, approval_source='native_user')
        original = self.operation()
        self.marker = 'replacement-credential-metadata'
        self.phase = 'Running'
        self.calls.clear()
        self.company.space_observer.poll(100)
        self.assertEqual(self.calls, [])
        self.company.lpai('space', name=self.name)
        self.assertEqual(self.operation()['state'], 'pending_approval')
        self.company.refresh_space(self.sid, self.uid)
        result = json.loads(self.operation()['result'])
        self.assertEqual(self.operation()['state'], 'observed_active')
        self.assertEqual(result['source'], 'manual_readback')
        self.assertEqual(result['approval_source'], 'native_user')
        self.assertIn('不推断审批结果', result['message'])
        self.assertEqual(self.operation()['request'], original['request'])
        self.assertEqual(self.operation()['request_hash'], original['request_hash'])
        self.assertTrue(all('get' in call for call in self.calls))

    def test_manual_running_readback_cannot_resolve_pending_stop(self):
        self.phase = 'Running'
        self.company.stop_space(self.sid, self.uid, 'original-stop', True, self.company.authority(self.sid))
        original = self.operation('original-stop')['request']
        self.marker = 'replacement-credential-metadata'
        self.calls.clear()
        self.company.refresh_space(self.sid, self.uid)
        self.assertEqual(self.operation('original-stop')['state'], 'pending_approval')
        self.phase = 'Stopped'
        self.company.refresh_space(self.sid, self.uid)
        result = json.loads(self.operation('original-stop')['result'])
        self.assertEqual(result['state'], 'observed_stopped')
        self.assertEqual(result['source'], 'manual_readback')
        self.assertEqual(result['approval_source'], 'human_ui')
        self.assertEqual(self.operation('original-stop')['request'], original)
        self.assertTrue(all('get' in call for call in self.calls))

    def test_manual_readback_rejects_recreated_uid_and_binding_changes_during_read(self):
        self.start()
        self.marker = 'replacement-credential-metadata'
        self.phase = 'Running'
        saved_bound = self.bound()
        for change in ('uid', 'name', 'binding'):
            with self.subTest(change=change):
                if change == 'uid': self.uid = 'same-name-recreated-uid'
                elif change == 'name': self.name = 'another-space'
                else: self.on_read = lambda: self.store.execute("UPDATE company_bindings SET uid='rebound-during-read' WHERE kind='lpai'")
                with self.assertRaises(ValueError):
                    self.company.refresh_space(self.sid, 'original-space')
                self.assertEqual(self.operation()['state'], 'pending_approval')
                self.assertEqual(json.loads(self.bound()['observation']), json.loads(saved_bound['observation']))
                self.uid = 'original-space'
                self.name = 'fixture-space'
                self.store.execute('INSERT OR REPLACE INTO company_bindings VALUES(?,?,?,?,?,?,?)', tuple(saved_bound.values()))

    def test_manual_readback_rejects_authentication_change_during_read(self):
        self.start()
        self.phase = 'Running'
        saved_bound = self.bound()
        self.on_read = lambda: setattr(self, 'marker', 'replacement-during-read')
        with self.assertRaisesRegex(ValueError, '连接已变化'):
            self.company.refresh_space(self.sid, self.uid)
        self.assertEqual(self.operation()['state'], 'pending_approval')
        self.assertEqual(self.bound(), saved_bound)

    def test_requested_inflight_cannot_be_reconciled_by_preflight_or_background(self):
        def writing():
            self.assertEqual(self.operation()['state'], 'requested')
            self.phase = 'Running'
            count = len(self.calls)
            self.company.space_observer.poll(100)
            self.assertEqual(len(self.calls), count)
            self.company.lpai('space', name=self.name)
            self.assertEqual(self.operation()['state'], 'requested')
        self.on_write = writing
        self.assertEqual(self.start()['state'], 'pending_approval')
        self.company.space_observer.poll(100)
        self.assertEqual(self.operation()['state'], 'observed_active')
        self.assertEqual(sum('start' in call for call in self.calls), 1)

    def test_stopped_is_neither_start_success_nor_approval_rejection(self):
        self.start()
        self.calls.clear()
        self.company.space_observer.poll(100)
        self.assertEqual(self.operation()['state'], 'pending_approval')
        self.assertEqual(self.read()['state'], 'waiting')
        self.assertEqual(self.read()['last_success_at'], self.read()['last_checked_at'])
        self.assertEqual(self.read()['next_check'], 130)
        self.assertEqual(sum('start' in call for call in self.calls), 0)

    def test_read_failures_back_off_durably_without_persisting_raw_errors(self):
        self.start()
        self.failure = subprocess.TimeoutExpired('fake-get', 25)
        self.calls.clear()
        self.company.space_observer.poll(100)
        self.assertEqual(self.read()['next_check'], 115)
        self.assertEqual(self.read()['failures'], 1)
        self.assertEqual(self.read()['error_type'], 'TimeoutError')
        self.company.space_observer.poll(114)
        self.assertEqual(len(self.calls), 1)
        self.company.close()
        self.company = Company(self.store, runner=self.runner, locator=lambda name: name)
        self.company.space_observer.poll(115)
        self.assertEqual(self.read()['next_check'], 145)
        self.assertEqual(self.read()['failures'], 2)
        self.assertEqual(self.operation()['state'], 'pending_approval')
        self.assertNotIn('fake-get', dumps(self.read()))

    def test_space_observer_failure_does_not_starve_training_monitor(self):
        with patch.object(self.company.space_observer, 'poll', side_effect=RuntimeError('offline fixture')), \
             patch.object(self.company, 'poll_watches') as watches:
            self.company.monitor_tick(None)
        watches.assert_called_once_with(None)
        health = self.store.one('SELECT * FROM company_monitor_health WHERE singleton=1')
        self.assertEqual(health['error_type'], 'RuntimeError')
        self.assertEqual(health['failures'], 1)

    def test_notifications_follow_state_and_phase_changes_without_same_phase_spam(self):
        self.start()
        def notifications():
            return self.store.rows("SELECT * FROM events WHERE kind IN ('company.space_observation','company.space_observed')")
        self.company.space_observer.poll(100)
        count = len(notifications())
        self.assertEqual(count, 1)
        self.company.space_observer.poll(130)
        self.assertEqual(len(notifications()), count)
        self.phase = 'Starting'
        self.company.space_observer.poll(160)
        count += 1
        self.assertEqual(len(notifications()), count)
        self.company.space_observer.poll(190)
        self.assertEqual(len(notifications()), count)
        self.failure = subprocess.TimeoutExpired('fake-get', 25)
        self.company.space_observer.poll(220)
        count += 1
        self.assertEqual(len(notifications()), count)
        self.company.space_observer.poll(235)
        self.assertEqual(len(notifications()), count)
        self.failure = None
        self.company.space_observer.poll(265)
        count += 1
        self.assertEqual(len(notifications()), count)
        self.phase = 'Running'
        self.company.space_observer.poll(295)
        count += 1
        self.assertEqual(len(notifications()), count)
        self.assertEqual(self.operation()['state'], 'observed_active')
        self.assertTrue(all(json.loads(event['payload'])['project_id'] == self.project['id'] for event in notifications()))

    def test_exact_not_found_updates_projection_but_preserves_original_request(self):
        self.start()
        operation = self.operation()
        self.failure = 'Error: HTTP 404: [DEVSPACE_NOT_FOUND] devspace not found'
        self.company.space_observer.poll(100)
        self.assertEqual(json.loads(self.bound()['observation'])['phase'], 'not_found')
        self.assertEqual(self.operation(), operation)
        self.assertEqual(self.read()['state'], 'not_found')
        self.assertEqual(self.read()['next_check'], 400)

    def test_other_uids_names_namespaces_and_unrelated_404_are_never_adopted(self):
        self.start()
        bound = self.bound()
        for change in ('uid', 'name', 'namespace', 'failure'):
            with self.subTest(change=change):
                self.store.execute("UPDATE company_space_reads SET next_check=0 WHERE operation_id='original-start'")
                old = getattr(self, change)
                setattr(self, change, 'Error: HTTP 404: [NOT_FOUND] notebooks/another-space not found' if change == 'failure' else 'another-object')
                self.company.space_observer.poll(100)
                self.assertEqual(self.operation()['state'], 'pending_approval')
                self.assertEqual(self.read()['state'], 'read_failed')
                self.assertEqual(self.bound(), bound)
                setattr(self, change, old)

    def test_changed_authority_before_or_during_read_preserves_operations_and_binding(self):
        self.start()
        saved_bound = self.bound()
        saved_config = self.store.one('SELECT * FROM company_config')
        for timing in ('before', 'during'):
            for change in ('credential', 'revision', 'binding', 'unbound', 'project_removed'):
                with self.subTest(timing=timing, change=change):
                    self.store.execute("UPDATE company_space_reads SET state='waiting',next_check=0 WHERE operation_id='original-start'")
                    def mutate():
                        if change == 'credential': self.marker = 'new-credential-metadata'
                        elif change == 'revision': self.store.execute('UPDATE company_config SET revision=revision+1')
                        elif change == 'binding': self.store.execute("UPDATE company_bindings SET uid='new-binding' WHERE kind='lpai'")
                        elif change == 'unbound': self.store.execute("DELETE FROM company_bindings WHERE kind='lpai'")
                        else: self.store.execute('INSERT INTO workbench_trash VALUES(?,?,?)', ('project', self.project['id'], now()))
                    if timing == 'before': mutate()
                    else: self.on_read = mutate
                    changed = self.bound() if timing == 'before' else None
                    self.phase = 'Running'
                    self.calls.clear()
                    self.company.space_observer.poll(100)
                    self.assertEqual(self.operation()['state'], 'pending_approval')
                    self.assertEqual(self.read()['state'], 'owner_unavailable' if change == 'project_removed' else 'authority_changed')
                    self.assertEqual(len(self.calls), 0 if timing == 'before' else 1)
                    if self.bound(): self.assertEqual(self.bound()['observation'], saved_bound['observation'])
                    if changed is not None: self.assertEqual(self.bound(), changed)
                    self.marker = 'credential-metadata-fixture'
                    self.store.execute('UPDATE company_config SET revision=?', (saved_config['revision'],))
                    self.store.execute('DELETE FROM workbench_trash')
                    self.store.execute('INSERT OR REPLACE INTO company_bindings VALUES(?,?,?,?,?,?,?)', tuple(saved_bound.values()))

    def test_legacy_requested_operation_can_only_be_recovered_by_manual_exact_read(self):
        request = {'action': 'lpai_start', 'bound_targets': [{key: self.bound()[key] for key in ('kind', 'scope', 'uid', 'name')}]}
        self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',
                           ('legacy', self.sid, 'legacy-hash', dumps(request), 'requested', None, now(), now()))
        self.phase = 'Running'
        self.calls.clear()
        self.company.space_observer.poll(100)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.company.context(self.sid)['devspace_observation']['state'], 'legacy_manual_only')
        self.company.lpai('space', name=self.name)
        self.assertEqual(self.operation('legacy')['state'], 'observed_active')


if __name__ == '__main__':
    unittest.main()
