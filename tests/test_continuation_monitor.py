"""Background liveness and isolation; no native model or platform requests."""
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from lab.kernel import Kernel
from lab.store import Store
from tests.test_core import FakeRPC


class ContinuationMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='iterlab-monitor-')
        self.store = Store(self.temp.name)
        self.kernel = Kernel(self.store, FakeRPC)
        self.monitor = self.kernel.continuation

    def tearDown(self):
        self.kernel.close()
        self.store.close()
        self.temp.cleanup()

    def test_transient_collection_error_does_not_kill_timer(self):
        # Two ordinary timer passes, not two direct model dispatches.
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, False, True]
        with patch.object(self.monitor, 'stop_event', stop), \
                patch.object(self.kernel.research.remote, 'poll') as poll, \
                patch.object(self.store.evidence, 'recover_local', side_effect=[OSError('private path'), None]) as recover, \
                patch.object(self.kernel, 'send') as send:
            self.monitor.start()
            self.monitor.thread.join(3)
            self.assertFalse(self.monitor.thread.is_alive())
            self.assertEqual(recover.call_count, 2)
            self.assertEqual(poll.call_count, 2)
            send.assert_not_called()

    def test_failed_stage_is_visible_and_other_stages_still_run(self):
        with patch.object(self.kernel.research.remote, 'poll', side_effect=ValueError('secret-value')), \
                patch.object(self.store.evidence, 'recover_local') as recover:
            self.monitor.monitor_tick()
        recover.assert_called_once_with()
        health = self.monitor.status()
        self.assertEqual((health['error_stage'], health['error_type'], health['failures']),
                         ('remote_observation', 'ValueError', 1))
        self.assertNotIn('secret-value', str(health))
        self.assertEqual(self.kernel.status()['continuation'], health)
        with patch.object(self.kernel.research.remote, 'poll'), \
                patch.object(self.store.evidence, 'recover_local'):
            self.monitor.monitor_tick()
        health = self.monitor.status()
        self.assertIsNotNone(health['last_failure'])
        self.assertIsNotNone(health['last_success'])
        self.assertEqual(health['failures'], 0)
        self.assertIsNone(health['error_type'])

    def test_session_failure_does_not_starve_other_owner(self):
        original_rows = self.store.rows
        def rows(sql, args=()):
            if sql.startswith('SELECT owner_session_id'):
                return [{'owner_session_id': 'broken'}, {'owner_session_id': 'healthy'}]
            return original_rows(sql, args)
        with patch.object(self.store, 'rows', side_effect=rows), \
                patch.object(self.kernel.research.remote, 'poll'), \
                patch.object(self.store.evidence, 'recover_local'), \
                patch.object(self.monitor, 'tick', side_effect=[ValueError('private'), None]) as tick:
            self.monitor.monitor_tick()
        self.assertEqual([call.args for call in tick.call_args_list], [('broken',), ('healthy',)])
        self.assertEqual(self.store.one('SELECT state FROM research_scheduler_status')['state'], 'blocked')
        self.assertEqual(self.monitor.status()['error_stage'], 'session_continuation')

    def test_database_failure_reporting_cannot_kill_timer_or_replay(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, False, True]
        with patch.object(self.monitor, 'stop_event', stop), \
                patch.object(self.monitor, 'monitor_tick', side_effect=[sqlite3.OperationalError('secret-db'), None]) as cycle, \
                patch.object(self.kernel, 'send') as send:
            self.monitor.start()
            self.monitor.thread.join(3)
            self.assertFalse(self.monitor.thread.is_alive())
            self.assertEqual(cycle.call_count, 2)
            send.assert_not_called()

    def test_health_survives_restart_without_creating_deliveries(self):
        with patch.object(self.kernel.research.remote, 'poll', side_effect=OSError('secret')), \
                patch.object(self.store.evidence, 'recover_local'):
            self.monitor.monitor_tick()
        self.kernel.close()
        self.store.close()
        self.store = Store(self.temp.name)
        self.kernel = Kernel(self.store, FakeRPC)
        health = self.kernel.continuation.status()
        self.assertEqual((health['error_stage'], health['error_type']), ('remote_observation', 'OSError'))
        self.assertFalse(health['running'])
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'), [])


if __name__ == '__main__':
    unittest.main()
