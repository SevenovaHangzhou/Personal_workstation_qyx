"""Native Windows release checks; also test portable archive rules on Linux."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
from platform_support import lock_file,PipeReader,process_options,stop_process
from ota_core import extract_windows_package,package_names

ROOT=Path(__file__).resolve().parents[1]

class PortablePlatformTests(unittest.TestCase):
    def test_native_lock_excludes_another_process_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'space.lock'
            code="from platform_support import lock_file; import sys; f=open(sys.argv[1],'a+'); lock_file(f)"
            with path.open('a+') as stream:
                lock_file(stream)
                result=subprocess.run([sys.executable,'-c',code,str(path)],cwd=ROOT,capture_output=True)
                self.assertNotEqual(result.returncode,0)
            result=subprocess.run([sys.executable,'-c',code,str(path)],cwd=ROOT,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_pipe_reader_timeout_eof_and_group_stop(self):
        process=subprocess.Popen([sys.executable,'-u','-c',"import time; print('ready',flush=True); time.sleep(60)"],stdout=subprocess.PIPE,**process_options())
        reader=PipeReader(process.stdout)
        try:
            output=b'';deadline=time.monotonic()+3
            while b'\n' not in output:
                chunk=reader.read(max(.01,deadline-time.monotonic()))
                self.assertTrue(chunk,'fixture output must arrive before deadline');output+=chunk
            self.assertEqual(output.rstrip(b'\r\n'),b'ready')
            self.assertIsNone(reader.read(.01))
            stop_process(process);self.assertIsNotNone(process.poll());self.assertEqual(reader.read(3),b'')
        finally:
            reader.close();stop_process(process);process.stdout.close()

    def test_windows_zip_and_platform_assets_reject_unsafe_paths(self):
        with patch('ota_core.platform.system',return_value='Windows'):
            self.assertEqual(package_names('0.9.7')[2],'orbit-0.9.7-windows-x86_64.zip')
        for evil in ('orbit/../escape','orbit/CON.txt','orbit/file:stream','orbit/foo.','orbit/a\\b','C:/outside','orbit-bin.exe'):
            with self.subTest(path=evil),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'bad.zip'
                with zipfile.ZipFile(path,'w') as z:z.writestr(evil,b'test')
                with self.assertRaises(ValueError):extract_windows_package(path,Path(temp)/'dest',threading.Event())
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ok.zip'
            with zipfile.ZipFile(path,'w') as z:
                for name in ('orbit.exe','orbit-bin.exe','_internal/data.json'):z.writestr('orbit/'+name,b'test')
            root=extract_windows_package(path,Path(temp)/'dest',threading.Event())
            self.assertEqual((root/'orbit.exe').read_bytes(),b'test')

@unittest.skipUnless(os.name=='nt','Requires native Windows APIs')
class WindowsNativeTests(unittest.TestCase):
    def test_dpapi_credentials_are_encrypted_and_roundtrip(self):
        from credentials import CredentialStore
        with tempfile.TemporaryDirectory() as temp:
            store=CredentialStore(temp);self.assertEqual(store.save('fixture-private-value'),'Windows DPAPI')
            payload=Path(str(store.path)+'.dpapi').read_bytes();self.assertNotIn(b'fixture-private-value',payload)
            self.assertEqual(store.load(),('fixture-private-value','Windows DPAPI'))
            store.clear();self.assertEqual(store.load(),('',''))

    def test_runtime_reader_and_second_instance_wake(self):
        from test_ui import APP
        from runtime import MomoRuntime
        from main import Momo
        from window_activation import WindowActivation
        from model import make_task
        from learning_content import BUILTIN_COURSE_ID
        from learning_ui import CourseReader
        with tempfile.TemporaryDirectory() as temp:
            runtime=MomoRuntime(Path(temp)/'tasks.json',auto_tick=False).start();window=Momo(runtime)
            activation=WindowActivation(temp);activation.listen();seen=[];activation.requested.connect(seen.append)
            try:
                runtime.tasks.clear_samples();task=runtime.tasks.add(make_task('Windows学习','learn'))
                runtime.learning.bind(task['id'],BUILTIN_COURSE_ID)
                reader=CourseReader(window,runtime.learning,task['id'],BUILTIN_COURSE_ID);reader.show_lesson(1)
                self.assertEqual(len(reader.lesson_rows),14);reader.close();reader.deleteLater()
                program="from qt_runtime import configure_qt_plugins; configure_qt_plugins(); from PyQt5.QtWidgets import QApplication; from window_activation import WindowActivation; import sys; a=QApplication([]); sys.exit(0 if WindowActivation(sys.argv[1]).request() else 1)"
                child=subprocess.Popen([sys.executable,'-c',program,temp],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                deadline=time.monotonic()+8
                while child.poll() is None and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
                out,err=child.communicate(timeout=2);self.assertEqual(child.returncode,0,err);self.assertEqual(len(seen),1)
            finally:activation.close();window.exiting=True;window.close();window.deleteLater();APP.processEvents();runtime.close()

    def test_windows_recycle_plan_and_metadata_skip_junction(self):
        from storage_usage import scan_usage
        from storage_retention import Retention
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'data';source=root/'daily/git/old/a.patch';source.parent.mkdir(parents=True);source.write_text('history')
            old=time.time()-40*86400
            for path in (source,source.parent,source.parent.parent,source.parent.parent.parent):os.utime(path,(old,old))
            external=Path(temp)/'external';external.mkdir();(external/'secret').write_text('outside')
            subprocess.run(['cmd','/c','mklink','/J',str(root/'linked'),str(external)],check=True,capture_output=True)
            try:
                usage=scan_usage(root);self.assertGreaterEqual(usage['skipped_links'],1)
                manager=Retention(root);plan=manager.preview(['daily/git/old','linked']);self.assertEqual(len(plan['rows']),1)
                trash=Path(temp)/'trash';trash.mkdir()
                with patch('send2trash.send2trash',side_effect=lambda name:Path(name).rename(trash/Path(name).name)):
                    result=manager.apply(plan['id'])
                self.assertFalse(result['errors']);self.assertFalse(source.exists());self.assertEqual((external/'secret').read_text(),'outside')
            finally:os.rmdir(root/'linked')

    def test_windows_launcher_switches_update_and_rolls_back_only_before_health(self):
        import windows_launcher
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base=root/'base';base.mkdir();(base/'VERSION').write_text('0.9.7')
            data=root/'local/Sevenova/Orbit';updates=data/'updates';overlay=updates/'releases/0.9.8';overlay.mkdir(parents=True)
            (overlay/'orbit-bin.exe').write_bytes(b'fixture');(overlay/'ota-install.json').write_text(json.dumps({'version':'0.9.8','app_id':'com.sevenova.Orbit'}))
            pointer=updates/'current-version';pointer.write_text('0.9.8')
            calls=[]
            def run(command,**options):
                calls.append(command)
                return subprocess.CompletedProcess(command,1 if len(calls)==1 else 0)
            with patch.dict(os.environ,{'LOCALAPPDATA':str(root/'local'),'ORBIT_SKIP_OTA':''}),patch.object(sys,'executable',str(base/'orbit.exe')),patch.object(sys,'argv',['orbit.exe']),patch('windows_launcher.subprocess.run',side_effect=run):
                self.assertEqual(windows_launcher.main(),0)
            self.assertEqual(Path(calls[0][0]).resolve(),(overlay/'orbit-bin.exe').resolve());self.assertEqual(Path(calls[1][0]).resolve(),(base/'orbit-bin.exe').resolve())
            self.assertFalse(pointer.exists());self.assertEqual((updates/'failed-version').read_text().strip(),'0.9.8')
            pointer.write_text('0.9.8');calls.clear()
            def healthy(command,**options):
                calls.append(command);Path(options['env']['ORBIT_OTA_HEALTH_FILE']).write_text('ready')
                return subprocess.CompletedProcess(command,1)
            with patch.dict(os.environ,{'LOCALAPPDATA':str(root/'local'),'ORBIT_SKIP_OTA':''}),patch.object(sys,'executable',str(base/'orbit.exe')),patch.object(sys,'argv',['orbit.exe']),patch('windows_launcher.subprocess.run',side_effect=healthy):
                self.assertEqual(windows_launcher.main(),1)
            self.assertEqual(len(calls),1);self.assertTrue(pointer.exists());self.assertFalse(list(updates.glob('launch-health.*')))

    def test_native_codex_rpc_pipe_transport(self):
        from codex_support import CodexRpc
        with tempfile.TemporaryDirectory() as temp:
            script=Path(temp)/'server.py'
            script.write_text("import json,sys\nfor line in sys.stdin:\n m=json.loads(line)\n if 'id' in m: print(json.dumps({'id':m['id'],'result':{'data':[],'nextCursor':None}}),flush=True)\n")
            original=subprocess.Popen
            def launch(cmd,**kwargs):return original([sys.executable,str(script)] if cmd[0]=='fixture.exe' else cmd,**kwargs)
            with patch('codex_support.find_codex',return_value='fixture.exe'),patch('codex_support.subprocess.Popen',side_effect=launch):
                with CodexRpc() as rpc:self.assertEqual(rpc.list_threads(),[])
