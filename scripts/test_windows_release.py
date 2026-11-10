"""Native regression supervisor; publish only code locations and test counts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

if os.environ.get('ORBIT_NATIVE_TEST_CHILD')!='1':
    with tempfile.TemporaryDirectory(prefix='orbit-test-status-') as temp:
        path=Path(temp)/'status.json'
        env=dict(os.environ,ORBIT_NATIVE_TEST_CHILD='1',ORBIT_NATIVE_TEST_STATUS=str(path))
        # Raw output is never uploaded or printed to the CI log.
        child=subprocess.run([sys.executable,str(Path(__file__).resolve())],cwd=ROOT,env=env,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        state=json.loads(path.read_text()) if path.exists() else {}
        for item in state.get('reports',[]):
            print('::error title=Windows regression location::'+item,flush=True)
        description=f"exit={child.returncode} | test={state.get('test','not-started')} | phase={state.get('phase','unknown')}"
        kind='error' if child.returncode else 'notice'
        print(f'::{kind} title=Windows native result::'+description+f" | count={state.get('count',0)} failures={state.get('failures',0)} errors={state.get('errors',0)}",flush=True)
        raise SystemExit(0 if child.returncode==0 else 1)

reports=[]
def status(**data):
    data['reports']=reports
    Path(os.environ['ORBIT_NATIVE_TEST_STATUS']).write_text(json.dumps(data),encoding='utf-8')

uncaught=[]
last_test='suite-startup'
def uncaught_exception(kind,value,tb):
    frames=traceback.extract_tb(tb)
    locations=' > '.join(f'{Path(f.filename).name}:{f.lineno} ({f.name})' for f in frames[-6:])
    report=f'uncaught Qt/Python callback | {kind.__name__} | {locations}'
    uncaught.append(report);reports.append(report)
    status(test=last_test,phase='uncaught-callback')
    sys.__excepthook__(kind,value,tb)
sys.excepthook=uncaught_exception

class Result(unittest.TextTestResult):
    def startTest(self,test):
        global last_test
        last_test=test.id()
        super().startTest(test);status(test=test.id(),phase='running')
    def stopTest(self,test):
        super().stopTest(test);status(test=test.id(),phase='completed')
    def report(self,test,error):
        # No messages, locals, source snippets, full paths, stdout or user values.
        frames=traceback.extract_tb(error[2])
        locations=' > '.join(f'{Path(f.filename).name}:{f.lineno} ({f.name})' for f in frames[-6:])
        reports.append(f'{test.id()} | {error[0].__name__} | {locations}')
        status(test=test.id(),phase='failed')
    def addFailure(self,test,error):super().addFailure(test,error);self.report(test,error)
    def addError(self,test,error):super().addError(test,error);self.report(test,error)

names=['test_windows','test_model','test_services','test_issue_scope','test_structure_undo',
       'test_planning','test_task_move','test_task_detach','test_study_resume','test_release_entry']
result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromNames(names))
status(test='suite',phase='completed',count=result.testsRun,failures=len(result.failures),errors=len(result.errors))
raise SystemExit(0 if result.wasSuccessful() and not uncaught else 1)
