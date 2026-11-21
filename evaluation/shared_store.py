"""Content-verified, immutable LAN file snapshots; no arbitrary directory sharing."""
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import threading
import uuid

from evaluation.mesh import PROTOCOL, TIMEOUT, PermissionDenied, Transport, _json_bytes
from evaluation.report_archive import safe_file, write_json
from platform_support import lock_file

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 3000
FORMAT = 'orbit-shared-store/v1'


def shared_path(name):
    if name != '日志统计标准.md' and not (isinstance(name, str) and re.fullmatch(
            r'(?:日志/\d{4}-\d{2}-\d{2}|日报/\d{4}-\d{2}-\d{2}-\d{4})/[0-9a-f-]{36}\.(md|json)', name)):
        raise ValueError('仅允许共享工作日志和日志统计标准')
    return name


def digest(files):
    return hashlib.sha256(_json_bytes(files)).hexdigest()


def validate_manifest(value):
    if type(value) is not dict or set(value) != {'format', 'version', 'files'} or value['format'] != FORMAT:
        raise ValueError('Shared Store 清单格式无效')
    files = value['files']
    if type(files) is not list or len(files) > MAX_FILES:
        raise ValueError('共享文件数量超过限制')
    seen, total = set(), 0
    for row in files:
        if type(row) is not dict or set(row) != {'path', 'bytes', 'sha256'}:
            raise ValueError('共享文件元数据无效')
        shared_path(row['path'])
        if row['path'] in seen or type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_FILE:
            raise ValueError('共享文件重复或大小无效')
        if not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError('共享文件摘要无效')
        total += row['bytes']
        seen.add(row['path'])
    if total > MAX_TOTAL or files != sorted(files, key=lambda row: row['path']) or value['version'] != digest(files):
        raise ValueError('共享清单大小、顺序或版本校验无效')
    return value


def file_manifest(files):
    rows = [dict(path=shared_path(name), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            for name, raw in sorted(files.items())]
    return validate_manifest(dict(format=FORMAT, version=digest(rows), files=rows))


class FileTransport(Transport):
    def shared_manifest(self):
        value = self._request('shared_manifest', {}, 2_000_000)
        if set(value) != {'employee_id', 'result'}:
            raise ValueError('共享清单身份无效')
        return validate_manifest(value['result'])

    def download(self, version, row, stream, cancel):
        """Direct TLS/TCP bytes. Validate peer before sending the per-admin token."""
        shared_path(row['path'])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = http.client.HTTPSConnection(self._address(), self.port, timeout=TIMEOUT, context=context)
        response = None
        try:
            if cancel.is_set():
                raise InterruptedError('收集已取消')
            connection.connect()
            actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual, self.fingerprint):
                raise ValueError('证书指纹不符，未发送授权')
            connection.request('POST', '/v1/shared_file', _json_bytes(dict(version=version, path=row['path'])), {
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token, 'X-Orbit-Protocol': PROTOCOL})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise PermissionDenied('共享已关闭或授权已撤销')
            if (response.status != 200 or response.getheader('X-Orbit-Protocol') != PROTOCOL or
                    response.getheader('X-Orbit-Employee') != self.employee_id or
                    response.getheader('Content-Type') != 'application/octet-stream' or
                    response.getheader('Content-Length') != str(row['bytes']) or
                    response.getheader('Transfer-Encoding') or response.getheader('Content-Encoding')):
                raise ValueError('共享文件响应或版本无效，请刷新后重试')
            checksum, count = hashlib.sha256(), 0
            while count < row['bytes']:
                if cancel.is_set():
                    raise InterruptedError('收集已取消')
                raw = response.read(min(64 * 1024, row['bytes'] - count))
                if not raw:
                    raise ValueError('文件传输中断，未启用不完整快照')
                stream.write(raw)
                checksum.update(raw)
                count += len(raw)
            if checksum.hexdigest() != row['sha256']:
                raise ValueError('文件摘要不符，未启用不完整快照')
        finally:
            if response:
                response.close()
            connection.close()


class SharedStore:
    """One atomic pointer per device, with retained immutable offline snapshots."""
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()

    def _employee(self, employee):
        if str(uuid.UUID(employee)) != employee:
            raise ValueError('员工设备标识无效')
        root = safe_file(self.root, employee)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    def current(self, employee):
        root = self._employee(employee)
        path = safe_file(root, 'current.json')
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        validate_manifest(value['manifest'])
        return value

    def pull(self, employee, transport, cancel):
        with self.lock:
            root = self._employee(employee)
            with safe_file(root, 'sync.lock').open('a+') as guard:
                while True:
                    if cancel.is_set():
                        raise InterruptedError('收集已取消')
                    try:
                        lock_file(guard)
                        break
                    except OSError:
                        cancel.wait(.1)
                return self._pull(employee, transport, cancel)

    def _pull(self, employee, transport, cancel):
        root = self._employee(employee)
        manifest = validate_manifest(transport.shared_manifest())
        old = self.current(employee)
        target = safe_file(root, manifest['version'])
        pending = root / ('.pending-' + uuid.uuid4().hex)
        pending.mkdir(mode=0o700)
        transferred = reused = 0
        try:
            for row in manifest['files']:
                if cancel.is_set():
                    raise InterruptedError('收集已取消')
                path = safe_file(pending, row['path'])
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                previous = next((r for r in old['manifest']['files'] if r == row), None) if old else None
                cached = safe_file(root / old['manifest']['version'], row['path']) if previous else None
                if cached and cached.is_file() and cached.stat().st_size == row['bytes'] and hashlib.sha256(cached.read_bytes()).hexdigest() == row['sha256']:
                    shutil.copyfile(cached, path)
                    reused += 1
                else:
                    with path.open('xb') as stream:
                        transport.download(manifest['version'], row, stream, cancel)
                        stream.flush()
                        os.fsync(stream.fileno())
                    transferred += row['bytes']
                if path.stat().st_size != row['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('本地文件校验失败')
                path.read_text(encoding='utf-8')
                path.chmod(0o600)
            # Re-authorize even if every file was reused, and reject a changing library.
            if cancel.is_set() or transport.shared_manifest() != manifest:
                raise InterruptedError('共享清单已变化，保留上次成功快照')
            value = dict(manifest=manifest, collected_at=datetime.now(timezone.utc).isoformat())
            write_json(pending / 'manifest.json', manifest)
            if not target.exists():
                pending.rename(target)
            else:
                # Existing immutable versions must be verified, never trusted by name.
                for row in manifest['files']:
                    path = safe_file(target, row['path'])
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                        raise ValueError('已有快照被修改，请先移走损坏版本再同步')
            write_json(root / 'current.json', value)
            return dict(value, transferred_bytes=transferred, reused_files=reused)
        finally:
            shutil.rmtree(pending, ignore_errors=True)

    def read(self, employee, name):
        shared_path(name)
        with self.lock:
            value = self.current(employee)
            if not value:
                raise ValueError('尚无成功同步的离线快照')
            row = next((r for r in value['manifest']['files'] if r['path'] == name), None)
            if not row:
                raise ValueError('文件不在当前快照中')
            path = safe_file(self.root / employee / value['manifest']['version'], name)
            if not path.is_file() or path.stat().st_size != row['bytes']:
                raise ValueError('快照文件大小不符')
            raw = path.read_bytes()
            if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('快照文件校验失败')
            return raw.decode('utf-8')
