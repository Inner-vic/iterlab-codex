"""Remote protocol fault tests; no SSH, GPU allocation, or actual fitting."""
import contextlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import uuid

from lab import devspace,devspace_worker as worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.root=(Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex).resolve()
        self.root.mkdir(parents=True);self.addCleanup(shutil.rmtree,self.root)
        self.source=self.root/'source';self.source.mkdir()
        (self.source/'train.py').write_text('# protocol fixture, not training')
        (self.root/'.iterlab-space-uid').write_text('space-id')
        self.id=uuid.uuid4().hex
        manifest={'train.py':{'sha256':worker.sha256(self.source/'train.py'),'size':(self.source/'train.py').stat().st_size}}
        self.fingerprint=devspace.stage(self.source,self.id,{'uid':'space-id','gpus':1},manifest,'train.py',[],['result.json'],5,detached=True)
        with (self.source/'.iterlab-input.tar').open('rb') as stream:self.assertEqual(stream.read(2),b'\x1f\x8b')

    def invoke(self,mode):
        out=io.StringIO()
        with patch.object(worker,'BASE',self.root),contextlib.redirect_stdout(out):
            worker.control(mode,self.id,'space-id',self.fingerprint)
        return json.loads(out.getvalue())

    def submit(self):
        with (self.source/'.iterlab-input.tar').open('rb') as stream,patch.object(worker.sys,'stdin',SimpleNamespace(buffer=stream)), \
             patch.object(worker.subprocess,'Popen',return_value=Mock(pid=123)) as spawn,patch.object(worker,'process_identity',return_value='birth-1'):
            reply=self.invoke('submit')
        self.assertEqual(spawn.call_count,1)
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.kwargs['stdin'],worker.subprocess.DEVNULL)
        self.assertEqual(reply['state'],'running')
        return self.root/'iterlab-runs'/self.id

    def test_lost_ack_cannot_launch_twice_status_retains_original_identity(self):
        root=self.submit()
        with patch.object(worker,'process_identity',return_value='birth-1'),patch.object(worker.subprocess,'Popen') as spawn:
            value=self.invoke('status')
            self.assertEqual(value['run_id'],self.id);self.assertEqual(value['job_digest'],self.fingerprint)
            with (self.source/'.iterlab-input.tar').open('rb') as stream,patch.object(worker.sys,'stdin',SimpleNamespace(buffer=stream)):
                with self.assertRaises(FileExistsError):self.invoke('submit')
        spawn.assert_not_called();self.assertTrue((root/'.iterlab-launch.json').exists())

    def test_pid_reuse_or_missing_job_is_unknown_not_permission_to_relaunch(self):
        self.submit()
        with patch.object(worker,'process_identity',return_value='different-birth'),patch.object(worker.subprocess,'Popen') as spawn:
            self.assertEqual(self.invoke('status')['state'],'unknown')
        spawn.assert_not_called()
        self.id=uuid.uuid4().hex
        self.assertEqual(self.invoke('status')['state'],'unknown')

    def test_supervisor_terminal_receipt_then_read_only_collection(self):
        root=self.submit()
        def run_fixture(*args,**kwargs):
            self.assertEqual(kwargs['env']['CUDA_VISIBLE_DEVICES'],'0')
            self.assertEqual(kwargs['env']['ITERLAB_INPUT_DIR'],str(root))
            self.assertEqual(kwargs['env']['ITERLAB_OUTPUT_DIR'],str(root))
            self.assertEqual(kwargs['env']['ITERLAB_RUN_ID'],self.id)
            (root/'result.json').write_text('{"fixture":true}')
            return Mock(pid=321,wait=Mock(return_value=0))
        with patch.object(worker,'BASE',self.root),patch.dict(worker.os.environ,{'CUDA_VISIBLE_DEVICES':'0,1,2,3,4,5,6,7'}), \
             patch.object(worker.subprocess,'run',return_value=SimpleNamespace(stdout='\n'.join('GPU '+str(i)+': fixture' for i in range(8)))), \
             patch.object(worker.subprocess,'Popen',side_effect=run_fixture),patch.object(worker.os,'killpg',create=True), \
             patch.object(worker.signal,'SIGKILL',9,create=True),contextlib.redirect_stdout(io.StringIO()):
            worker.main(self.id,'space-id',staged=True,expected=self.fingerprint)
            with self.assertRaises(FileExistsError):worker.main(self.id,'space-id',staged=True,expected=self.fingerprint)
        with patch.object(worker.subprocess,'Popen') as spawn:
            result=self.invoke('status');self.assertEqual(result['state'],'completed')
            self.assertIn('result.json',result['files']);self.assertEqual(result['exit_code'],0)
        spawn.assert_not_called()

    def test_changed_frozen_job_or_space_identity_cannot_be_read_as_original(self):
        root=self.submit()
        (root/'.iterlab-job.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'identity'):self.invoke('status')
        (self.root/'.iterlab-space-uid').write_text('another-space')
        with self.assertRaisesRegex(ValueError,'identity'):self.invoke('status')


    def test_cpu_supervisor_does_not_require_gpu_tools(self):
        self.source=self.root/'source-cpu';self.source.mkdir()
        (self.source/'train.py').write_text('# CPU protocol fixture')
        manifest={'train.py':{'sha256':worker.sha256(self.source/'train.py'),'size':(self.source/'train.py').stat().st_size}}
        self.fingerprint=devspace.stage(self.source,self.id,{'uid':'space-id','gpus':0},manifest,'train.py',[],['result.json'],5,detached=True)
        root=self.submit()
        def execute(*args,**kwargs):
            self.assertEqual(kwargs['env']['CUDA_VISIBLE_DEVICES'],'')
            (root/'result.json').write_text('{"cpu_fixture":true}')
            return Mock(pid=321,wait=Mock(return_value=0))
        with patch.object(worker,'BASE',self.root),patch.object(worker.subprocess,'run') as gpu_tools, \
             patch.object(worker.subprocess,'Popen',side_effect=execute),patch.object(worker.os,'killpg',create=True), \
             patch.object(worker.signal,'SIGKILL',9,create=True),contextlib.redirect_stdout(io.StringIO()):
            worker.main(self.id,'space-id',staged=True,expected=self.fingerprint)
        gpu_tools.assert_not_called()
        self.assertEqual(self.invoke('status')['state'],'completed')


if __name__=='__main__':unittest.main()
