"""Dependency setup isolation; fake installers never touch user software or accounts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from scripts.bootstrap import Bootstrap, SDK_VERSIONS, LPAI_HUB

ROOT = Path(__file__).resolve().parents[1]


class DependencySetupTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / '.evidence/tests'
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.calls = []
        self.found = {'ept': 'C:/tools/ept.exe', 'lpai-agent': 'C:/tools/lpai-agent.exe'}
        self.env = patch.dict(os.environ, {'ITERLAB_CODEX_DATA': str(self.root / '.data')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.assertEqual(self.root.resolve().parent, self.parent.resolve())
        shutil.rmtree(self.root)

    def runner(self, argv, **kwargs):
        self.calls.append([str(a) for a in argv])
        return 0, 'lpai-agent devspaces tasks' if '--help' in argv else 'version 1.0'

    def subject(self, **options):
        obj = Bootstrap(self.root, runner=self.runner, which=self.found.get, **options)
        obj.sdk_state = lambda python: dict(SDK_VERSIONS)
        obj.sdk_sources = lambda python: True
        return obj

    def test_existing_installations_reused_without_install_login_or_cloud_calls(self):
        obj = self.subject()
        result = obj.execute()
        self.assertTrue(result['complete'])
        self.assertEqual(result['cloud_writes'], 0)
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(self.calls, [[str(Path('C:/tools/ept.exe')), 'version'], [str(Path('C:/tools/lpai-agent.exe')), '--help']])

    def test_check_only_missing_never_installs_or_downloads(self):
        self.found.clear()
        obj = self.subject(check_only=True)
        obj.known_tool = lambda name: None
        obj.sdk_state = lambda python: None
        obj.download = lambda *args: self.fail('check-only downloaded')
        result = obj.execute()
        self.assertFalse(result['complete'])
        self.assertFalse(self.calls)
        self.assertEqual([e['state'] for e in result['entries']], ['ready', 'missing', 'missing', 'missing'])

    def test_missing_saved_ept_does_not_install_or_fallback_to_codex(self):
        (self.root / '.data').mkdir()
        (self.root / '.data/settings.json').write_text('{"channel":"ept"}', encoding='utf-8')
        obj = self.subject(skip_lpai=True)
        obj.known_tool = lambda name: None
        obj.install_codex = lambda: self.fail('channel fallback')
        result = obj.execute()
        self.assertFalse(result['complete'])
        self.assertEqual(result['entries'][-1]['name'], 'ept')
        self.assertEqual((self.root / '.data/settings.json').read_text(), '{"channel":"ept"}')

    def test_broken_existing_cli_not_overwritten_and_diagnostics_redacted(self):
        obj = self.subject(skip_lpai=True)
        obj.run = lambda *a, **k: (1, 'api_key=DO_NOT_PERSIST_THIS_VALUE')
        result = obj.execute()
        self.assertEqual(result['entries'][-1]['state'], 'failed')
        self.assertNotIn('DO_NOT_PERSIST', json.dumps(result))

    def test_partial_sdk_wrong_version_is_not_upgraded(self):
        obj = self.subject()
        obj.sdk_state = lambda python: {'lpai': '9.9', 'lpai-asset': None}
        obj.lpai_sdk()
        self.assertEqual(obj.entries[-1]['state'], 'failed')
        self.assertFalse(self.calls)

    def test_exact_version_with_source_drift_is_not_ready(self):
        obj = self.subject()
        obj.sdk_sources = lambda python: False
        obj.lpai_sdk()
        self.assertEqual(obj.entries[-1]['state'], 'failed')
        self.assertFalse(self.calls)

    def test_sdk_install_exact_pins_company_index_and_rechecks(self):
        obj = self.subject()
        states = iter([{'lpai': '1.0.1', 'lpai-asset': None}, dict(SDK_VERSIONS)])
        obj.sdk_state = lambda python: next(states)
        obj.lpai_sdk()
        self.assertEqual(obj.entries[-1]['state'], 'ready')
        self.assertEqual(len(self.calls), 1)
        self.assertIn('lpai==1.0.1', self.calls[0])
        self.assertIn('lpai-asset==3.0.23', self.calls[0])
        self.assertIn('--isolated', self.calls[0])
        self.assertNotIn('--upgrade', self.calls[0])

    def test_cli_uses_vendor_artifact_and_records_digest_without_skill_install(self):
        obj = self.subject()
        obj.known_tool = lambda name: None
        downloads = []
        def download(url, target):
            downloads.append(url)
            target.write_bytes(b'MZtest-executable')
        obj.download = download
        obj.lpai_cli()
        self.assertEqual(len(downloads), 1)
        self.assertTrue(downloads[0].startswith(LPAI_HUB + '/latest/cli/windows/'))
        self.assertEqual(obj.entries[-1]['state'], 'ready')
        self.assertEqual(len(obj.entries[0]['sha256']), 64)
        self.assertTrue((self.root / '.runtime/bin/lpai-agent.exe').exists())
        self.assertFalse((self.root / 'skills').exists())

    def test_invalid_cli_download_is_not_installed_or_executed(self):
        obj = self.subject()
        obj.known_tool = lambda name: None
        obj.download = lambda url, target: target.write_text('<html>login required</html>')
        obj.lpai_cli()
        self.assertFalse((self.root / '.runtime/bin/lpai-agent.exe').exists())
        self.assertFalse(list((self.root / '.runtime/bin').iterdir()))
        self.assertFalse(self.calls)

    def test_codex_install_uses_official_package_local_prefix_no_shell(self):
        obj = self.subject()
        node = self.root / 'node/node.exe'
        npm = node.parent / 'node_modules/npm/bin/npm-cli.js'
        npm.parent.mkdir(parents=True)
        npm.write_text('fixture')
        self.found['node'] = str(node)
        obj.install_codex()
        self.assertIn('@openai/codex@latest', self.calls[0])
        self.assertIn('--registry=https://registry.npmjs.org', self.calls[0])
        self.assertIn(str(self.root / '.runtime/codex'), self.calls[0])
        self.assertNotIn('-g', self.calls[0])


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell argument transport')
class QuietWindowsCompatibilityTests(unittest.TestCase):
    def test_windows_powershell_quotes_arguments_and_preserves_exit_code(self):
        powershell = shutil.which('powershell.exe')
        if not powershell:
            self.skipTest('Windows PowerShell missing')
        parent = ROOT / '.evidence/tests'
        temp = parent / uuid.uuid4().hex
        temp.mkdir(parents=True)
        try:
            probe = temp / 'argv probe.py'
            probe.write_text('import json,sys; print(json.dumps(sys.argv[1:])); raise SystemExit(7)', encoding='utf-8')
            args = ['with spaces', 'literal "quote"', 'trailing\\', '$HOME;`whoami`', '中文']
            def quote(value):
                return "'" + str(value).replace("'", "''") + "'"
            command = ('& ' + quote(ROOT / 'scripts/run-quiet.ps1') + ' -Program ' + quote(sys.executable)
                       + ' -ProgramArgs @(' + ','.join(quote(v) for v in [str(probe), *args]) + '); exit $LASTEXITCODE')
            # EncodedCommand transports Unicode without the Windows legacy codepage.
            import base64
            result = subprocess.run([powershell, '-NoProfile', '-EncodedCommand', base64.b64encode(command.encode('utf-16le')).decode()],
                                    capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 7, result.stderr.decode(errors='replace'))
            self.assertEqual(json.loads(result.stdout.decode('utf-8-sig')), args)
        finally:
            self.assertEqual(temp.resolve().parent, parent.resolve())
            shutil.rmtree(temp)


if __name__ == '__main__':
    unittest.main()
