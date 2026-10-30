"""Offline release probe, always using disposable data and no model calls."""
import json
from pathlib import Path
import subprocess
import tempfile
from PyQt5.QtCore import QCoreApplication
from framework import api
from learning_content import BUILTIN_COURSE_ID
from learning_runner import ROOT
from learning_ui import CourseReader
from model import make_task
from process_environment import system_environment
from runtime import MomoRuntime
from typedefine.TypeDefine import APP_VERSION


def run_checks(app, window_factory):
    with tempfile.TemporaryDirectory(prefix='orbit-self-test-') as temporary:
        runtime = MomoRuntime(Path(temporary)/'tasks.json', auto_tick=False).start()
        window = reader = None
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
            app.processEvents()
            skill = ROOT/'.agents/skills/learning-course-authoring'
            for name in ('SKILL.md', 'references/course-standard.md', 'references/course-package.md'):
                assert (skill/name).read_text(encoding='utf-8').strip(), name
            command = subprocess.run(['git', '--version'], env=system_environment(), capture_output=True, text=True, timeout=10)
            assert command.returncode == 0, command.stderr
            print(json.dumps(dict(app_version=APP_VERSION, framework_version=api.version(),
                                  checks=['services', 'structure-undo', 'progress-records', 'course-14-chapters',
                                          'native-reader', 'teaching-skill', 'system-git'],
                                  user_data_modified=False, model_called=False), ensure_ascii=False))
            return 0
        finally:
            if reader:
                reader.close(); reader.deleteLater()
            if window:
                window.hide(); window.tray.hide(); window.deleteLater()
            QCoreApplication.sendPostedEvents(None, 0)
            app.processEvents()
            runtime.close()
