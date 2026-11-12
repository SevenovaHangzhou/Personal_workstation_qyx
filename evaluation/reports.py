"""Read-only, currently authorized report folders; no arbitrary filesystem API.

The explicit creator below owns ``root/<task UUID>/.orbit-report.json``.
ReportFolders itself never creates folders, follows links, or caches contents.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import uuid

from evaluation.protocol import valid_uuid


MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
MAX_FILES = 500
TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.json'}
MARKER = '.orbit-report.json'
SECRET_WORDS = {'auth', 'credentials', 'credential', 'token', 'tokens', 'key', 'keys', 'secret', 'secrets',
                'password', 'passwd', 'apikey', 'privatekey', 'keystore', 'keychain'}
SECRET_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.crt', '.cer', '.der', '.jks', '.keystore', '.kdbx', '.p7b', '.p7c',
                   '.sqlite', '.sqlite3', '.db'}
WINDOWS_RESERVED = {'con', 'prn', 'aux', 'nul'} | {f'{prefix}{index}' for prefix in ('com', 'lpt') for index in range(1, 10)}
SECURE_DIR_FD = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW')


def _secret(name):
    lowered = name.casefold()
    words = set(re.findall(r'[a-z0-9]+', lowered))
    return (bool(words & SECRET_WORDS) or Path(lowered).suffix in SECRET_SUFFIXES or
            lowered.startswith(('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519')) or
            any(word in name for word in ('密钥', '凭据', '密码', '口令', '证书')))


def _component(value):
    if (not isinstance(value, str) or not value or len(value) > 255 or value.startswith('.') or
            value.endswith(('.', ' ')) or any(ord(char) < 32 for char in value) or
            any(char in value for char in '/\\:\x00') or value.split('.')[0].casefold() in WINDOWS_RESERVED):
        raise ValueError('汇报资料路径无效，不能使用隐藏或特殊路径')
    if _secret(value):
        raise ValueError('凭据、密钥或证书不能作为共享汇报资料')
    return value


def _relative(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError('请选择有效的汇报资料相对路径')
    parts = value.split('/')
    if len(parts) > 32:
        raise ValueError('汇报资料目录层级超过32层')
    return tuple(_component(part) for part in parts)


def _is_link(info):
    # Windows junctions and other reparse points are also outside the contract.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _identity(info):
    return info.st_dev, info.st_ino


def _version(info):
    return _identity(info) + (info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _modified(info):
    return datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()


def _chain(path):
    """Check every absolute ancestor, including Windows reparse points."""
    if not path.is_absolute():
        raise ValueError('汇报根目录须为本机绝对路径')
    chain = []
    for current in [*reversed(path.parents), path]:
        info = current.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('汇报目录及其祖先不能是链接或非目录')
        chain.append((current, _identity(info)))
    return chain


def _unchanged_chain(chain):
    for path, identity in chain:
        info = path.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode) or _identity(info) != identity:
            raise ValueError('汇报目录在读取期间变化，请重新读取')


@contextmanager
def _directory(path):
    chain = _chain(path)
    descriptor = None
    try:
        if SECURE_DIR_FD:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(path.anchor, flags)
            for component in path.parts[1:]:
                following = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
            if _identity(os.fstat(descriptor)) != chain[-1][1]:
                raise ValueError('汇报目录在读取期间变化，请重新读取')
        yield descriptor, chain
        _unchanged_chain(chain)
    except OSError as exc:
        raise ValueError('汇报目录无法安全读取，目录或链接可能已变化') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def _file(folder, parts, descriptor=None):
    """Open a regular, non-linked file and verify its identity after reading."""
    opened = []
    file_descriptor = None
    path = folder.joinpath(*parts)
    ancestor_chain = _chain(path.parent)
    try:
        if SECURE_DIR_FD and descriptor is not None:
            parent = descriptor
            for component in parts[:-1]:
                parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                opened.append(parent)
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('汇报资料必须是普通文件，不能使用链接')
            file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        else:
            info = path.lstat()
            if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('汇报资料必须是普通文件，不能使用链接')
            file_descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        before = os.fstat(file_descriptor)
        if _version(before) != _version(info):
            raise ValueError('汇报资料在读取期间变化，请重新读取')
        with os.fdopen(file_descriptor, 'rb') as stream:
            file_descriptor = None
            yield stream, before
            after = os.fstat(stream.fileno())
            current = path.lstat()
            if _is_link(current) or _version(after) != _version(before) or _version(current) != _version(before):
                raise ValueError('汇报资料在读取期间变化，请重新读取')
            _unchanged_chain(ancestor_chain)
    except OSError as exc:
        raise ValueError('汇报资料无法安全读取，文件或目录可能已变化') from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for item in reversed(opened):
            os.close(item)


class ReportFolders:
    """Read folders for a callback's *current, explicitly shared* work tasks.

    ``authorized_tasks_callback()`` returns task dictionaries, not task IDs. The
    caller must return an empty list when sharing is disabled or access revoked.
    A task needs a verified-looking UUID/Linear URL mapping and its matching marker.
    The API itself performs no remote Linear lookup or attachment persistence.
    """

    def __init__(self, root, authorized_tasks_callback):
        self.root = Path(root).expanduser()
        if not self.root.is_absolute():
            raise ValueError('汇报根目录须为本机绝对路径')
        if not callable(authorized_tasks_callback):
            raise ValueError('汇报资料需要当前授权任务回调')
        self.authorized_tasks_callback = authorized_tasks_callback

    def _task(self, task_id):
        if not valid_uuid(task_id):
            raise ValueError('汇报任务 UUID 无效')
        tasks = self.authorized_tasks_callback()
        if not isinstance(tasks, (list, tuple)):
            raise ValueError('当前汇报资料授权不可用')
        candidates = [task for task in tasks if isinstance(task, dict) and task.get('id') == task_id]
        if len(candidates) != 1:
            raise ValueError('此任务当前未获汇报资料共享授权')
        task = candidates[0]
        issue = task.get('linear_issue')
        if (task.get('category') != 'work' or task.get('is_sample') or not isinstance(issue, dict) or
                issue.get('demo') or not valid_uuid(issue.get('id'))):
            raise ValueError('只能读取已授权且绑定真实 issue 的工作任务资料')
        if not isinstance(issue.get('url'), str):
            raise ValueError('工作任务的 Linear issue 链接无效')
        parsed = urlparse(issue['url'])
        segments = parsed.path.strip('/').split('/')
        identifier = issue.get('identifier', '')
        if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*-[0-9]+', identifier) or
                parsed.scheme != 'https' or parsed.netloc != 'linear.app' or parsed.query or parsed.fragment or
                len(segments) < 3 or segments[1] != 'issue' or segments[2] != identifier):
            raise ValueError('工作任务的 Linear issue 关联不完整或不一致')
        if sum(isinstance(other, dict) and isinstance(other.get('linear_issue'), dict) and
               other['linear_issue'].get('id') == issue['id'] for other in tasks) != 1:
            raise ValueError('多个任务重复关联同一 issue，请先核对')
        return issue['id']

    def _marker(self, folder, descriptor, task_id, issue_id):
        with _file(folder, (MARKER,), descriptor) as (stream, info):
            if os.name != 'nt' and stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('汇报目录标识权限须为600，请由星序重新创建目录标识')
            if info.st_size > 16 * 1024:
                raise ValueError('汇报目录标识过大')
            try:
                marker = json.loads(stream.read(16 * 1024 + 1).decode('utf-8'))
            except (ValueError, UnicodeError) as exc:
                raise ValueError('汇报目录标识损坏') from exc
            if (not isinstance(marker, dict) or marker.get('task_id') != task_id or
                    marker.get('issue_id') != issue_id or not valid_uuid(marker.get('employee_id'))):
                raise ValueError('汇报目录标识与当前任务或 issue 不一致')

    @contextmanager
    def _authorized_folder(self, task_id):
        issue_id = self._task(task_id)
        folder = self.root / task_id
        try:
            with _directory(folder) as (descriptor, chain):
                self._marker(folder, descriptor, task_id, issue_id)
                yield folder, descriptor
                if self._task(task_id) != issue_id:
                    raise ValueError('汇报任务的 issue 关联已变化，请重新读取')
                self._marker(folder, descriptor, task_id, issue_id)
                _unchanged_chain(chain)
        except OSError as exc:
            raise ValueError('汇报目录尚未创建或无法读取，请由员工在星序中打开汇报文件夹') from exc

    def _metadata(self, folder, parts, descriptor):
        relative = '/'.join(parts)
        with _file(folder, parts, descriptor) as (stream, info):
            if info.st_size > MAX_ATTACHMENT_BYTES:
                raise ValueError('单份汇报资料超过100MiB，请在个人端整理后读取清单')
            digest = ''
            previewable = info.st_size <= MAX_TEXT_BYTES and Path(parts[-1]).suffix.casefold() in TEXT_SUFFIXES
            if info.st_size <= MAX_TEXT_BYTES:
                content = stream.read(MAX_TEXT_BYTES + 1)
                if len(content) != info.st_size or len(content) > MAX_TEXT_BYTES:
                    raise ValueError('汇报资料在读取期间变化，请重新读取')
                digest = hashlib.sha256(content).hexdigest()
                if previewable:
                    try:
                        content.decode('utf-8-sig')
                        previewable = b'\x00' not in content
                    except UnicodeDecodeError:
                        previewable = False
            return {'path': relative, 'size': info.st_size, 'digest': digest,
                    'previewable': previewable,
                    'modified': _modified(info)}

    def list(self, task_id):
        result = []
        visited = 0
        with self._authorized_folder(task_id) as (folder, descriptor):
            def walk(path, parts=()):
                nonlocal visited
                with os.scandir(path) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > 5000:
                            raise ValueError('汇报目录包含过多项目，请先整理（最多5000个目录项）')
                        name = entry.name
                        # Never inspect private names, including the internal marker.
                        if name.startswith('.') or _secret(name):
                            continue
                        _component(name)
                        relative_parts = parts + (name,)
                        if len(relative_parts) > 32:
                            raise ValueError('汇报资料目录层级超过32层')
                        info = entry.stat(follow_symlinks=False)
                        if _is_link(info):
                            raise ValueError('汇报资料目录中含有链接，请移除后再共享')
                        if stat.S_ISDIR(info.st_mode):
                            _chain(Path(entry.path))
                            walk(Path(entry.path), relative_parts)
                        elif stat.S_ISREG(info.st_mode):
                            if len(result) >= MAX_FILES:
                                raise ValueError('汇报资料超过500份，请在个人端整理后重新读取')
                            result.append(self._metadata(folder, relative_parts, descriptor))
                        else:
                            raise ValueError('汇报目录中含有非普通文件，不能共享')
            walk(folder)
        return sorted(result, key=lambda row: row['path'].casefold())

    def read(self, task_id, relative_path):
        parts = _relative(relative_path)
        if Path(parts[-1]).suffix.casefold() not in TEXT_SUFFIXES:
            raise ValueError('当前仅在线预览 UTF-8 的 txt、md、csv 和 json 资料，其他格式只列清单')
        with self._authorized_folder(task_id) as (folder, descriptor):
            with _file(folder, parts, descriptor) as (stream, info):
                if info.st_size > MAX_TEXT_BYTES:
                    raise ValueError('文本汇报资料超过2MiB，不能在线预览')
                content = stream.read(MAX_TEXT_BYTES + 1)
                if len(content) > MAX_TEXT_BYTES or len(content) != info.st_size:
                    raise ValueError('汇报资料在读取期间变化，请重新读取')
                if b'\x00' in content:
                    raise ValueError('资料含二进制内容，不能作为文本预览')
                try:
                    text = content.decode('utf-8-sig')
                except UnicodeDecodeError as exc:
                    raise ValueError('资料不是 UTF-8 文本，不能在线预览') from exc
                return {'path': '/'.join(parts), 'size': info.st_size,
                        'digest': hashlib.sha256(content).hexdigest(), 'previewable': True,
                        'modified': _modified(info), 'content': content, 'text': text}


def _existing_marker(folder, descriptor, task_id, issue_id, employee_id):
    """Return False only for an absent marker, never for an invalid one."""
    try:
        if descriptor is not None:
            os.stat(MARKER, dir_fd=descriptor, follow_symlinks=False)
        else:
            (folder / MARKER).lstat()
    except FileNotFoundError:
        return False
    with _file(folder, (MARKER,), descriptor) as (stream, info):
        if os.name != 'nt' and stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('现有汇报目录标识权限须为600，请手动核对原资料')
        if info.st_size > 16 * 1024:
            raise ValueError('现有汇报目录标识过大，未覆盖原文件')
        try:
            marker = json.loads(stream.read(16 * 1024 + 1).decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('现有汇报目录标识损坏，未覆盖原文件') from exc
        if (not isinstance(marker, dict) or marker.get('task_id') != task_id or
                marker.get('issue_id') != issue_id or marker.get('employee_id') != employee_id):
            raise ValueError('汇报目录已绑定其他任务、issue或员工，请手动整理原资料；未修改原关联')
    return True


def create_report_folder(root, task, employee_id):
    """Explicitly create a task directory under an existing, link-free root.

    Existing bindings are immutable. Repeated requests for the same three IDs
    return the existing directory without rewriting its marker or report files.
    POSIX opens and writes relative to verified directory descriptors; Windows
    checks every ancestor and directory identity before and after each mutation.
    """
    root = Path(root).expanduser()
    if not root.is_absolute() or not valid_uuid(employee_id) or not isinstance(task, dict):
        raise ValueError('汇报目录需已有的绝对根目录、有效工作任务和员工 UUID')
    task_id = task.get('id')
    reader = ReportFolders(root, lambda: [task])
    issue_id = reader._task(task_id)
    folder = root / task_id
    payload = json.dumps({'task_id': task_id, 'issue_id': issue_id, 'employee_id': employee_id},
                         ensure_ascii=False, sort_keys=True).encode('utf-8')

    def current_binding():
        if reader._task(task_id) != issue_id:
            raise ValueError('创建期间任务或 issue 关联变化，未改变原资料绑定')

    try:
        with _directory(root) as (root_descriptor, root_chain):
            current_binding()
            _unchanged_chain(root_chain)
            try:
                if root_descriptor is not None:
                    os.mkdir(task_id, mode=0o700, dir_fd=root_descriptor)
                    os.fsync(root_descriptor)
                else:
                    folder.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _unchanged_chain(root_chain)
            current_binding()
            with _directory(folder) as (descriptor, chain):
                if _existing_marker(folder, descriptor, task_id, issue_id, employee_id):
                    current_binding()
                    return folder
                temporary = '.orbit-report.' + uuid.uuid4().hex + '.tmp'
                temporary_path = folder / temporary
                temporary_info = None
                file_descriptor = None
                try:
                    _unchanged_chain(chain)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                    if descriptor is not None:
                        file_descriptor = os.open(temporary, flags, 0o600, dir_fd=descriptor)
                    else:
                        file_descriptor = os.open(temporary_path, flags, 0o600)
                    temporary_info = os.fstat(file_descriptor)
                    if _is_link(temporary_info) or not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1:
                        raise ValueError('汇报目录临时标识必须是独立普通文件')
                    _unchanged_chain(chain)
                    opened_path = (os.stat(temporary, dir_fd=descriptor, follow_symlinks=False)
                                   if descriptor is not None else temporary_path.lstat())
                    if _is_link(opened_path) or _identity(opened_path) != _identity(temporary_info):
                        raise ValueError('汇报目录临时标识在打开期间变化，未写入绑定')
                    current_binding()
                    if os.name != 'nt':
                        os.fchmod(file_descriptor, 0o600)
                    with os.fdopen(file_descriptor, 'wb') as stream:
                        file_descriptor = None
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                        temporary_info = os.fstat(stream.fileno())
                    _unchanged_chain(chain)
                    current_binding()
                    if _existing_marker(folder, descriptor, task_id, issue_id, employee_id):
                        return folder
                    actual = (os.stat(temporary, dir_fd=descriptor, follow_symlinks=False)
                              if descriptor is not None else temporary_path.lstat())
                    if _is_link(actual) or actual.st_nlink != 1 or _version(actual) != _version(temporary_info):
                        raise ValueError('汇报目录临时标识在写入期间变化，未覆盖原文件')
                    _unchanged_chain(chain)
                    if descriptor is not None:
                        os.replace(temporary, MARKER, src_dir_fd=descriptor, dst_dir_fd=descriptor)
                        os.fsync(descriptor)
                    else:
                        os.replace(temporary_path, folder / MARKER)
                    _unchanged_chain(chain)
                    current_binding()
                    _existing_marker(folder, descriptor, task_id, issue_id, employee_id)
                finally:
                    if file_descriptor is not None:
                        os.close(file_descriptor)
                    # Remove only our own file. Never follow or delete a
                    # pre-existing/randomly swapped temporary link or file.
                    if temporary_info is not None:
                        try:
                            actual = (os.stat(temporary, dir_fd=descriptor, follow_symlinks=False)
                                      if descriptor is not None else temporary_path.lstat())
                            if not _is_link(actual) and _identity(actual) == _identity(temporary_info):
                                if descriptor is not None:
                                    os.unlink(temporary, dir_fd=descriptor)
                                else:
                                    temporary_path.unlink()
                        except FileNotFoundError:
                            pass
            _unchanged_chain(root_chain)
            current_binding()
        return folder
    except OSError as exc:
        raise ValueError('汇报目录无法安全创建，原资料未被覆盖；请检查目录和链接') from exc
