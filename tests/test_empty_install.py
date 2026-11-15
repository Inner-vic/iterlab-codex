"""First-run database/defaults contract, with no model or enterprise access."""
import json,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from lab.store import Store
from lab.kernel import Kernel

class EmptyInstallTests(unittest.TestCase):
    def test_new_install_bootstraps_empty_database_and_reopens_without_credentials(self):
        with tempfile.TemporaryDirectory(prefix='iterlab-empty-install-') as temp:
            root=Path(temp)/'user-data'
            for _ in range(2):
                store=Store(root)
                with patch('subprocess.Popen',side_effect=AssertionError('First boot must not start external tools')),patch('subprocess.run',side_effect=AssertionError('First boot must not log in or call a model')):
                    kernel=Kernel(store)
                    try:
                        for table in ('projects','sessions','messages','records','company_training','research_grants'):
                            self.assertEqual(store.one('SELECT COUNT(*) AS n FROM '+table)['n'],0,table)
                        self.assertEqual(store.one('PRAGMA journal_mode')['journal_mode'],'wal')
                        self.assertEqual(store.one('PRAGMA foreign_keys')['foreign_keys'],1)
                        self.assertFalse(kernel.company.config()['lpai_enabled'])
                        self.assertFalse(kernel.company.config()['gitlab_enabled'])
                        self.assertIsNone(kernel.settings.value['model'])
                        self.assertIsNone(kernel.rpc)
                        self.assertTrue(store.one('SELECT COUNT(*) AS n FROM schema_versions')['n']>0)
                    finally:kernel.close();store.close()
            self.assertTrue((root/'workbench.sqlite3').is_file())

if __name__=='__main__':unittest.main()
