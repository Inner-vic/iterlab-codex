"""Concrete preparation entry points do not depend on the research cwd."""
import unittest
from pathlib import Path
from lab.host_preparation import instructions

class HostPreparationTests(unittest.TestCase):
    def test_explicit_wrapper_and_interpreter_use_host_root_not_project_cwd(self):
        value=instructions(Path('D:/Install/IterLab'),Path('C:/Python/python.exe'))
        self.assertIn("& 'D:",value)
        self.assertIn('scripts',value);self.assertIn('run-quiet.ps1',value)
        self.assertIn("-Program 'C:",value)
        self.assertIn("-ProgramArgs @('-m','py_compile'",value)
        self.assertIn('当前cwd是项目工作区，不是IterLab源码根目录',value)
        self.assertIn('不能用此入口在本地代跑远端训练',value)
    def test_paths_with_spaces_and_quotes_are_powershell_literals(self):
        value=instructions(Path("D:/Alice's Lab/IterLab"),Path('C:/Python Tools/python.exe'))
        self.assertIn("Alice''s Lab",value)
        self.assertIn("-Program "+"'"+str(Path('C:/Python Tools/python.exe'))+"'",value)

if __name__=='__main__':unittest.main()
