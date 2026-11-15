"""Read bootstrap may be slow; write uncertainty and account guards stay strict."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from lab.settings import Settings
from lab.lpai_training import LPAITraining


class ReadDeadlineTests(unittest.TestCase):
    def test_enterprise_check_waits_for_local_cli_but_rejects_personal_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('lab.settings.shutil.which',return_value='fixture-ept'):
                settings=Settings(Path(temp));settings.value.update(channel='ept',profile='fixture')
                before=copy.deepcopy(settings.value)
                with patch('lab.settings.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"value":"ept"}')) as runner:
                    settings.verify_channel()
                    self.assertEqual(runner.call_args.args[0],['fixture-ept','--ept-profile','fixture','config','get','codex.mode','--json'])
                    self.assertEqual(runner.call_args.kwargs['timeout'],90)
                with patch('lab.settings.subprocess.run',return_value=types.SimpleNamespace(returncode=0,stdout='{"value":"personal"}')):
                    with self.assertRaises(ValueError):settings.verify_channel()
                self.assertEqual(settings.value,before)

    def test_sdk_reads_get_cold_start_time_but_submit_timeout_is_not_replayed(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs))
            return types.SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':{'read':True}}))
        adapter=object.__new__(LPAITraining)
        adapter.company=types.SimpleNamespace(config=lambda:{'namespace':'fixture'},runner=runner)
        with patch('lab.lpai_training.Path.is_file',return_value=True):
            adapter.sdk('component',component_uid='component')
            self.assertEqual(calls[-1][1]['timeout'],90)
            adapter.sdk('submit',component_uid='component')
            self.assertEqual(calls[-1][1]['timeout'],55)
            failed=[]
            def timeout(argv,**kwargs):
                failed.append(kwargs);raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
            adapter.company.runner=timeout
            with self.assertRaisesRegex(TimeoutError,'写入结果未知'):adapter.sdk('submit',component_uid='component')
            self.assertEqual(len(failed),1);self.assertEqual(failed[0]['timeout'],55)


if __name__=='__main__':unittest.main()
