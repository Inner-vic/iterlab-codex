import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from lab.store import Store,now
from lab.kernel import Kernel
from lab import skill_management as skills
from tests.test_core import FakeRPC


class SessionToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name);self.kernel=Kernel(self.store,FakeRPC)
        self.pid=self.store.create_project('test')['id'];self.sid=self.store.create_session(self.pid,'codex')['id']
    def tearDown(self):self.kernel.close();self.store.close();self.temp.cleanup()
    def queued(self):
        with patch.object(self.kernel.pool,'submit'):return self.kernel.send(self.sid,'test instruction',delivery_id='queued-user')
    def test_promote_preserves_original_delivery_and_does_not_resend_unknown(self):
        item=self.queued();self.store.patch_session(self.sid,active_turn='current',status='running')
        with patch.object(self.kernel.pool,'submit') as submit:
            result=self.kernel.promote_delivery(self.sid,item['id'])
            submit.assert_called_once_with(self.kernel._steer,item['id'])
        self.assertEqual(result['mode'],'steer');self.assertEqual(result['id'],item['id'])
        self.assertEqual(self.store.one('SELECT count(*) AS n FROM deliveries')['n'],1)
        self.store.execute("UPDATE deliveries SET state='unknown'")
        with self.assertRaises(ValueError):self.kernel.promote_delivery(self.sid,item['id'])
        with self.assertRaises(ValueError):self.kernel.cancel_delivery(self.sid,item['id'])
    def test_rejection_dismissal_retains_evidence(self):
        item=self.queued();self.store.execute("UPDATE deliveries SET state='rejected',error='original' WHERE id=?",(item['id'],))
        self.kernel.dismiss_delivery(self.sid,item['id'])
        self.assertEqual(self.store.one('SELECT error FROM deliveries')['error'],'original')
        self.assertEqual(self.store.one('SELECT state FROM deliveries')['state'],'rejected')
    def test_rename_keeps_thread_and_records_and_detects_stale_title(self):
        self.store.patch_session(self.sid,thread_id='keep-thread');before=self.store.session(self.sid)
        result=self.store.rename_session(self.sid,' renamed ',before['title'])
        self.assertEqual(result['title'],'renamed');self.assertEqual(result['thread_id'],'keep-thread')
        self.assertEqual(result['project_id'],self.pid)
        with self.assertRaises(ValueError):self.store.rename_session(self.sid,'other',before['title'])
        with self.assertRaises(ValueError):self.store.rename_session(self.sid,'  ')
    def source(self):
        path=Path(self.temp.name)/'source';path.mkdir();(path/'SKILL.md').write_text('---\nname: demo\ndescription: local fixture\n---\n# Test\n',encoding='utf-8');return path
    def test_skill_import_is_project_scoped_and_disable_preserves_copy(self):
        source=self.source();receipt=skills.import_local(self.store,self.sid,str(source))
        self.assertEqual(Path(receipt['path']),skills.location(self.store,self.sid)/'demo/SKILL.md')
        self.assertTrue(source.is_dir())
        with self.assertRaises(ValueError):skills.import_local(self.store,self.sid,str(source))
        skills.disable(self.store,self.sid,'demo')
        self.assertFalse((skills.location(self.store,self.sid)/'demo').exists())
        self.assertEqual(len(list((self.store.root/'disabled-skills').rglob('SKILL.md'))),1)
    def test_skill_import_rejects_credentials_and_active_turn(self):
        source=self.source();(source/'credentials.json').write_text('{}')
        with self.assertRaises(ValueError):skills.import_local(self.store,self.sid,str(source))
        (source/'credentials.json').unlink();self.store.patch_session(self.sid,active_turn='active',status='running')
        with self.assertRaises(ValueError):skills.import_local(self.store,self.sid,str(source))
