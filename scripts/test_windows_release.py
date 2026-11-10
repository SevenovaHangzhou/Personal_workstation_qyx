"""Native release regression runner with content-free public diagnostics."""
import os
from pathlib import Path
import sys
import traceback
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

class Result(unittest.TextTestResult):
    def startTest(self,test):
        super().startTest(test)
        print('Testing '+test.id(),flush=True)
    def report(self,test,error):
        # Publish only code locations and exception class, never exception
        # messages, locals, source snippets, stdout, paths or user values.
        frames=traceback.extract_tb(error[2])
        locations=' > '.join(f'{Path(f.filename).name}:{f.lineno} ({f.name})' for f in frames[-6:])
        text=f'{test.id()} | {error[0].__name__} | {locations}'
        print('::error title=Windows regression location::'+text.replace('%','%25').replace('\n','%0A').replace('\r','%0D'),flush=True)
    def addFailure(self,test,error):super().addFailure(test,error);self.report(test,error)
    def addError(self,test,error):super().addError(test,error);self.report(test,error)

names=['test_windows','test_model','test_services','test_issue_scope','test_structure_undo',
       'test_planning','test_task_move','test_task_detach','test_study_resume','test_release_entry']
result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromNames(names))
print(f'::warning title=Windows test result::count={result.testsRun} failures={len(result.failures)} errors={len(result.errors)}',flush=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
