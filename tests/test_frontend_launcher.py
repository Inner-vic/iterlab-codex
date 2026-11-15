"""Static launcher contract checks only; this does not execute Windows PowerShell."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class LauncherContractTests(unittest.TestCase):
    def test_four_paths_fail_closed_and_preserve_original_instance(self):
        source=(ROOT/'scripts/open-frontend.ps1').read_text(encoding='utf-8-sig')
        for marker in ['if ($existing.Count)','Test-OwnedService','service.json','OwningProcess','Get-CimInstance Win32_Process','CreationDate','LastWriteTimeUtc','start.ps1','@startOptions','Get-Process -Id','AddSeconds($TimeoutSeconds)','-MaximumRedirection 0','Start-Process -FilePath $baseUrl','exit 2','exit 1','exit 0']:
            self.assertIn(marker,source)
        self.assertEqual(source.count("'start.ps1'"),1)
        for forbidden in ['Stop-Process','taskkill','ExecutionPolicy','Remove-Item','api/service/stop']:
            self.assertNotIn(forbidden,source)
    def test_double_click_quotes_paths_and_preserves_failure_code(self):
        source=(ROOT/'app.cmd').read_text(encoding='utf-8')
        self.assertIn('where pwsh.exe',source)
        self.assertIn('powershell.exe',source)
        self.assertIn('"%~dp0scripts\\open-frontend.ps1"',source)
        self.assertIn('exit /b %result%',source)
        self.assertNotIn('ExecutionPolicy',source)

if __name__=='__main__':unittest.main()
