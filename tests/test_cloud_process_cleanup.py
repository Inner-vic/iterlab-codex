"""Linux container process ownership, including torchrun-style session escapes.

Fresh stdlib sleepers only. No training, dependency installation or cloud calls.
"""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lab.cloud_runtime_worker import bounded_training


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux subreaper/process-session integration')
class CloudProcessCleanupTests(unittest.TestCase):
    @staticmethod
    def identity(pid):
        try:
            fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
            return fields[0],fields[19]
        except FileNotFoundError:return None

    def command(self,folder,parent_exit=False):
        child='''import json,os,sys,time
from pathlib import Path
pid=os.getpid();fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
Path(sys.argv[1]).write_text(json.dumps({'pid':pid,'start':fields[19]}))
time.sleep(30)
'''
        parent='''import subprocess,sys,time
from pathlib import Path
folder=Path(sys.argv[2])
for rank in range(3):
    subprocess.Popen([sys.executable,'-c',sys.argv[1],str(folder/(str(rank)+'.json'))],start_new_session=True)
deadline=time.monotonic()+5
while len(list(folder.glob('*.json')))<3 and time.monotonic()<deadline:time.sleep(.01)
assert len(list(folder.glob('*.json')))==3
'''+('' if parent_exit else 'time.sleep(30)\n')
        return shlex.join([sys.executable,'-c',parent,child,str(folder)])

    def assert_collected(self,folder):
        identities=[json.loads(path.read_text()) for path in folder.glob('*.json')]
        self.assertEqual(len(identities),3)
        alive=[]
        for item in identities:
            ref=self.identity(item['pid'])
            if ref and ref[0]!='Z' and ref[1]==item['start']:alive.append(item)
        # Even the regression's failing implementation must leave no sleeper.
        for item in alive:
            if (ref:=self.identity(item['pid'])) and ref[1]==item['start']:
                os.kill(item['pid'],signal.SIGKILL)
        self.assertEqual(alive,[], 'owned ranks escaped cleanup')

    def test_timeout_collects_independent_rank_sessions(self):
        with tempfile.TemporaryDirectory(prefix='iterlab-rank-timeout-') as temp:
            folder=Path(temp)
            self.assertEqual(bounded_training(self.command(folder),os.environ.copy(),2),124)
            self.assert_collected(folder)

    def test_preexisting_child_prevents_launch_and_is_untouched(self):
        sentinel=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            with tempfile.TemporaryDirectory(prefix='iterlab-rank-timeout-') as temp:
                folder=Path(temp)
                with self.assertRaisesRegex(RuntimeError,'preexisting_children'):
                    bounded_training(self.command(folder),os.environ.copy(),2)
                self.assertEqual(list(folder.iterdir()),[],'must refuse before launching training')
            self.assertIsNone(sentinel.poll(),'must not kill unrelated pre-existing child')
        finally:
            sentinel.kill();sentinel.wait(timeout=5)

    def test_kernel_without_pidfd_collects_independent_rank_sessions(self):
        import errno
        with tempfile.TemporaryDirectory(prefix='iterlab-rank-legacy-') as temp:
            folder=Path(temp)
            with patch.object(os,'pidfd_open',side_effect=OSError(errno.ENOSYS,'kernel has no pidfd'),create=True):
                self.assertEqual(bounded_training(self.command(folder),os.environ.copy(),2),124)
            self.assert_collected(folder)

    def test_zero_parent_exit_with_live_ranks_is_failure_and_collects_orphans(self):
        with tempfile.TemporaryDirectory(prefix='iterlab-rank-orphan-') as temp:
            folder=Path(temp)
            self.assertEqual(bounded_training(self.command(folder,True),os.environ.copy(),10),125)
            self.assert_collected(folder)

    def test_normal_command_preserves_exit_code(self):
        for code in (0,7):
            command=shlex.join([sys.executable,'-c','raise SystemExit('+str(code)+')'])
            self.assertEqual(bounded_training(command,os.environ.copy(),5),code)


if __name__=='__main__':unittest.main()
