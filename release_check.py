"""Offline release probe, always using disposable data and no model calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from PyQt5.QtCore import QCoreApplication
from framework import api
from learning_content import BUILTIN_COURSE_ID
from learning_runner import ROOT
from learning_ui import CourseReader, LearningDialog
from model import make_task
from process_environment import system_environment
from runtime import MomoRuntime
from typedefine.TypeDefine import APP_VERSION


def run_checks(app, window_factory, graph_port=None):
    with tempfile.TemporaryDirectory(prefix='orbit-self-test-') as temporary:
        runtime = MomoRuntime(Path(temporary)/'tasks.json', auto_tick=False, graph_port=graph_port).start()
        window = reader = panel = None
        try:
            runtime.tasks.clear_samples()
            parent = make_task('隔离自检', 'learn'); child = make_task('验证步骤', 'learn')
            runtime.tasks.add(parent); runtime.tasks.add(child)
            runtime.tasks.merge(child['id'], parent['id'])
            runtime.tasks.record_progress(child['id'], 'verification', '离线发布自检')
            runtime.tasks.undo_structure(runtime.tasks.undo_state()['id'])
            assert runtime.tasks.get(child['id'])['parent_id'] is None
            assert len(runtime.tasks.get(child['id'])['evidence']) == 1
            runtime.learning.bind(parent['id'], BUILTIN_COURSE_ID)
            window = window_factory(runtime)
            window.select_task(parent['id'])
            reader = CourseReader(window, runtime.learning, parent['id'], BUILTIN_COURSE_ID)
            assert len(reader.lesson_rows) == 14
            reader.show_lesson(0)
            runtime.learning.save_feedback(parent['id'],BUILTIN_COURSE_ID,'离线检查：讲透分步案例')
            panel=LearningDialog(window,parent['id']); panel.improve()
            assert panel.feedback.toPlainText()=='离线检查：讲透分步案例'
            assert panel.regenerate_button.isEnabled()
            panel.update_generation_progress({'jobs':[{'id':'offline-progress','status':'running',
                'started_at':'2026-11-07T00:00:00+00:00','progress':{'stage':'writing'}}]})
            assert panel.progress.value()==3 and panel.progress.maximum()==7
            assert '编写' in panel.progress_text.text()
            from data_paths import default_data_directory
            from credentials import CredentialStore
            if os.name=='nt':
                from windows_credentials import transform
                assert transform(transform(b'offline-fixture'),True)==b'offline-fixture'
                assert default_data_directory(Path(temporary)/'data')==Path(temporary)/'data/Sevenova/Orbit'
            else:
                legacy=Path(temporary)/'data/MomoLocal/Momo';legacy.mkdir(parents=True)
                (legacy/'probe.txt').write_text('preserved',encoding='utf-8')
                identity=CredentialStore(legacy).service
                migrated=default_data_directory(Path(temporary)/'data')
                assert migrated==Path(temporary)/'data/Sevenova/Orbit'
                assert legacy.is_symlink() and (legacy/'probe.txt').read_text(encoding='utf-8')=='preserved'
                assert CredentialStore(migrated).service==identity
            import tomllib
            from daily_summary import disabled_mcp_arguments
            overrides=disabled_mcp_arguments({'server.with.dot':{'command':'unused-server'}})
            assert tomllib.loads(overrides[1])['mcp_servers']['server.with.dot']['enabled'] is False
            reader.add_block({'type':'diagram','title':'离线教学图解','caption':'顺序示意，不是仿真。',
                              'nodes':[{'id':'a','title':'输入','explanation':'确定输入条件。'},
                                       {'id':'b','title':'结果','explanation':'解释结果并核对。'}],
                              'edges':[{'source':'a','target':'b','label':'推理'}]})
            from learning_diagram_ui import LessonDiagram
            diagram=reader.findChild(LessonDiagram); assert diagram is not None
            diagram.move(1); assert '解释结果' in diagram.explanation.text()
            app.processEvents()
            skill = ROOT/'.agents/skills/learning-course-authoring'
            for name in ('SKILL.md', 'references/course-standard.md', 'references/course-package.md'):
                assert (skill/name).read_text(encoding='utf-8').strip(), name
            command = subprocess.run(['git', '--version'], env=system_environment(), capture_output=True, text=True, timeout=10)
            assert command.returncode == 0, command.stderr
            graph_checks = []
            if runtime.graph:
                from urllib.request import urlopen
                before = runtime.graph.snapshot()
                for resource in ('/', '/app.js', '/app.css', '/api/snapshot'):
                    with urlopen(runtime.graph.url+resource, timeout=3) as response:
                        data = response.read()
                    assert data, resource
                    if resource == '/api/snapshot':
                        snapshot = json.loads(data)
                        registered = [s for s in snapshot['services'] if s['registered']]
                        assert len(registered) == 15
                        assert sum(not s['builtin'] for s in registered) == 10
                        assert snapshot['version'] == api.version()
                        assert snapshot['edges'] == before['edges']
                graph_checks = ['graph-10-business-5-builtin', 'graph-http-assets', 'graph-query-does-not-pollute']
            print(json.dumps(dict(app_version=APP_VERSION, framework_version=api.version(),
                                  checks=['services', 'structure-undo', 'progress-records', 'course-14-chapters',
                                          'native-reader', 'teaching-skill', 'system-git',
                                          'course-feedback-draft', 'native-teaching-diagram',
                                          'generation-stage-progress', 'legacy-data-migration',
                                          'daily-mcp-config-override']+graph_checks,
                                  user_data_modified=False, model_called=False), ensure_ascii=False))
            return 0
        finally:
            if runtime.graph: runtime.graph.stop_http()
            if panel:
                panel.close(); panel.deleteLater()
            if reader:
                reader.close(); reader.deleteLater()
            if window:
                window.hide(); window.tray.hide(); window.deleteLater()
            QCoreApplication.sendPostedEvents(None, 0)
            app.processEvents()
            runtime.close(apply_update=False, final_shutdown=runtime.graph is not None)
