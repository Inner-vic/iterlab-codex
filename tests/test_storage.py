import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from lab.storage import cold_backup,create_snapshot,file_sha,restore_snapshot,verify_snapshot,retained_backups


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='iterlab-storage-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.data=self.root/'.data';self.data.mkdir()
        with closing(sqlite3.connect(self.data/'workbench.sqlite3')) as db:
            db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,content TEXT)')
            db.execute('INSERT INTO records VALUES(?,?)',('original','原对话及负面实验结果'*100))
            db.commit()
        (self.data/'settings.json').write_text(json.dumps({'channel':'ept','profile':'default'}),encoding='utf8')

    def test_snapshot_roundtrip_preserves_exact_database_settings_and_counts(self):
        source=file_sha(self.data/'workbench.sqlite3')
        result=create_snapshot(self.data)
        manifest=verify_snapshot(result['path'])
        self.assertEqual(manifest['table_counts'],{'records':1})
        restored=restore_snapshot(result['path'],self.root/'restored')
        self.assertEqual(restored['integrity'],'ok')
        with closing(sqlite3.connect(self.root/'restored/workbench.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT content FROM records').fetchone()[0],'原对话及负面实验结果'*100)
        self.assertEqual((self.root/'restored/settings.json').read_bytes(),(self.data/'settings.json').read_bytes())
        self.assertEqual(file_sha(self.data/'workbench.sqlite3'),source)
        self.assertFalse(list((self.data/'backups/snapshots').glob('*.partial')))

    def test_unchanged_snapshot_reuses_verified_archive_and_changes_create_new_one(self):
        first=create_snapshot(self.data);second=create_snapshot(self.data)
        self.assertEqual(first['path'],second['path']);self.assertTrue(second['reused'])
        with closing(sqlite3.connect(self.data/'workbench.sqlite3')) as db:
            db.execute("INSERT INTO records VALUES('new','new result')");db.commit()
        third=create_snapshot(self.data)
        self.assertFalse(third['reused']);self.assertNotEqual(first['path'],third['path'])
        self.assertEqual(verify_snapshot(first['path'])['table_counts']['records'],1)
        self.assertEqual(verify_snapshot(third['path'])['table_counts']['records'],2)

    def test_wal_snapshot_includes_committed_rows_without_stopping_original_connection(self):
        with closing(sqlite3.connect(self.data/'workbench.sqlite3')) as db:
            db.execute('PRAGMA journal_mode=WAL');db.execute("INSERT INTO records VALUES('wal','committed in WAL')");db.commit()
            result=create_snapshot(self.data)
            self.assertEqual(verify_snapshot(result['path'])['table_counts']['records'],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0],2)

    def test_budget_failure_never_deletes_original_or_old_archive_and_cleans_owned_partial(self):
        first=create_snapshot(self.data);sha=file_sha(first['path'])
        with self.assertRaisesRegex(ValueError,'budget'):create_snapshot(self.data,max_total_bytes=1)
        self.assertEqual(file_sha(first['path']),sha)
        with closing(sqlite3.connect(self.data/'workbench.sqlite3')) as db:
            db.execute("INSERT INTO records VALUES('new','new result')");db.commit()
        # Enough for the old archive but not one input chunk of the new one.
        with self.assertRaisesRegex(ValueError,'budget'):
            create_snapshot(self.data,max_total_bytes=Path(first['path']).stat().st_size+1)
        self.assertEqual(file_sha(first['path']),sha)
        self.assertFalse(list((self.data/'backups/snapshots').glob('*.partial')))
        self.assertFalse((self.data/'backups/snapshots/snapshot.lock').exists())

    def test_restore_refuses_existing_destination_before_overwrite(self):
        result=create_snapshot(self.data);sha=file_sha(self.data/'workbench.sqlite3')
        with self.assertRaisesRegex(ValueError,'must not exist'):restore_snapshot(result['path'],self.data)
        self.assertEqual(file_sha(self.data/'workbench.sqlite3'),sha)

    def test_corrupt_members_and_unsafe_names_fail_without_destination(self):
        result=create_snapshot(self.data)
        with zipfile.ZipFile(result['path']) as source:
            entries={name:source.read(name) for name in source.namelist()}
        broken=self.root/'broken.zip'
        with zipfile.ZipFile(broken,'w') as target:
            for name,body in entries.items():target.writestr(name,body+b'x' if name=='settings.json' else body)
        with self.assertRaises(ValueError):restore_snapshot(broken,self.root/'bad-restore')
        self.assertFalse((self.root/'bad-restore').exists())
        unsafe=self.root/'unsafe.zip'
        with zipfile.ZipFile(unsafe,'w') as target:
            for name,body in entries.items():target.writestr(name,body)
            target.writestr('../outside','not permitted')
        with self.assertRaises(ValueError):restore_snapshot(unsafe,self.root/'bad-restore')

    def test_cold_backup_excludes_live_data_changed_journals_links_and_unnamed_clones(self):
        backup=self.data/'backups/before-change.sqlite3';backup.parent.mkdir();backup.write_bytes((self.data/'workbench.sqlite3').read_bytes())
        os.utime(backup,(time.time()-7200,)*2)
        self.assertEqual(cold_backup(self.root,backup)[0],backup)
        with self.assertRaises(ValueError):cold_backup(self.root,self.data/'workbench.sqlite3')
        clone=self.root/'.evidence/isolated/workbench.sqlite3';clone.parent.mkdir(parents=True);clone.write_bytes(backup.read_bytes())
        with self.assertRaises(ValueError):cold_backup(self.root,clone)
        journal=Path(str(backup)+'-wal');journal.write_bytes(b'unknown committed WAL')
        with self.assertRaises(ValueError):cold_backup(self.root,backup)
        journal.unlink();os.utime(backup,None)
        with self.assertRaises(ValueError):cold_backup(self.root,backup)

    def test_existing_writer_lock_is_not_removed_or_stolen(self):
        directory=self.data/'backups/snapshots';directory.mkdir(parents=True)
        lock=directory/'snapshot.lock';lock.write_text('unknown owner')
        with self.assertRaises(FileExistsError):create_snapshot(self.data)
        self.assertEqual(lock.read_text(),'unknown owner')

    def test_retention_keeps_baseline_recent_three_and_latest_per_recent_day(self):
        from datetime import datetime,timezone
        day=datetime(2026,1,1,tzinfo=timezone.utc).timestamp()
        rows=[{'path':f'{i}-{j}','mtime_ns':int((day+i*86400+j*3600)*1e9)} for i in range(12) for j in range(2)]
        keep=retained_backups(rows,now=day+12*86400)
        self.assertIn('0-0',keep)
        self.assertTrue({'10-1','11-0','11-1'}<=keep)
        self.assertTrue({f'{i}-1' for i in range(5,12)}<=keep)
        self.assertNotIn('4-1',keep);self.assertNotIn('8-0',keep)
        self.assertEqual(retained_backups([]),set())


if __name__=='__main__':unittest.main()
