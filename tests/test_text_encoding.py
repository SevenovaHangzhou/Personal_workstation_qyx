"""Persisted UTF-8 data must not depend on the Windows ANSI code page."""
from contextlib import contextmanager
import copy
import gc
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from app_profile import PROFILES
from framework import api
from model import JsonTaskRepository, make_task
from runtime import MomoRuntime
from split_migration import MARKER, split_data, write_json


@contextmanager
def gbk_default_files():
    """Emulate Chinese Windows even when the test runner enables UTF-8 mode.

    Patch file opening and pathlib's default encoding resolution. UTF-8 mode
    resolves omitted read_text/write_text encodings before Path.open is called,
    so both boundaries must emulate the locale. Explicit UTF-8 and binary file
    access remain unchanged, including SQLite, certificates, and OTA archives.
    """
    original = Path.open
    original_text_encoding = io.text_encoding

    def open_file(path, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
        if 'b' not in mode and encoding in (None, 'locale'):
            encoding = 'gbk'
        return original(path, mode, buffering, encoding, errors, newline)

    def text_encoding(encoding, stacklevel=2):
        if encoding in (None, 'locale'):
            return 'gbk'
        return original_text_encoding(encoding, stacklevel)

    with patch.object(Path, 'open', open_file), patch.object(io, 'text_encoding', text_encoding):
        yield


class TextEncodingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='orbit-encoding-')
        self.root = Path(self.temporary.name)
        self.runtime = None
        self.addCleanup(self.temporary.cleanup)
        # Finalize SQLite objects before Windows removes the temporary folder.
        self.addCleanup(gc.collect)
        self.addCleanup(self.close_runtime)

    def start_runtime(self, profile):
        self.runtime = MomoRuntime(self.root/'tasks.json', auto_tick=False, profile=profile).start()
        return self.runtime

    def close_runtime(self):
        if self.runtime is not None:
            self.runtime.close(apply_update=False)
            self.runtime = None
        gc.collect()

    def test_locale_fixture_rejects_utf8_while_explicit_encoding_roundtrips(self):
        path = self.root/'encoding-probe.txt'
        text = '中文资料与问题背景 ✦ 🚀'
        with gbk_default_files():
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(UnicodeDecodeError):
                path.read_text()
            with self.assertRaises(UnicodeDecodeError):
                with path.open(encoding='locale') as stream:
                    stream.read()
            self.assertEqual(path.read_text(encoding='utf-8'), text)
            self.assertEqual(path.read_bytes(), text.encode('utf-8'))
            # Omitted writes must exercise the same locale as omitted reads.
            with self.assertRaises(UnicodeEncodeError):
                (self.root/'default-write.txt').write_text(text)

    def test_work_runtime_reopens_chinese_collaboration_and_identity_markers(self):
        runtime = self.start_runtime('work')
        runtime.tasks.clear_samples()
        task = runtime.tasks.add(make_task('机械臂项目：排查启动问题 🚀', 'work'))
        device_name = '七耀星 · 同事的 Windows 电脑 ✦ 🚀'
        runtime.collaboration_service.state['config']['name'] = device_name
        runtime.collaboration_service.persist()
        self.close_runtime()
        write_json(self.root/'.application.json', dict(app_id=PROFILES['work'].app_id,
                   product='work', note='保留用户数据与协作资料 🚀'))
        write_json(self.root/MARKER, dict(product='work', source='旧版资料目录 🚀'))

        with gbk_default_files():
            runtime = self.start_runtime('work')
            self.assertEqual(runtime.tasks.get(task['id'])['title'], task['title'])
            state = runtime.collaboration.snapshot()
            self.assertEqual(state['config']['name'], device_name)
            self.assertFalse(state['config']['enabled'])
            self.assertIsNone(runtime.collaboration_service.listener)
            self.assertIsNone(runtime.collaboration_service.discovery)
            self.assertIsNone(runtime.daily_service.worker)
            self.assertIsNone(runtime.update_service.clock)
            self.close_runtime()

    def test_split_migration_and_recovery_preserve_unicode_paths_and_credentials(self):
        from data_paths import credential_identity
        from split_migration import default_split_directory
        home = self.root/'中文资料'/'Sevenova'
        source = home/'Orbit'
        source.mkdir(parents=True)
        repository = JsonTaskRepository(source/'tasks.json')
        repository.clear_samples()
        work = make_task('工作资料与开发计划 🚀', 'work')
        study = make_task('学习机械臂控制 ✦', 'learn')
        repository.add(work)
        repository.add(study)
        write_json(source/'.orbit-data-migration.json',
                   dict(credential_path=str(source/'最早的数据空间'), legacy_path=str(source)))
        before = (source/'tasks.json').read_bytes()
        identity = credential_identity(source)

        with gbk_default_files():
            result = split_data(source, home)
            self.assertEqual(split_data(source, home), result)
            for product in ('work', 'study'):
                with patch('split_migration.PROFILE', PROFILES[product]):
                    self.assertEqual(default_split_directory(home.parent), result[product])
            self.assertEqual(credential_identity(result['work']), identity)
            self.assertEqual(JsonTaskRepository(result['work']/'tasks.json').get(work['id'])['title'], work['title'])
            self.assertEqual(JsonTaskRepository(result['study']/'tasks.json').get(study['id'])['title'], study['title'])
        self.assertEqual((source/'tasks.json').read_bytes(), before)
        self.assertEqual(len(list((home/'Backups').iterdir())), 1)

    def test_study_runtime_reopens_course_feedback_reading_and_question_history(self):
        from learning_content import BUILTIN_COURSE_ID
        from services.LearningService import atomic_json
        runtime = self.start_runtime('study')
        runtime.tasks.clear_samples()
        task = runtime.tasks.add(make_task('学习力矩控制：从零建立知识体系 🚀', 'learn'))
        service = runtime.learning_service
        course = copy.deepcopy(service.courses[BUILTIN_COURSE_ID])
        course.update(id='encoding-course', title='从入门到理解的课程 ✦ 🚀', origin='generated')
        atomic_json(service.folder/'courses/encoding-course.json', course)
        service.courses[course['id']] = course
        runtime.learning.bind(task['id'], course['id'])
        feedback = '请用图解解释公式的物理意义 🚀'
        runtime.learning.save_feedback(task['id'], course['id'], feedback)
        lesson = course['package']['lessons'][0]
        question = '这个力矩公式中的坐标系怎么选？✦ 🚀'
        runtime.learning.save_study(task_id=task['id'], course_id=course['id'],
            lesson_id=lesson['id'], lesson_revision=lesson['revision'], scroll_ratio=.4, question=question)
        tutor = service.tutor
        turn = dict(id='encoding-answer', status='done', question=question,
                    answer='已知结论：先统一坐标系，再计算力矩。🚀')
        tutor.state['conversations'][tutor.key(task['id'], course['id'])] = dict(turns=[turn])
        tutor._save()
        self.close_runtime()

        with gbk_default_files():
            runtime = self.start_runtime('study')
            self.assertEqual(runtime.learning.course(course['id'], task['id'])['title'], course['title'])
            self.assertEqual(runtime.learning.snapshot(task['id'])['feedback_draft'], feedback)
            self.assertEqual(runtime.learning.study_state(task['id'], course['id'])['question'], question)
            self.assertEqual(runtime.learning.tutor_snapshot(task['id'], course['id'])['turns'], [turn])
            self.assertFalse(runtime.learning_service.tutor.busy())
            self.assertIsNone(runtime.collaboration)
            self.close_runtime()

    def test_ota_preparation_reads_unicode_manifest_without_locale_loss(self):
        from ota_core import API, REPOSITORY, stage_release
        version = '0.10.3'
        name = f'orbit-{version}-windows-x86_64.zip'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as package:
            for binary in ('orbit.exe', 'orbit-bin.exe'):
                package.writestr('orbit/'+binary, b'isolated package fixture')
        payload = archive.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        manifest = dict(version=version, platform='windows-x86_64', minimum_windows='10',
                        app_id=PROFILES['work'].app_id, title='星序更新：中文支持 🚀',
                        artifacts=[dict(file=name, bytes=len(payload), sha256=digest)])
        files = {name:payload, 'windows-manifest.json':json.dumps(manifest, ensure_ascii=False).encode('utf-8'),
                 'WINDOWS-SHA256SUMS':(digest+'  '+name+'\n').encode('utf-8')}
        assets = [dict(id=i, name=filename, url=API+'/releases/assets/'+str(i), state='uploaded',
                       size=len(data), digest='sha256:'+hashlib.sha256(data).hexdigest())
                  for i, (filename, data) in enumerate(files.items(), 1)]
        release = dict(tag_name='v'+version, draft=False, prerelease=False, assets=assets,
                       html_url='https://github.com/'+REPOSITORY+'/releases/tag/v'+version)

        class LocalSource:
            def asset(self, asset, destination, event, progress):
                Path(destination).write_bytes(files[asset['name']])

        with gbk_default_files(), patch('ota_core.platform.system', return_value='Windows'), \
                patch('ota_core.platform.machine', return_value='AMD64'), \
                patch('ota_core.PROFILE', PROFILES['work']), patch('ota_core.SLUG', 'orbit'), \
                patch('ota_core.probe') as probe:
            ready = stage_release(self.root/'updates', release, threading.Event(), lambda *_:None, LocalSource())
            self.assertEqual(ready['version'], version)
            self.assertEqual(stage_release(self.root/'updates', release, threading.Event(), lambda *_:None, LocalSource()), ready)
            self.assertEqual(probe.call_count, 3)
        self.assertFalse((self.root/'updates/current-version').exists())

    def test_ota_saved_state_and_install_stamp_remain_readable_with_unicode_metadata(self):
        from services.UpdateService import UpdateService
        folder = self.root/'updates'
        ready = dict(version='0.10.3', sha256='a'*64, app_id=PROFILES['work'].app_id,
                     note='退出后更新，保留旧版以供回退 🚀')
        target = folder/'releases'/ready['version']
        target.mkdir(parents=True)
        write_json(target/'ota-install.json', ready)
        write_json(folder/'state.json', dict(enabled=False, ready=ready, last_checked='2026-11-12'))
        with gbk_default_files():
            api.init(2)
            service = UpdateService(api.server(), folder, auto_tick=False, current='0.10.2')
            self.assertEqual(service.state['status'], 'ready')
            self.assertEqual(service.state['ready'], ready)
            self.assertFalse(service.state['enabled'])
            service.activate()
            self.assertEqual((folder/'current-version').read_text(encoding='utf-8'), ready['version']+'\n')


if __name__ == '__main__':
    unittest.main()
