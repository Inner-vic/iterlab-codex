"""Phase timing remains diagnostic; original wait budget is not relaxed."""
import unittest
from unittest.mock import patch
from pathlib import Path
import subprocess
from tests import test_autonomy as fixtures


class RunTimingTests(unittest.TestCase):
    setUp=fixtures.AutonomyTests.setUp
    tearDown=fixtures.AutonomyTests.tearDown
    record=fixtures.AutonomyTests.record
    execute=fixtures.AutonomyTests.execute

    def test_actual_short_process_records_separate_phases_and_original_limit(self):
        result=self.execute(self.first,'timing-short')
        self.assertEqual(result['state'],'completed',result)
        timing=result['timing']
        self.assertEqual(timing['wait_limit_seconds'],5)
        self.assertFalse(timing['timeout_observed'])
        for name in ['input_snapshot_seconds','process_spawn_seconds','process_ownership_seconds','wait_seconds','artifact_verification_seconds']:
            self.assertGreaterEqual(timing['phases'][name],0)
        self.assertIn('not a total-wall guarantee',timing['limit_scope'])
        self.assertEqual(self.r.status(self.sid)['resources']['remaining_fits'],2)

    def test_real_timeout_preserves_budget_and_termination_evidence(self):
        (self.workspace/'train.py').write_text('import time\ntime.sleep(3)\n',encoding='utf-8')
        result=self.r.run(self.sid,{**self.request,'plan_id':self.first['id'],'timeout':1},'timing-timeout')
        self.assertEqual(result['state'],'timed_out',result)
        self.assertTrue(result['timing']['timeout_observed'])
        self.assertEqual(result['timing']['wait_limit_seconds'],1)
        self.assertIn('termination_seconds',result['timing']['phases'])
        self.assertEqual(self.r.resources(self.first)['used_fits'],1)
        self.assertFalse(self.r.processes)

    def test_failed_spawn_is_not_success_and_does_not_invent_wait_duration(self):
        with patch('lab.research.subprocess.Popen',side_effect=OSError('isolated spawn failure')):
            result=self.execute(self.first,'failed-spawn')
        self.assertEqual(result['state'],'failed')
        self.assertIsNone(result['timing']['process_wall_seconds'])
        self.assertNotIn('wait_seconds',result['timing']['phases'])
        self.assertEqual(self.r.resources(self.first)['used_fits'],1)

    def test_timeout_with_late_zero_exit_remains_timeout(self):
        class Peer:
            pid=987654
            returncode=0
            def wait(self,timeout):raise subprocess.TimeoutExpired('fixture',timeout)
            def poll(self):return 0
        class Job:
            def __init__(self,p):pass
            def close(self):pass
        with patch('lab.research.subprocess.Popen',return_value=Peer()),patch('lab.research.OwnedJob',Job),patch.object(self.r,'_stop'):
            result=self.execute(self.first,'late-zero')
        self.assertEqual(result['state'],'timed_out')
        self.assertEqual(result['exit_code'],0)
        self.assertTrue(result['timing']['timeout_observed'])
        self.assertEqual(self.r.resources(self.first)['used_fits'],1)


if __name__=='__main__':unittest.main()
