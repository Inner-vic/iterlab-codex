"""Portable-source boundary checks; no user store, model or platform access."""
import json
from pathlib import Path
import shutil
import unittest
import uuid

from scripts import source_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]


class SourceSharingTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / '.evidence/tests'
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.assertEqual(self.root.resolve().parent, self.parent.resolve())
        shutil.rmtree(self.root)

    @unittest.skipUnless(shutil.which('git'), 'Git is needed to evaluate ignore rules')
    def test_personal_files_excluded_and_runtime_assets_included(self):
        shutil.copyfile(ROOT / '.gitignore', self.root / '.gitignore')
        keep = ['app.cmd', 'lab/web.py', 'static/vendor/exploration-map.js',
                'frontend/exploration-map/package-lock.json', 'scripts/lpai_task_files.py',
                'scripts/probe_cloud_outputs.py', 'docs/sharing-and-deployment.md',
                'scripts/check_docs.py', 'docs/README.md', 'docs/repository-audit.md']
        private = ['.data/research.sqlite3', '.data/projects/chat.md', '.runtime/lpai/python.exe',
                   '.evidence/run.json', '.local/original.txt', 'token.txt',
                   'docs/acceptance/test/identity.json', 'docs/progress.md',
                   'scripts/personal_submission.py', 'tests/test_nofit_loop_acceptance.py',
                   'frontend/exploration-map/node_modules/pkg/index.js', 'lab/auth.json']
        for name in keep + private:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture', encoding='utf-8')
        names = bundle.source_files(self.root)
        self.assertTrue(set(keep).issubset(names))
        self.assertFalse(set(private).intersection(names))

    def test_possible_credential_blocks_without_echoing_value(self):
        value = 'a' * 40
        (self.root / 'config.json').write_text(json.dumps({'api_key': value}), encoding='utf-8')
        _, problems = bundle.inspect(self.root, ['config.json'])
        self.assertTrue(any(p['reason'].startswith('possible embedded credential') for p in problems))
        self.assertNotIn(value, json.dumps(problems))

    def test_missing_runtime_file_blocks_export(self):
        _, problems = bundle.inspect(self.root, [])
        self.assertIn('lab/web.py', [p['file'] for p in problems])
        self.assertIn('scripts/lpai_task_files.py', [p['file'] for p in problems])

    def test_empty_required_tool_blocks_export(self):
        path = self.root / 'scripts/source_bundle.py'
        path.parent.mkdir()
        path.write_text('', encoding='utf-8')
        _, problems = bundle.inspect(self.root, ['scripts/source_bundle.py'])
        self.assertIn({'file': 'scripts/source_bundle.py',
                       'reason': 'required distributable file is empty'}, problems)


if __name__ == '__main__':
    unittest.main()
