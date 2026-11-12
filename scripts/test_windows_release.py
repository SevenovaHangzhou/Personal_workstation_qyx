"""Native regression supervisor; publish only code locations and test counts."""
import json
import ast
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

NAMES=['test_app_split','test_windows','test_model','test_services','test_issue_scope','test_structure_undo',
       'test_planning','test_task_move','test_task_detach','test_task_dependencies_ui','test_study_resume','test_display_settings','test_release_entry','test_evaluation_mesh','test_evaluation_journals','test_evaluation_orbit_bridge','test_evaluation_ui']
if os.environ.get('ORBIT_NATIVE_TEST_CHILD')!='1':
    failed=False
    cases=[]
    for module in NAMES:
        if module=='test_task_detach':
            tree=ast.parse((ROOT/'tests'/f'{module}.py').read_text())
            cases.extend(f'{module}.{cls.name}.{method.name}' for cls in tree.body if isinstance(cls,ast.ClassDef)
                         for method in cls.body if isinstance(method,ast.FunctionDef) and method.name.startswith('test_'))
        else:cases.append(module)
    for module in cases:
        with tempfile.TemporaryDirectory(prefix='orbit-test-status-') as temp:
            path=Path(temp)/'status.json'
            env=dict(os.environ,ORBIT_NATIVE_TEST_CHILD='1',ORBIT_NATIVE_TEST_STATUS=str(path),ORBIT_NATIVE_TEST_MODULE=module)
            # Each module has its own QApplication/native Qt lifetime.
            # Raw output is discarded and is never uploaded or printed.
            child=subprocess.run([sys.executable,str(Path(__file__).resolve())],cwd=ROOT,env=env,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            state=json.loads(path.read_text()) if path.exists() else {}
            for item in state.get('reports',[]):
                print('::error title=Windows regression location::'+item,flush=True)
            description=f"module={module} exit={child.returncode} | test={state.get('test','not-started')} | phase={state.get('phase','unknown')}"
            kind='error' if child.returncode else 'notice'
            print(f'::{kind} title=Windows native result::'+description+f" | count={state.get('count',0)} failures={state.get('failures',0)} errors={state.get('errors',0)}",flush=True)
            failed=failed or child.returncode!=0
    raise SystemExit(1 if failed else 0)

reports=[]
def status(**data):
    data['reports']=reports
    Path(os.environ['ORBIT_NATIVE_TEST_STATUS']).write_text(json.dumps(data),encoding='utf-8')

uncaught=[]
last_test='suite-startup'
def uncaught_exception(kind,value,tb):
    frames=traceback.extract_tb(tb) if tb else traceback.extract_stack()[:-1]
    locations=' > '.join(f'{Path(f.filename).name}:{f.lineno} ({f.name})' for f in frames[-6:])
    category=''
    deleted=re.fullmatch(r'wrapped C/C\+\+ object of type ([A-Za-z][A-Za-z0-9_]*) has been deleted',str(value))
    if deleted:category='deleted Qt wrapper '+deleted.group(1)
    report=f'uncaught Qt/Python callback | {kind.__name__} | {category} | {locations}'
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

module=os.environ['ORBIT_NATIVE_TEST_MODULE'];assert module.split('.')[0] in NAMES
names=[module]
result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromNames(names))
status(test='suite',phase='completed',count=result.testsRun,failures=len(result.failures),errors=len(result.errors))
raise SystemExit(0 if result.wasSuccessful() and not uncaught else 1)
