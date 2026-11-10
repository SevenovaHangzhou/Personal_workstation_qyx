"""Real YomkServerPy, temporary data, no external/model/scheduler writes."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = '''
import json, socket, tempfile, threading, time
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from runtime import MomoRuntime
from framework import api
from observation import GROUPS, caller
from model import make_task
from typedefine.TypeDefine import FOCUS_LOOP

def edges(snapshot):
    names={s['id']:s['name'] for s in snapshot['services']}
    return {(names[e['source']],names[e['target']],e['endpoint'],e['kind']):e for e in snapshot['edges'] if e['evidence']=='observed'}

def clean(runtime):
    runtime.close(apply_update=False)
    runtime.close(apply_update=False)
    assert not getattr(api.server(),'_yomk_graph_observer',None)
    assert '/TaskService' not in api.server_info_services().data
    assert not any(t.name.startswith(('yomk-graph-http','momo-focus-clock','momo-daily','orbit-course-','orbit-tutor-','orbit-ota')) for t in threading.enumerate()), threading.enumerate()
'''


class ObservationTests(unittest.TestCase):
    def scenario(self, code):
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        result = subprocess.run([sys.executable,'-c',PRELUDE+'\n'+textwrap.dedent(code)],cwd=ROOT,
                                env=environment,capture_output=True,text=True,timeout=35)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_actual_inventory_groups_privacy_and_queries_do_not_pollute(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            task=r.tasks.add(make_task('PRIVATE_TASK_BODY_773','learn'))
            api.context_create('private','PRIVATE_CONTEXT_VALUE_884','private-group')
            snapshot=r.graph.snapshot()
            names={s['name']:s for s in snapshot['services'] if s['registered']}
            expected={name for services in GROUPS.values() for name in services}
            builtins={'/YomkContext','/YomkServerInfo','/YomkEventLoop','/YomkFunctionPool','/YomkLogger'}
            assert set(names)==expected|builtins
            for group,services in GROUPS.items():
                assert all(names[s]['module']==group and names[s]['functions'] for s in services)
            before=edges(snapshot)
            for _ in range(3):
                with urlopen(r.graph.url+'/api/snapshot',timeout=3) as response:
                    current=json.load(response)
                assert edges(current)==before
            text=json.dumps(current)
            assert 'PRIVATE_TASK_BODY_773' not in text and 'PRIVATE_CONTEXT_VALUE_884' not in text
            assert 'private-group' in text and 'checker_on' in text
            assert current.get('demo') is not True
            api.context_destroy('private','private-group')
            clean(r)
        api.shutdown()
        ''')

    def test_ui_nested_request_background_and_real_focus_callback_sources(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            task=r.tasks.tasks[0]
            r.focus.start(task['id'])
            response=api.event_loop_post_wait(FOCUS_LOOP,None)
            assert response.status==api.ResStatus.eOk
            worker=threading.Thread(target=r.tasks.revision)
            worker.start();worker.join()
            observed=edges(r.graph.snapshot())
            assert ('@QtUI','/FocusService','/start','request') in observed
            assert ('/FocusService','/TaskService','/get','request') in observed
            assert ('@FocusEventLoop','/FocusService','/tick','request') in observed
            assert ('@UIWorker','/TaskService','/revision','request') in observed
            focus=r.boot.focus_service
            focus.thread=threading.Thread(target=focus.pulse,name='momo-focus-clock')
            focus.thread.start()
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                observed=edges(r.graph.snapshot())
                if ('@FocusClock','/YomkEventLoop','/post','request') in observed:break
                time.sleep(.01)
            assert ('@FocusClock','/YomkEventLoop','/post','request') in observed
            clean(r)
        api.shutdown()
        ''')

    def test_direct_daily_writer_and_actual_daily_job_without_network(self):
        self.scenario('''
        from types import SimpleNamespace
        from observation import daily_writer
        from daily_history import latest_due,ZONE
        from datetime import datetime
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            fake=SimpleNamespace(choices=lambda team: {'viewer':{'id':'fake'},'teams':[],'projects':[]})
            with patch.object(r.boot.linear_service,'writer',return_value=fake):
                assert r.daily.choices('fixture-team')['viewer']['id']=='fake'
            # No work tasks / no real history / no repositories means no remote side effect.
            r.tasks.clear_samples()
            engine=r.daily_service.engine
            with patch.object(engine.history,'scan',return_value=[]),patch.object(engine.history,'between',return_value={}):
                engine.run(latest_due(datetime.now(ZONE)),r.daily.snapshot()['config'],threading.Event())
            observed=edges(r.graph.snapshot())
            assert ('/DailyService','/LinearService','/writer/choices','direct-call') in observed
            assert ('@DailyWorker','/DailyService','/run-day','job') in observed
            assert ('/DailyService','/TaskService','/list','request') in observed
            clean(r)
        api.shutdown()
        ''')

    def test_learning_job_inflight_ends_on_cancellation_without_model(self):
        self.scenario('''
        from services.LearningService import LearningService
        from learning_content import SUBJECT_MODE
        class Runner:
            def run(self,job,folder,cancel,progress):
                cancel.wait(5)
                raise InterruptedError('fixture cancellation')
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            task=r.tasks.add(make_task('not exported','learn'))
            r.learning_service.runner=Runner()
            r.learning.start(task['id'],'not exported',1)
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                observed=edges(r.graph.snapshot())
                value=observed.get(('@CourseWorker','/LearningService','/generate-course','job'))
                if value and value['in_flight']==1:break
                time.sleep(.01)
            assert value['in_flight']==1
            # Submission has already returned while the actual job is still in flight.
            assert observed[('@QtUI','/LearningService','/start','request')]['in_flight']==0
            clean(r)
            assert not any(t.name.startswith('orbit-course-') for t in threading.enumerate())
        api.shutdown()
        ''')

    def test_port_conflict_rolls_back_and_default_disabled_still_works(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary, socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=occupied.getsockname()[1])
            try:r.start()
            except RuntimeError as error:assert '--graph-port' in str(error)
            else:raise AssertionError('expected bind error')
            assert not r.started
            clean(r)
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False).start()
            assert r.graph is None and r.tasks.tasks
            clean(r)
        api.shutdown()
        ''')

    def test_missing_dependency_and_boot_failure_leave_no_hook(self):
        self.scenario('''
        import builtins
        original=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='yomk_graph':raise ImportError('fixture dependency absent')
            return original(name,*args,**kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0)
            with patch('builtins.__import__',side_effect=missing):
                try:r.start()
                except RuntimeError as error:assert '组件缺失' in str(error)
                else:raise AssertionError('dependency failure not reported')
            clean(r)
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0)
            with patch.object(r.boot,'after',side_effect=RuntimeError('fixture boot failure')):
                try:r.start()
                except RuntimeError:pass
                else:raise AssertionError('boot failure not reported')
            clean(r)
        api.shutdown()
        ''')

    def test_http_start_failure_and_cleanup_exception_still_drain(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            from yomk_graph import GraphServer
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0)
            with patch.object(GraphServer,'start',side_effect=RuntimeError('fixture server failure')):
                try:r.start()
                except RuntimeError:pass
                else:raise AssertionError('server failure not reported')
            clean(r)
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            original=r.update_service.stop
            def broken():original();raise RuntimeError('fixture drain error')
            with patch.object(r.update_service,'stop',side_effect=broken):
                try:r.close(apply_update=False)
                except RuntimeError:pass
                else:raise AssertionError('cleanup error not reported')
            assert not r.started
            clean(r)
        api.shutdown()
        ''')

    def test_final_close_drains_framework_request_and_callback_before_detach(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            called=threading.Event()
            def callback(response):
                time.sleep(.08)
                with caller('/LearningService'):r.tasks.revision()
                called.set()
            api.async_request('/TaskService/list',None,callback)
            r.close(apply_update=False,final_shutdown=True)
            assert called.is_set()
            assert not getattr(api.server(),'_yomk_graph_observer',None)
            assert not any(t.name=='yomk-graph-http' for t in threading.enumerate())
        api.shutdown()
        ''')

    def test_qt_results_drain_while_services_and_observer_still_exist(self):
        self.scenario('''
        from main import Momo
        from PyQt5.QtWidgets import QApplication
        from codex_ui import HistoryWorker
        app=QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            window=Momo(r)
            callback=[]
            worker=HistoryWorker(lambda cancel: cancel.wait(1),window)
            def finished():
                assert getattr(api.server(),'_yomk_graph_observer',None)
                callback.append(True)
            worker.finished.connect(finished)
            worker.start()
            r.graph.stop_http()
            window.drain_ui_workers()
            assert callback==[True]
            window.exiting=True;window.close()
            clean(r)
        api.shutdown()
        ''')

    def test_deinit_exception_does_not_strand_removed_registration(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            original=r.learning_service.deinit
            def broken():
                original()
                raise RuntimeError('fixture deinit error after drain')
            with patch.object(r.learning_service,'deinit',side_effect=broken):
                try:r.close(apply_update=False)
                except RuntimeError:pass
                else:raise AssertionError('deinit error not reported')
            assert not r.started
            clean(r)
        api.shutdown()
        ''')

    def test_http_requests_finish_before_observer_detaches(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            entered=threading.Event();release=threading.Event();finished=[];failures=[]
            original=r.graph.observer.snapshot
            def slow():
                entered.set()
                assert release.wait(2)
                assert getattr(api.server(),'_yomk_graph_observer',None)
                return original()
            def fetch():
                try:
                    with urlopen(r.graph.url+'/api/snapshot',timeout=4) as response:json.load(response)
                    finished.append(True)
                except Exception as error:failures.append(str(error))
            with patch.object(r.graph.observer,'snapshot',side_effect=slow):
                request=threading.Thread(target=fetch);request.start()
                assert entered.wait(2)
                timer=threading.Timer(.1,release.set);timer.start()
                r.close(apply_update=False)
                timer.join();request.join()
            assert finished==[True] and not failures,failures
            clean(r)
        api.shutdown()
        ''')

    def test_framework_error_metrics_keep_payloads_out_of_snapshot(self):
        self.scenario('''
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False,graph_port=0).start()
            with caller('@Control'):
                result=api.request('/TaskService/get',{'secret':'PRIVATE_REQUEST_992'})
            assert result.status==api.ResStatus.eInvalid
            snapshot=r.graph.snapshot()
            edge=edges(snapshot)[('@Control','/TaskService','/get','request')]
            assert edge['calls']==1 and edge['errors']==1 and edge['in_flight']==0
            assert 'PRIVATE_REQUEST_992' not in json.dumps(snapshot)
            task=next(s for s in snapshot['services'] if s['name']=='/TaskService')
            assert next(f for f in task['functions'] if f['name']=='/get')['message']=='TaskId'
            clean(r)
        api.shutdown()
        ''')

    def test_default_disabled_never_imports_graph_package(self):
        self.scenario('''
        import sys
        with tempfile.TemporaryDirectory() as temporary:
            r=MomoRuntime(Path(temporary)/'tasks.json',auto_tick=False).start()
            assert 'yomk_graph' not in sys.modules
            r.focus.snapshot();r.tasks.revision()
            clean(r)
        api.shutdown()
        ''')
