"""Execution dependencies and terminal evidence integrity, without real platforms."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_autonomy as fixtures
from lab.research import digest as original_digest


class ExecutionIntegrityTests(unittest.TestCase):
    setUp=fixtures.AutonomyTests.setUp
    tearDown=fixtures.AutonomyTests.tearDown
    record=fixtures.AutonomyTests.record
    execute=fixtures.AutonomyTests.execute

    def trial(self,title,**extra):
        return self.record('trial',title,plan_id=self.first['id'],**extra)

    def run_trial(self,trial,operation):
        return self.r.run(self.sid,{**self.request,'plan_id':self.first['id'],'trial_id':trial['id']},operation)

    def test_unexecuted_declared_predecessor_blocks_new_run(self):
        first=self.trial('必须先完成的实验')
        next_trial=self.trial('依赖它的实验',depends_on_trial_ids=[first['id']])
        with self.assertRaisesRegex(ValueError,'前置实验'):
            self.run_trial(next_trial,'before-dependency')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_failed_dependency_is_not_successful_preparation(self):
        first=self.trial('执行失败的前置实验')
        (self.workspace/'train.py').write_text('raise RuntimeError("fixture")',encoding='utf-8')
        result=self.run_trial(first,'failed-dependency')
        self.assertEqual(result['state'],'failed')
        next_trial=self.trial('尚不可执行',depends_on_trial_ids=[first['id']])
        with self.assertRaisesRegex(ValueError,'前置实验'):
            self.run_trial(next_trial,'cannot-hide-failure')
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)

    def test_successful_archived_dependency_allows_exact_successor(self):
        first=self.trial('已验证的前置实验');result=self.run_trial(first,'dependency-complete')
        next_trial=self.trial('依赖结果的实验',depends_on_trial_ids=[first['id']])
        self.assertEqual(self.run_trial(next_trial,'dependency-successor')['state'],'completed')
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),2)
        self.assertEqual(self.store.one('SELECT state FROM research_runs WHERE id=?',(result['run_id'],))['state'],'completed')

    def test_unresolved_platform_operation_blocks_direct_local_execution(self):
        from lab.store import dumps,now
        for state in ['requested','unknown','pending_approval','submitted']:
            with self.subTest(state):
                id='platform-'+state;stamp=now()
                try:
                    self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',
                        (id,self.sid,'fixture',dumps({'action':'lpai_start'}),state,None,stamp,stamp))
                    with patch('lab.research.subprocess.Popen',side_effect=AssertionError('未决操作不能开始本地计算')) as process:
                        with self.assertRaisesRegex(ValueError,'平台操作'):
                            self.execute(self.first,'local-with-'+state)
                        process.assert_not_called()
                    self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
                    self.assertEqual(self.store.one('SELECT state FROM company_operations WHERE id=?',(id,))['state'],state)
                finally:self.store.execute('DELETE FROM company_operations WHERE id=?',(id,))

    def test_output_hash_failure_saves_terminal_receipt_instead_of_running_forever(self):
        def broken(path):
            if Path(path).name=='metrics.json':raise PermissionError('fixture artifact unavailable')
            return original_digest(path)
        with patch('lab.research.digest',broken):result=self.execute(self.first,'output-unreadable')
        self.assertEqual(result['state'],'artifacts_missing')
        self.assertTrue(result['artifact_errors'])
        row=self.store.one('SELECT state,receipt FROM research_runs')
        self.assertEqual(row['state'],'artifacts_missing')
        self.assertEqual(json.loads(row['receipt'])['run_id'],result['run_id'])
        self.assertFalse(self.r.processes)

    def test_declared_symlink_is_not_an_original_output(self):
        import os
        if os.name=='nt':self.skipTest('Windows symlink privilege is not assumed by this fixture')
        (self.workspace/'train.py').write_text(
            "from pathlib import Path\nPath('real.json').write_text('{}')\nPath('metrics.json').symlink_to('real.json')\n",encoding='utf-8')
        result=self.execute(self.first,'symlink-not-output')
        self.assertEqual(result['state'],'artifacts_missing')
        self.assertIn('metrics.json',result['missing_outputs'])
        self.assertFalse(any(item['path'].endswith('/metrics.json') for item in result['outputs']))

    def test_owned_descendant_does_not_continue_after_parent_exits(self):
        import os
        import time
        if os.name=='nt':self.skipTest('POSIX process-group fixture; Windows retains OwnedJob')
        child=None
        (self.workspace/'train.py').write_text(
            "import subprocess,sys\nfrom pathlib import Path\n"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
            "Path('child.pid').write_text(str(p.pid))\nPath('metrics.json').write_text('{}')\n",encoding='utf-8')
        try:
            result=self.execute(self.first,'owned-child-cleanup')
            child=int((self.workspace/result['directory']/'child.pid').read_text())
            def alive():
                path=Path('/proc')/str(child)/'stat'
                if not path.exists():return False
                try:return path.read_text().rsplit(')',1)[1].strip().split()[0] not in {'Z','X'}
                except FileNotFoundError:return False
            deadline=time.monotonic()+1
            while alive() and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(alive(),'own descendant remained executing after evidence verification began')
            self.assertEqual(result['state'],'completed')
        finally:
            if child:
                try:os.kill(child,9)
                except ProcessLookupError:pass

    def test_unconfirmed_owned_tree_cleanup_saves_unknown_and_cannot_replay(self):
        class Job:
            def __init__(self,process):pass
            def close(self):raise PermissionError('fixture cleanup not confirmed')
        with patch('lab.research.OwnedJob',Job):result=self.execute(self.first,'cleanup-unknown')
        self.assertEqual(result['state'],'unknown')
        self.assertTrue(result['ownership_errors'])
        self.assertEqual(self.store.one('SELECT state FROM research_runs')['state'],'unknown')
        self.assertFalse(self.store.evidence.summary('local',result['run_id'])['analysis_ready'])
        self.assertEqual(self.execute(self.first,'cleanup-unknown')['run_id'],result['run_id'])
        with self.assertRaises(ValueError):self.execute(self.first,'no-replacement-cleanup')
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)

    def test_receipt_write_failure_keeps_original_durable_result_and_blocks_analysis(self):
        write=Path.write_text
        def failure(path,*args,**kwargs):
            if path.name=='receipt.json':raise PermissionError('fixture receipt unavailable')
            return write(path,*args,**kwargs)
        with patch.object(Path,'write_text',failure):result=self.execute(self.first,'receipt-unwritable')
        self.assertEqual(self.store.one('SELECT state FROM research_runs')['state'],result['state'])
        self.assertNotIn(result['state'],{'requested','running','unknown'})
        self.assertIsNotNone(self.store.evidence.blocking(self.pid))
        self.assertEqual(self.execute(self.first,'receipt-unwritable')['run_id'],result['run_id'])
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),1)


if __name__=='__main__':unittest.main()
