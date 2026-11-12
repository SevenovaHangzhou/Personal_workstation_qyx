import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.reports import MAX_ATTACHMENT_BYTES, MAX_TEXT_BYTES, ReportFolders, SECURE_DIR_FD, create_report_folder


class ReportFoldersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'reports'
        self.task_id, self.issue_id, self.employee_id = (str(uuid.uuid4()) for _ in range(3))
        self.task = {'id': self.task_id, 'title': '接口验收', 'category': 'work',
                     'linear_issue': {'id': self.issue_id, 'identifier': 'EE-12',
                                      'url': 'https://linear.app/team/issue/EE-12/interface'}}
        self.allowed = [self.task]
        self.folder = self.root / self.task_id
        self.folder.mkdir(parents=True)
        self.marker = self.folder / '.orbit-report.json'
        self.write_marker()
        self.reader = ReportFolders(self.root, lambda: copy.deepcopy(self.allowed))

    def tearDown(self):
        self.temp.cleanup()

    def write_marker(self, **updates):
        marker = {'task_id': self.task_id, 'issue_id': self.issue_id, 'employee_id': self.employee_id}
        marker.update(updates)
        self.marker.write_text(json.dumps(marker), encoding='utf-8')
        self.marker.chmod(0o600)

    def test_list_and_read_utf8_are_purely_read_only(self):
        (self.folder / 'testing').mkdir()
        path = self.folder / 'testing/结果.md'
        path.write_text('# 测试报告\n全部接口验证通过', encoding='utf-8')
        (self.folder / 'chart.png').write_bytes(b'\x89PNG\x00not-a-real-image')
        listing = self.reader.list(self.task_id)
        self.assertEqual([entry['path'] for entry in listing], ['chart.png', 'testing/结果.md'])
        result = self.reader.read(self.task_id, 'testing/结果.md')
        self.assertEqual(result['content'], path.read_bytes())
        self.assertEqual(result['text'], path.read_text(encoding='utf-8'))
        self.assertEqual(result['digest'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result['size'], path.stat().st_size)
        self.assertTrue(result['previewable'])
        self.assertFalse(listing[0]['previewable'])
        self.assertEqual(self.reader.list(self.task_id), listing)
        self.assertEqual(stat.S_IMODE(self.marker.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['.orbit-report.json', 'chart.png', 'testing'])

    def test_each_operation_checks_current_explicit_authorization_and_real_issue(self):
        (self.folder / 'report.md').write_text('报告', encoding='utf-8')
        self.allowed = []
        with self.assertRaisesRegex(ValueError, '授权'):
            self.reader.list(self.task_id)
        with self.assertRaisesRegex(ValueError, '授权'):
            self.reader.read(self.task_id, 'report.md')
        for change in ({'category': 'learn'}, {'is_sample': True},
                       {'linear_issue': {}}, {'linear_issue': dict(self.task['linear_issue'], id='fake')},
                       {'linear_issue': dict(self.task['linear_issue'], url='https://linear.app/team/issue/EE-13/wrong')}):
            self.allowed = [dict(self.task, **change)]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.reader.list(self.task_id)
        self.allowed = [self.task, dict(self.task, id=str(uuid.uuid4()))]
        with self.assertRaisesRegex(ValueError, '重复关联'):
            self.reader.list(self.task_id)

    def test_marker_is_required_matches_issue_and_is_private(self):
        self.marker.unlink()
        with self.assertRaises(ValueError):
            self.reader.list(self.task_id)
        self.write_marker(issue_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.reader.list(self.task_id)
        self.write_marker(task_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.reader.list(self.task_id)
        self.write_marker()
        if os.name != 'nt':
            self.marker.chmod(0o644)
            with self.assertRaisesRegex(ValueError, '600'):
                self.reader.list(self.task_id)

    def test_parent_and_child_links_cannot_escape_root(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'private.txt').write_text('must never be shared', encoding='utf-8')
        ancestor = self.base / 'linked'
        try:
            ancestor.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(ValueError, '链接'):
            ReportFolders(ancestor, lambda: self.allowed).list(self.task_id)
        child = self.folder / 'foreign'
        child.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.reader.list(self.task_id)
        with self.assertRaises(ValueError):
            self.reader.read(self.task_id, 'foreign/private.txt')
        child.unlink()
        (self.folder / 'report.txt').symlink_to(outside / 'private.txt')
        with self.assertRaisesRegex(ValueError, '链接'):
            self.reader.list(self.task_id)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.reader.read(self.task_id, 'report.txt')

    def test_path_traversal_hidden_and_secret_material_never_listed_or_read(self):
        names = ['.env', 'auth.json', 'credentials.json', 'api-key.txt', 'token.csv', 'secret-notes.md',
                 'certificate.pem', 'id_ed25519', '密码.txt', 'notes.sqlite', 'index.db']
        for name in names:
            (self.folder / name).write_text('credential material', encoding='utf-8')
        (self.folder / '.private').mkdir()
        (self.folder / '.private/file.txt').write_text('hidden', encoding='utf-8')
        (self.folder / 'notes.md').write_text('public report', encoding='utf-8')
        self.assertEqual([row['path'] for row in self.reader.list(self.task_id)], ['notes.md'])
        for path in names + ['.private/file.txt', '../outside.txt', '/etc/passwd', 'nested/../../outside.txt',
                             'C:/Windows/test.txt', 'nested\\report.md', './notes.md', 'nested//notes.md',
                             '.orbit-report.json', 'notes.md\x00', 'CON.txt']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.reader.read(self.task_id, path)

    def test_oversized_nonpreview_metadata_does_not_load_attachment(self):
        path = self.folder / 'large.pdf'
        with path.open('wb') as stream:
            stream.truncate(MAX_TEXT_BYTES + 1)
        listing = self.reader.list(self.task_id)
        self.assertEqual(listing[0]['size'], MAX_TEXT_BYTES + 1)
        self.assertFalse(listing[0]['previewable'])
        self.assertEqual(listing[0]['digest'], '')
        with self.assertRaisesRegex(ValueError, '格式只列清单'):
            self.reader.read(self.task_id, 'large.pdf')
        path.rename(self.folder / 'large.txt')
        with self.assertRaisesRegex(ValueError, '2MiB'):
            self.reader.read(self.task_id, 'large.txt')
        with (self.folder / 'large.txt').open('wb') as stream:
            stream.truncate(MAX_ATTACHMENT_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '100MiB'):
            self.reader.list(self.task_id)

    def test_binary_or_non_utf8_text_is_not_previewable(self):
        for name, content in [('binary.txt', b'normal\x00binary'), ('encoding.md', b'\xff\xfe\x81'),
                              ('valid.csv', b'\xef\xbb\xbfitem,value\na,1')]:
            (self.folder / name).write_bytes(content)
        listing = {row['path']: row for row in self.reader.list(self.task_id)}
        self.assertFalse(listing['binary.txt']['previewable'])
        self.assertFalse(listing['encoding.md']['previewable'])
        self.assertTrue(listing['valid.csv']['previewable'])
        for name in ('binary.txt', 'encoding.md'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.reader.read(self.task_id, name)
        self.assertEqual(self.reader.read(self.task_id, 'valid.csv')['text'], 'item,value\na,1')

    def test_no_implicit_folder_creation_and_clear_file_limit(self):
        missing = self.base / 'missing'
        with self.assertRaises(ValueError):
            ReportFolders(missing, lambda: self.allowed).list(self.task_id)
        self.assertFalse(missing.exists())
        for number in range(3):
            (self.folder / f'{number}.txt').write_text('report', encoding='utf-8')
        with patch('evaluation.reports.MAX_FILES', 2), self.assertRaisesRegex(ValueError, '超过500'):
            self.reader.list(self.task_id)

    def test_revoked_during_read_does_not_return_contents(self):
        (self.folder / 'report.txt').write_text('report', encoding='utf-8')
        calls = 0
        def authorized():
            nonlocal calls
            calls += 1
            return self.allowed if calls == 1 else []
        reader = ReportFolders(self.root, authorized)
        with self.assertRaisesRegex(ValueError, '授权'):
            reader.read(self.task_id, 'report.txt')

    @unittest.skipUnless(SECURE_DIR_FD, 'POSIX descriptor-safe opening required')
    def test_link_swap_between_stat_and_open_fails_closed(self):
        target = self.folder / 'report.txt'
        target.write_text('report', encoding='utf-8')
        outside = self.base / 'outside.txt'
        outside.write_text('must not return this', encoding='utf-8')
        original_open = os.open
        swapped = False
        def replacing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == 'report.txt' and not swapped:
                swapped = True
                target.unlink()
                target.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)
        with patch('evaluation.reports.os.open', side_effect=replacing_open), self.assertRaises(ValueError):
            self.reader.read(self.task_id, 'report.txt')
        self.assertTrue(swapped)

    @unittest.skipUnless(hasattr(os, 'link'), 'hard links unavailable')
    def test_hardlinks_are_not_shared(self):
        private = self.base / 'private.txt'
        private.write_text('private', encoding='utf-8')
        os.link(private, self.folder / 'report.txt')
        with self.assertRaisesRegex(ValueError, '链接'):
            self.reader.read(self.task_id, 'report.txt')
        with self.assertRaisesRegex(ValueError, '链接'):
            self.reader.list(self.task_id)


class CreateReportFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'reports'
        self.root.mkdir()
        self.task_id, self.issue_id, self.employee_id = (str(uuid.uuid4()) for _ in range(3))
        self.task = {'id': self.task_id, 'title': '接口报告', 'category': 'work',
                     'linear_issue': {'id': self.issue_id, 'identifier': 'EE-12',
                                      'url': 'https://linear.app/team/issue/EE-12/interface'}}
        self.folder = self.root/self.task_id
        self.marker = self.folder/'.orbit-report.json'

    def create(self):
        return create_report_folder(self.root, self.task, self.employee_id)

    def test_explicit_creation_is_private_atomic_and_same_binding_idempotent(self):
        self.assertEqual(self.create(), self.folder)
        expected = dict(task_id=self.task_id, issue_id=self.issue_id, employee_id=self.employee_id)
        self.assertEqual(json.loads(self.marker.read_text()), expected)
        self.assertEqual(stat.S_IMODE(self.marker.stat().st_mode), 0o600)
        self.assertEqual(self.marker.stat().st_nlink, 1)
        before = self.marker.stat().st_mtime_ns, self.marker.read_bytes()
        (self.folder/'report.md').write_text('现有资料必须保留', encoding='utf-8')
        self.task['title'] = '改标题不改变资料关联'
        self.assertEqual(self.create(), self.folder)
        self.assertEqual((self.marker.stat().st_mtime_ns, self.marker.read_bytes()), before)
        self.assertEqual((self.folder/'report.md').read_text(), '现有资料必须保留')
        self.assertFalse(list(self.folder.glob('*.tmp')))
        self.assertEqual(ReportFolders(self.root, lambda: [self.task]).list(self.task_id)[0]['path'], 'report.md')

    def test_root_must_already_exist_and_task_must_be_real_work_issue(self):
        missing = self.base/'missing'
        with self.assertRaises(ValueError):
            create_report_folder(missing, self.task, self.employee_id)
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            create_report_folder('relative', self.task, self.employee_id)
        for updates in ({'category': 'learn'}, {'is_sample': True}, {'id': '../other'},
                        {'linear_issue': {}}, {'linear_issue': dict(self.task['linear_issue'], id='fake')},
                        {'linear_issue': dict(self.task['linear_issue'], url='https://linear.app/team/issue/EE-13/wrong')}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                create_report_folder(self.root, dict(self.task, **updates), self.employee_id)
        with self.assertRaises(ValueError):
            create_report_folder(self.root, self.task, 'not-an-employee-uuid')
        self.assertFalse(self.folder.exists())

    def test_existing_marker_binding_cannot_be_reassigned_or_corrupt_replaced(self):
        self.create()
        before = self.marker.read_bytes()
        self.task['linear_issue']['id'] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, '绑定|关联'):
            self.create()
        self.assertEqual(self.marker.read_bytes(), before)
        self.task['linear_issue']['id'] = self.issue_id
        with self.assertRaisesRegex(ValueError, '绑定|关联'):
            create_report_folder(self.root, self.task, str(uuid.uuid4()))
        self.assertEqual(self.marker.read_bytes(), before)
        self.marker.write_text('broken marker')
        with self.assertRaisesRegex(ValueError, '损坏'):
            self.create()
        self.assertEqual(self.marker.read_text(), 'broken marker')

    def test_linked_root_ancestor_folder_or_marker_is_never_followed(self):
        outside = self.base/'outside'
        outside.mkdir()
        outside_file = outside/'private.txt'
        outside_file.write_text('must not change')
        alias = self.base/'linked-root'
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        for root in (alias, alias/'nested'):
            if root.name == 'nested':
                (self.root/'nested').mkdir()
            with self.assertRaisesRegex(ValueError, '链接'):
                create_report_folder(root, self.task, self.employee_id)
        self.folder.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.create()
        self.folder.unlink()
        self.folder.mkdir()
        self.marker.symlink_to(outside_file)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.create()
        self.assertEqual(outside_file.read_text(), 'must not change')
        self.assertTrue(self.marker.is_symlink())

    @unittest.skipUnless(hasattr(os, 'link'), 'hard links unavailable')
    def test_hardlinked_existing_marker_is_rejected_without_changing_target(self):
        self.create()
        outside = self.base/'original-marker'
        self.marker.rename(outside)
        os.link(outside, self.marker)
        before = outside.read_bytes()
        with self.assertRaisesRegex(ValueError, '链接'):
            self.create()
        self.assertEqual(outside.read_bytes(), before)
        self.assertEqual(outside.stat().st_nlink, 2)

    def test_random_temporary_symlink_collision_is_rejected_without_following(self):
        self.folder.mkdir()
        outside = self.base/'outside.txt'
        outside.write_text('must not change')
        fixed = uuid.UUID('12345678-1234-4234-8234-123456789abc')
        temporary = self.folder/('.orbit-report.' + fixed.hex + '.tmp')
        try:
            temporary.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with patch('evaluation.reports.uuid.uuid4', return_value=fixed), self.assertRaises(ValueError):
            self.create()
        self.assertEqual(outside.read_text(), 'must not change')
        self.assertTrue(temporary.is_symlink())
        self.assertFalse(self.marker.exists())

    def test_binding_change_during_write_is_rejected_and_temporary_cleaned(self):
        original = os.open
        changed = False
        def swapping(path, flags, *args, **kwargs):
            nonlocal changed
            if str(path).endswith('.tmp') and not changed:
                changed = True
                self.task['linear_issue']['id'] = str(uuid.uuid4())
            return original(path, flags, *args, **kwargs)
        with patch('evaluation.reports.os.open', side_effect=swapping), self.assertRaisesRegex(ValueError, '关联变化'):
            self.create()
        self.assertTrue(changed)
        self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.folder.iterdir()), [])

    @unittest.skipUnless(SECURE_DIR_FD, 'POSIX descriptor-safe creation required')
    def test_root_swap_during_creation_cannot_write_into_replacement(self):
        replacement = self.base/'replacement'
        replacement.mkdir()
        moved = self.base/'original-root'
        original = os.mkdir
        changed = False
        def swapping(path, *args, **kwargs):
            nonlocal changed
            result = original(path, *args, **kwargs)
            if path == self.task_id and not changed:
                changed = True
                self.root.rename(moved)
                self.root.symlink_to(replacement, target_is_directory=True)
            return result
        with patch('evaluation.reports.os.mkdir', side_effect=swapping), self.assertRaisesRegex(ValueError, '变化'):
            self.create()
        self.assertTrue(changed)
        self.assertEqual(list(replacement.iterdir()), [])
        self.assertFalse((moved/self.task_id/'.orbit-report.json').exists())

    def test_descriptorless_fallback_still_validates_binding_and_chains(self):
        with patch('evaluation.reports.SECURE_DIR_FD', False):
            self.assertEqual(self.create(), self.folder)
            self.assertEqual(self.create(), self.folder)
            self.task['linear_issue']['id'] = str(uuid.uuid4())
            with self.assertRaisesRegex(ValueError, '绑定|关联'):
                self.create()


if __name__ == '__main__':
    unittest.main()
