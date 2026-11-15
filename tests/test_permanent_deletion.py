from tests.test_project_management import ProjectManagementTests
from lab.deletion import remove
from lab.purge import purge
from lab.web import Application

class PermanentDeletionTests(ProjectManagementTests):
    def test_direct_permanent_delete_does_not_require_archive_or_trash(self):
        app=Application.__new__(Application);app.store=self.store;app.kernel=self.kernel
        value={'kind':'session','id':self.sid,'confirmation':'wrong'}
        with self.assertRaises(ValueError):app.route('POST','/api/delete/permanent',{},value)
        self.assertFalse(self.store.deleted('session',self.sid))
        self.store.patch_session(self.sid,status='unknown')
        value['confirmation']='确认'
        with self.assertRaises(ValueError):app.route('POST','/api/delete/permanent',{},value)
        self.store.patch_session(self.sid,status='idle')
        self.assertTrue(app.route('POST','/api/delete/permanent',{},value)['purged'])
        self.assertEqual(self.store.trash_items(),[])
        self.assertIsNotNone(self.store.one('SELECT id FROM projects WHERE id=?',(self.project['id'],)))

    def test_direct_project_delete_keeps_other_project(self):
        other=self.store.create_project('keep')
        purge(self.kernel,'project',self.project['id'],'确定',direct=True)
        self.assertEqual([p['id'] for p in self.store.navigation()['projects']],[other['id']])

    def test_project_removal_with_deleted_child_is_idempotent_and_keeps_child_deleted(self):
        child=self.store.create_session(self.project['id'],'codex')
        remove(self.kernel,'session',child['id'])
        self.assertTrue(remove(self.kernel,'project',self.project['id'])['deleted'])
        self.assertTrue(remove(self.kernel,'project',self.project['id'])['deleted'])
        self.store.restore_deleted('project',self.project['id'])
        self.assertEqual([s['id'] for s in self.store.navigation()['sessions']],[self.sid])
        self.assertTrue(self.store.deleted('session',child['id']))

    def test_deleted_child_unknown_execution_still_blocks_parent(self):
        child=self.store.create_session(self.project['id'],'codex')
        remove(self.kernel,'session',child['id'])
        self.store.patch_session(child['id'],status='unknown')
        with self.assertRaises(ValueError):remove(self.kernel,'project',self.project['id'])
        self.assertFalse(self.store.deleted('project',self.project['id']))

    def test_purge_route_requires_recycle_and_exact_name(self):
        app=Application.__new__(Application);app.store=self.store;app.kernel=self.kernel
        value={'kind':'session','id':self.sid,'confirmation':'wrong'}
        with self.assertRaises(ValueError):app.route('POST','/api/trash/purge',{},value)
        app.route('POST','/api/trash',{},value)
        with self.assertRaises(ValueError):app.route('POST','/api/trash/purge',{},value)
        value['confirmation']=' 确认 '
        self.assertTrue(app.route('POST','/api/trash/purge',{},value)['purged'])
        self.assertEqual(self.store.trash_items(),[])

    def test_delete_session_is_distinct_from_archive_and_restores_paused(self):
        remove(self.kernel,'session',self.sid)
        self.assertFalse(self.store.one('SELECT archived FROM sessions WHERE id=?',(self.sid,))['archived'])
        self.assertEqual(self.store.navigation()['sessions'],[])
        self.assertEqual(self.store.trash_items()[0]['kind'],'session')
        self.store.restore_deleted('session',self.sid)
        self.assertEqual(len(self.store.navigation()['sessions']),1)
        self.assertTrue(self.kernel.research.workflow.paused(self.sid))

    def test_permanent_project_deletes_only_its_local_rows_not_workspace_or_other_project(self):
        other=self.store.create_project('keep');sid=self.store.create_session(other['id'],'codex')['id']
        record=self.store.add_record(self.sid,{'kind':'question','title':'erase','content':'erase'}, {})
        keep=self.store.add_record(sid,{'kind':'question','title':'keep','content':'keep'}, {})
        path=self.store.safe_path(self.project['id'],'keep.txt');path.write_text('file retained')
        remove(self.kernel,'project',self.project['id'])
        with self.assertRaises(ValueError):purge(self.kernel,'project',self.project['id'],'wrong')
        self.assertIsNotNone(self.store.one('SELECT id FROM records WHERE id=?',(record['id'],)))
        result=purge(self.kernel,'project',self.project['id'],'确定')
        self.assertTrue(result['purged']);self.assertTrue(path.exists())
        self.assertIsNone(self.store.one('SELECT id FROM projects WHERE id=?',(self.project['id'],)))
        self.assertIsNotNone(self.store.one('SELECT id FROM records WHERE id=?',(keep['id'],)))
        self.assertEqual(self.store.rows('PRAGMA foreign_key_check'),[])

    def test_session_purge_preserves_sibling_and_project(self):
        sibling=self.store.create_session(self.project['id'],'codex')
        remove(self.kernel,'session',self.sid)
        purge(self.kernel,'session',self.sid,'确认')
        self.assertIsNone(self.store.one('SELECT id FROM sessions WHERE id=?',(self.sid,)))
        self.assertEqual(self.store.session(sibling['id'])['project_id'],self.project['id'])

    def test_cross_session_reference_blocks_purge_and_rolls_back(self):
        own=self.store.add_record(self.sid,{'kind':'question','title':'parent','content':'evidence'}, {})
        sibling=self.store.create_session(self.project['id'],'codex')
        self.store.add_record(sibling['id'],{'kind':'question','title':'child','content':'uses parent','parent_id':own['id']}, {})
        remove(self.kernel,'session',self.sid)
        with self.assertRaisesRegex(ValueError,'其他会话'):
            purge(self.kernel,'session',self.sid,'确认')
        self.assertIsNotNone(self.store.one('SELECT id FROM records WHERE id=?',(own['id'],)))
        self.assertIsNotNone(self.store.one('SELECT id FROM sessions WHERE id=?',(self.sid,)))
