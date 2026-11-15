import copy
from pathlib import Path
import unittest,uuid,subprocess
from unittest.mock import patch
from lab.store import Store
from lab.kernel import Kernel
from lab.web import Application
from lab.deletion import remove
from tests.test_core import FakeRPC

class UI8CoreTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex)
        self.kernel=Kernel(self.store,FakeRPC);self.kernel.settings.value['channel']='codex';self.kernel.settings.command=lambda *a:['fixture']
        self.pid=self.store.create_project('removal-isolated')['id'];self.sid=self.store.create_session(self.pid,'codex')['id']
        self.app=Application.__new__(Application);self.app.store=self.store;self.app.kernel=self.kernel
    def tearDown(self):self.kernel.close();self.store.close()
    def test_project_and_session_recoverable_deletion_keep_history_and_files(self):
        self.store.add_record(self.sid,{'kind':'question','title':'keep','content':'original evidence'},{})
        self.store.draft(self.sid,'unsent draft',0)
        file=Path(self.store.session(self.sid)['workspace'])/'keep.txt';file.write_text('keep')
        records=self.store.rows('SELECT * FROM records');draft=self.store.rows('SELECT * FROM drafts')
        remove(self.kernel,'session',self.sid)
        self.assertEqual(self.store.navigation()['sessions'],[])
        self.store.restore_deleted('session',self.sid)
        remove(self.kernel,'project',self.pid)
        self.assertEqual(self.store.navigation()['projects'],[]);self.assertEqual(self.store.navigation()['sessions'],[])
        self.assertEqual(self.store.rows('SELECT * FROM records'),records);self.assertEqual(self.store.rows('SELECT * FROM drafts'),draft)
        self.assertEqual(file.read_text(),'keep')
        with self.assertRaises(ValueError):self.kernel.send(self.sid,'must not dispatch')
        self.store.restore_deleted('project',self.pid)
        self.assertEqual(self.store.navigation()['sessions'][0]['id'],self.sid)
        self.assertTrue(self.kernel.research.workflow.paused(self.sid))
        self.assertIsNone(self.kernel.rpc)
    def test_unknown_execution_prevents_project_removal(self):
        self.store.patch_session(self.sid,status='unknown')
        with self.assertRaises(ValueError):remove(self.kernel,'project',self.pid)
        self.assertEqual(self.store.trash_items(),[])
    def test_archive_is_local_and_does_not_need_a_cli_connection(self):
        self.store.patch_session(self.sid,thread_id='preserved-native-thread')
        result=self.app.route('POST','/api/sessions/'+self.sid+'/archive',{}, {})
        self.assertTrue(result['archived']);self.assertIsNone(self.kernel.rpc)
        self.assertEqual(self.store.session(self.sid)['thread_id'],'preserved-native-thread')
        self.assertTrue(self.kernel.research.workflow.paused(self.sid))
    def test_model_catalog_refreshes_without_creating_turns(self):
        first=self.kernel.model_catalog('codex');second=self.kernel.model_catalog('codex')
        self.assertEqual(len(first['models']),4);self.assertEqual(second['models'],first['models'])
        self.assertIsNone(self.kernel.rpc) # Discovery is isolated from research.
        self.assertTrue(second['cached'])
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
    def test_other_channel_preview_does_not_change_saved_channel_or_account(self):
        old=copy.deepcopy(self.kernel.settings.value)
        with patch('lab.settings.Settings.command',return_value=['fixture']),patch('lab.settings.Settings.verify_channel'):
            result=self.kernel.model_catalog('ept')
        self.assertEqual(result['channel'],'ept');self.assertEqual(self.kernel.settings.value,old)
        self.assertIsNone(self.kernel.rpc);self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
    def test_ept_timeout_explains_boundary_without_raw_command(self):
        self.kernel.settings.value['channel']='ept'
        with patch('lab.settings.shutil.which',return_value='private-path/ept.exe'),patch('lab.settings.subprocess.run',side_effect=subprocess.TimeoutExpired(['secret-command'],30)):
            with self.assertRaisesRegex(ValueError,'尚未读取模型列表') as caught:self.kernel.settings.verify_channel()
        self.assertNotIn('private-path',str(caught.exception));self.assertNotIn('secret-command',str(caught.exception))

