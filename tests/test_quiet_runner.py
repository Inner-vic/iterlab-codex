"""Real Windows argument binding and input preservation, no model or fit."""
import base64,os,shutil,subprocess,sys,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.name=='nt','Windows process transport')
class QuietRunnerTests(unittest.TestCase):
    def setUp(self):
        self.parent=ROOT/'.evidence/tests';self.temp=self.parent/uuid.uuid4().hex
        self.temp.mkdir(parents=True)
        self.probe=self.temp/'input script.py';self.marker=self.temp/'executed.txt'
        self.body='from pathlib import Path\nPath('+repr(str(self.marker))+').write_text("executed")\nprint("real-output")\nraise SystemExit(7)\n'
        self.probe.write_text(self.body,encoding='utf-8')
    def tearDown(self):
        self.assertEqual(self.temp.resolve().parent,self.parent.resolve());shutil.rmtree(self.temp)
    def q(self,value):return "'"+str(value).replace("'","''")+"'"
    def run_shells(self,suffix):
        paths=[shutil.which('pwsh.exe'),shutil.which('powershell.exe')]
        for shell in dict.fromkeys(p for p in paths if p):
            with self.subTest(shell=shell):
                self.marker.unlink(missing_ok=True)
                command='& '+self.q(ROOT/'scripts/run-quiet.ps1')+' '+suffix+'; exit $LASTEXITCODE'
                r=subprocess.run([shell,'-NoProfile','-EncodedCommand',base64.b64encode(command.encode('utf-16le')).decode()],
                    capture_output=True,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
                yield r
    def test_positional_script_is_argument_and_never_stdout_destination(self):
        for r in self.run_shells(self.q(sys.executable)+' '+self.q(self.probe)):
            self.assertEqual(r.returncode,7,r.stderr.decode(errors='replace'))
            self.assertIn(b'real-output',r.stdout);self.assertTrue(self.marker.exists())
            self.assertEqual(self.probe.read_text(encoding='utf-8'),self.body)
    def test_named_output_preserves_real_exit_code_and_source(self):
        output=self.temp/'explicit-output.txt'
        for r in self.run_shells('-Program '+self.q(sys.executable)+' -StdoutPath '+self.q(output)+' -ProgramArgs @('+self.q(self.probe)+')'):
            self.assertEqual(r.returncode,7,r.stderr.decode(errors='replace'))
            self.assertIn('real-output',output.read_text(encoding='utf-8'));self.assertTrue(self.marker.exists())
            self.assertEqual(self.probe.read_text(encoding='utf-8'),self.body)
    def test_explicit_input_output_collision_refused_before_launch(self):
        for switch in ('-StdoutPath','-StdoutStreamPath'):
            extra=(' -StderrStreamPath '+self.q(self.temp/'stderr.txt')) if switch=='-StdoutStreamPath' else ''
            for r in self.run_shells('-Program '+self.q(sys.executable)+' '+switch+' '+self.q(self.probe)+extra+' -ProgramArgs @('+self.q(self.probe)+')'):
                self.assertNotEqual(r.returncode,0);self.assertFalse(self.marker.exists())
                self.assertEqual(self.probe.read_text(encoding='utf-8'),self.body)
