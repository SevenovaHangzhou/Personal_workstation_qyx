"""Opt-in LAN presence and narrowly scoped, certificate-pinned journal sharing.

Discovery is not authorization. A separate revocable token is issued to every
reviewer, and the HTTP surface cannot enumerate directories or execute commands.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import binascii
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
import uuid
from evaluation.protocol import PROTOCOL, DISCOVERY_PORT, MAX_BATCH, validate_record

MAX_RECORDS = MAX_BATCH
MAX_RESPONSE = 2_000_000
MAX_MANIFEST = 2_000_000
MAX_REQUEST = 4096
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENT_METADATA_BYTES = 100 * 1024 * 1024
MAX_ATTACHMENT_RESPONSE = 3 * 1024 * 1024
MAX_ATTACHMENTS = 500
PREVIEW_SUFFIXES = {'.txt', '.md', '.csv', '.json'}
TIMEOUT = 5
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{32,160}\Z')
_PRIVATE = tuple(ipaddress.ip_network(net) for net in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    '127.0.0.0/8', 'fc00::/7', 'fe80::/10', '::1/128'))


class PermissionDenied(PermissionError):
    """The employee explicitly refused access, rather than being offline."""


def _task_uuid(value):
    if not isinstance(value, str):
        raise ValueError('汇报任务标识无效')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('汇报任务标识无效') from None
    return value


def _attachment_path(value):
    if not isinstance(value, str) or not value or len(value) > 1000 or '\\' in value or ':' in value:
        raise ValueError('汇报资料相对路径无效')
    segments = value.split('/')
    sensitive = {'auth.json', 'credentials.json', 'credentials', 'secrets.json', 'id_rsa', 'id_ed25519'}
    reserved = {'con', 'prn', 'aux', 'nul'} | {'com' + str(i) for i in range(1, 10)} | {'lpt' + str(i) for i in range(1, 10)}
    for segment in segments:
        name = segment.lower()
        if (not segment or len(segment) > 200 or segment.startswith('.') or segment.endswith((' ', '.')) or
                any(ord(c) < 32 or ord(c) == 127 for c in segment) or name in sensitive or
                name.split('.')[0] in reserved or Path(name).suffix in {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.db'}):
            raise ValueError('隐藏文件、凭据或越界路径不能共享')
    return value


def _attachment_metadata(value):
    required = {'path', 'size', 'digest', 'previewable'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'modified'}:
        raise ValueError('汇报资料清单格式无效')
    path = _attachment_path(value['path'])
    size, digest, previewable = value['size'], value['digest'], value['previewable']
    if type(size) is not int or not 0 <= size <= MAX_ATTACHMENT_METADATA_BYTES or type(previewable) is not bool:
        raise ValueError('汇报资料大小或格式无效')
    if not isinstance(digest, str) or (not _HASH.fullmatch(digest) and not (not previewable and digest == '')):
        raise ValueError('汇报资料内容摘要无效')
    if previewable and (size > MAX_ATTACHMENT_BYTES or Path(path).suffix.lower() not in PREVIEW_SUFFIXES):
        raise ValueError('该资料格式或大小不支持在线预览')
    result = {'path': path, 'size': size, 'digest': digest, 'previewable': previewable}
    if 'modified' in value:
        if not isinstance(value['modified'], str) or len(value['modified']) > 80:
            raise ValueError('汇报资料修改时间无效')
        result['modified'] = value['modified']
    return result


def _attachment_content(content, metadata):
    if not isinstance(content, bytes) or len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError('汇报资料超过在线预览大小限制')
    if len(content) != metadata['size'] or hashlib.sha256(content).hexdigest() != metadata['digest']:
        raise ValueError('汇报资料在读取期间变化，请刷新清单')
    text = content.decode('utf-8-sig')
    if '\x00' in text:
        raise ValueError('该资料不是可预览的纯文本')
    return content


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('标识无效')
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise ValueError('名称应为 1 至 80 个可见字符')
    return value.strip()


def _fingerprint(value):
    if not isinstance(value, str):
        raise ValueError('证书指纹无效')
    value = value.replace(':', '').strip().lower()
    if not _HASH.fullmatch(value):
        raise ValueError('请输入 64 位 SHA256 证书指纹')
    return value


def _local_address(value):
    try:
        address = ipaddress.ip_address(value)
        return any(address.version == net.version and address in net for net in _PRIVATE)
    except ValueError:
        return False


def endpoint(value):
    if not isinstance(value, str) or len(value) > 400 or any(c.isspace() for c in value) or '\\' in value:
        raise ValueError('请输入局域网 HTTPS 地址')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or '@' in parsed.netloc or
                parsed.query or parsed.fragment or parsed.path not in ('', '/') or '%' in parsed.hostname):
            raise ValueError()
        host = parsed.hostname
        port = parsed.port or 443
        if not 1 <= port <= 65535:
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host) or re.fullmatch(r'[0-9.]+', host):
                raise ValueError()
        else:
            if not _local_address(host):
                raise ValueError()
        return host, port
    except ValueError:
        raise ValueError('仅支持局域网 HTTPS 地址和有效端口，不包含路径、账号或参数') from None


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('JSON 含重复字段')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('JSON 数值无效')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _private_write(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass  # Windows uses the user's profile ACL rather than POSIX modes.
    finally:
        temporary.unlink(missing_ok=True)


class _JournalHTTP(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, owner, context):
        self.owner = owner
        self.context = context
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, _Handler)

    def process_request(self, request, client_address):
        if not _local_address(client_address[0]) or not self.slots.acquire(False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(TIMEOUT)
            request = self.context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
            request.do_handshake()
            super().process_request_thread(request, client_address)
        except (OSError, ssl.SSLError):
            request.close()
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Do not log request bodies, credentials, journals, or tracebacks.


class _Handler(BaseHTTPRequestHandler):
    server_version = 'OrbitJournal/1'
    sys_version = ''

    def log_message(self, *args):
        pass

    def reply(self, status, value):
        raw = _json_bytes(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Orbit-Protocol', PROTOCOL)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass
        self.close_connection = True

    def do_GET(self):
        self.reply(405, {'error': '只接受只读日志查询'})

    def do_POST(self):
        try:
            if self.path not in ('/v1/manifest', '/v1/record', '/v1/attachments', '/v1/attachment', '/v1/report_manifest', '/v1/report'):
                self.reply(404, {'error': '未知只读接口'})
                return
            if self.headers.get_all('X-Orbit-Protocol') != [PROTOCOL]:
                raise ValueError('日志协议版本不符')
            auth = self.headers.get_all('Authorization', [])
            scope = 'full_reports' if self.path in ('/v1/report_manifest', '/v1/report') else None
            if len(auth) != 1 or not auth[0].startswith('Bearer ') or not self.server.owner._authorized(auth[0][7:], scope):
                raise PermissionError('日志共享未授权或已撤销')
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
                raise ValueError('不接受压缩或分块请求')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_REQUEST:
                raise ValueError('请求长度无效')
            if self.headers.get_all('Content-Type') != ['application/json']:
                raise ValueError('请求应为 JSON')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise ValueError('请求未完整接收')
            payload = _json(raw)
            if not isinstance(payload, dict):
                raise ValueError('请求格式无效')
            # Recheck after body reception so a token revoked during a slow
            # request cannot be used to start reading a journal.
            if not self.server.owner._authorized(auth[0][7:], scope):
                raise PermissionError('日志共享授权已撤销')
            result = self.server.owner._read(self.path, payload)
            limit = (MAX_ATTACHMENT_RESPONSE if self.path == '/v1/attachment' else
                     MAX_MANIFEST if self.path.endswith(('manifest', 'attachments')) else MAX_RESPONSE)
            if len(_json_bytes(result)) > limit:
                raise ValueError('授权日志内容过大，请缩小共享范围')
            if not self.server.owner._authorized(auth[0][7:], scope):
                raise PermissionError('日志共享授权已撤销')
            self.reply(200, result)
        except PermissionError as exc:
            self.reply(403, {'error': str(exc)})
        except (ValueError, TypeError, KeyError, UnicodeError):
            self.reply(400, {'error': '日志请求无效、版本已变化或记录不可用，请刷新清单'})
        except Exception:
            self.reply(503, {'error': '日志共享暂不可用，请稍后重试'})


class ShareServer:
    """A stopped-by-default, authenticated read view over frozen authorized rows."""
    def __init__(self, folder, employee_id, name, records_callback, attachments_callback=None, reports_callback=None):
        self.folder = Path(folder)
        self.employee_id = _identifier(employee_id)
        self.name = _name(name)
        if not callable(records_callback):
            raise TypeError('records_callback 必须可调用')
        self.records_callback = records_callback
        if attachments_callback is not None and not callable(attachments_callback):
            raise TypeError('attachments_callback 必须可调用')
        self.attachments_callback = attachments_callback
        if reports_callback is not None and not callable(reports_callback):
            raise TypeError('reports_callback 必须可调用')
        self.reports_callback = reports_callback
        self.lock = threading.RLock()
        self.server = None
        self.thread = None
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            self.folder.chmod(0o700)
        except OSError:
            pass
        self.state_path = self.folder / 'grants.json'
        if self.state_path.exists():
            state = _json(self.state_path.read_bytes())
            if state.get('employee_id') != self.employee_id or not isinstance(state.get('grants'), list):
                raise ValueError('共享凭据不属于此员工，不能复用其他员工的目录')
            self.state = state
        else:
            self.state = {'employee_id': self.employee_id, 'grants': []}
            self._save()

    def _save(self):
        _private_write(self.state_path, _json_bytes(self.state))

    def grant(self, label, reports=False):
        label = _name(label)
        if type(reports) is not bool:
            raise ValueError('完整报告授权格式无效')
        with self.lock:
            if len(self.state['grants']) >= 256:
                raise ValueError('授权数量已达上限，请先移除旧共享身份')
            token = secrets.token_urlsafe(32)
            row = {'id': str(uuid.uuid4()), 'label': label,
                   'token_hash': hashlib.sha256(token.encode()).hexdigest(),
                   'created': datetime.now(timezone.utc).isoformat(), 'revoked': False,
                   'full_reports': reports}
            self.state['grants'].append(row)
            self._save()
            return {'id': row['id'], 'label': label, 'token': token}

    def grants(self):
        with self.lock:
            return [{k: v for k, v in row.items() if k != 'token_hash'} for row in self.state['grants']]

    def revoke(self, grant_id):
        with self.lock:
            row = next((row for row in self.state['grants'] if row['id'] == grant_id), None)
            if row is None:
                raise ValueError('共享授权不存在')
            row['revoked'] = True
            self._save()

    def _authorized(self, token, scope=None):
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            return any(not row.get('revoked') and (scope is None or row.get(scope) is True) and hmac.compare_digest(row['token_hash'], digest)
                       for row in self.state['grants'])

    def _records(self):
        rows = self.records_callback()
        if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
            raise ValueError('共享清单过大或格式无效')
        records = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('共享日志格式无效')
            key = _identifier(row.get('id'))
            digest = row.get('digest')
            if not isinstance(digest, str) or not _HASH.fullmatch(digest) or key in records:
                raise ValueError('共享日志摘要无效或标识重复')
            if row.get('employee_id') != self.employee_id:
                raise ValueError('共享日志员工不符')
            records[key] = row
        return records

    def _read(self, path, payload):
        if path in ('/v1/report_manifest', '/v1/report'):
            if not self.reports_callback:
                raise PermissionDenied('此员工端未提供完整报告共享')
            result = self.reports_callback(path.removeprefix('/v1/'), payload)
            return dict(employee_id=self.employee_id, result=result)
        if path in ('/v1/attachments', '/v1/attachment'):
            return self._read_attachments(path, payload)
        records = self._records()
        if path == '/v1/manifest':
            if payload:
                raise ValueError('清单请求不接受其他字段')
            return {'employee_id': self.employee_id, 'name': self.name,
                    'records': [{'id': key, 'digest': row['digest']} for key, row in sorted(records.items())]}
        if set(payload) != {'id', 'digest'}:
            raise ValueError('记录请求仅接受标识与摘要')
        key = _identifier(payload['id'])
        row = records.get(key)
        if row is None or row['digest'] != payload['digest']:
            raise ValueError('日志已撤回或修改，请刷新清单')
        row = _json(_json_bytes(row))  # Freeze before validation and transmission.
        validate_record(row)
        return {'employee_id': self.employee_id, 'record': row}

    def _read_attachments(self, path, payload):
        if not self.attachments_callback:
            raise PermissionDenied('此员工未启用汇报资料共享')
        expected = {'task_id'} if path == '/v1/attachments' else {'task_id', 'path'}
        if set(payload) != expected:
            raise ValueError('汇报资料请求字段无效')
        task = _task_uuid(payload['task_id'])
        # The callback must independently verify this task remains explicitly
        # shared. A task need not have generated a journal yet to have a report.
        rows = self.attachments_callback(task, None)
        if not isinstance(rows, list) or len(rows) > MAX_ATTACHMENTS:
            raise ValueError('汇报资料超过 500 项限制')
        files = {}
        for value in rows:
            metadata = _attachment_metadata(value)
            if metadata['path'] in files:
                raise ValueError('汇报资料相对路径重复')
            files[metadata['path']] = metadata
        if path == '/v1/attachments':
            return {'employee_id': self.employee_id, 'task_id': task,
                    'files': [files[key] for key in sorted(files)]}
        relative = _attachment_path(payload['path'])
        selected = files.get(relative)
        if selected is None or not selected['previewable']:
            raise ValueError('资料不在当前共享清单或仅支持查看元数据')
        value = self.attachments_callback(task, relative)
        if not isinstance(value, dict) or set(value) - {'path', 'size', 'digest', 'previewable', 'modified', 'content', 'text'}:
            raise ValueError('汇报资料读取格式无效')
        metadata = _attachment_metadata({key: item for key, item in value.items() if key not in {'content', 'text'}})
        if metadata != selected:
            raise ValueError('汇报资料在读取期间变化，请刷新清单')
        content = _attachment_content(value.get('content'), metadata)
        return {'employee_id': self.employee_id, 'task_id': task,
                'file': dict(metadata, content=base64.b64encode(content).decode('ascii'))}

    def _certificate(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        cert_path, key_path = self.folder / 'certificate.pem', self.folder / 'private-key.pem'
        if cert_path.exists() != key_path.exists():
            raise ValueError('共享证书文件不完整，请恢复原证书或重新核对配对')
        if not cert_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ORBIT Employee Journal')])
            now = datetime.now(timezone.utc)
            certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
            _private_write(key_path, key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            _private_write(cert_path, certificate.public_bytes(serialization.Encoding.PEM))
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        for path in (cert_path, key_path):
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return cert_path, key_path, certificate.fingerprint(hashes.SHA256()).hex()

    def start(self, host='0.0.0.0', port=0):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return {'port': self.server.server_port, 'fingerprint': self.fingerprint}
            if host != '0.0.0.0' and not _local_address(host):
                raise ValueError('日志服务只能监听本机或局域网地址')
            if type(port) is not int or not 0 <= port <= 65535:
                raise ValueError('端口无效')
            cert, key, self.fingerprint = self._certificate()
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, key)
            self.server = _JournalHTTP((host, port), self, context)
            self.thread = threading.Thread(target=self.server.serve_forever,
                kwargs={'poll_interval': .1}, name='orbit-journal-sharing', daemon=True)
            self.thread.start()
            return {'port': self.server.server_port, 'fingerprint': self.fingerprint}

    def stop(self):
        # Never hold the grants lock while draining requests; a handler may be
        # waiting to check revocation before returning a response.
        with self.lock:
            server, thread = self.server, self.thread
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(TIMEOUT + 1)
            if thread.is_alive():
                raise RuntimeError('日志共享服务未停止')
        with self.lock:
            self.server = self.thread = None


class Transport:
    """Pinned LAN TLS client; validates certificate before sending any token."""
    def __init__(self, url, fingerprint, token, employee_id):
        self.host, self.port = endpoint(url)
        self.fingerprint = _fingerprint(fingerprint)
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError('共享授权 token 无效')
        self.token = token
        self.employee_id = _identifier(employee_id)

    def _address(self):
        rows = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses or any(not _local_address(address) for address in addresses):
            raise ValueError('员工地址解析到非局域网地址，未发送凭据')
        return addresses[0]

    def _request(self, operation, payload, limit):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Connect to the validated numeric address so DNS cannot change between
        # validation and connection. http.client ignores HTTP proxy variables.
        connection = http.client.HTTPSConnection(self._address(), self.port, timeout=TIMEOUT, context=context)
        response = None
        try:
            connection.connect()
            actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual, self.fingerprint):
                raise ValueError('员工证书指纹不符，未发送凭据；请重新核对配对信息')
            connection.request('POST', '/v1/' + operation, _json_bytes(payload), {
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token,
                'X-Orbit-Protocol': PROTOCOL})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise PermissionDenied('员工日志共享未授权或已撤销')
            if response.getheader('X-Orbit-Protocol') != PROTOCOL:
                raise ValueError('员工日志服务协议不符')
            count = response.getheader('Content-Length')
            if count is not None and (not count.isdigit() or int(count) > limit):
                raise ValueError('员工日志响应过大或长度无效')
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('员工日志响应过大')
            value = _json(raw)
            if response.status != 200:
                raise ValueError('员工日志请求失败，记录可能已修订，请刷新后重试')
            if not isinstance(value, dict) or value.get('employee_id') != self.employee_id:
                raise ValueError('返回日志不属于已配对员工')
            return value
        finally:
            # For Connection: close, http.client transfers the socket's file
            # ownership to HTTPResponse and drops its connection reference.
            # Authentication/size failures may happen before read() reaches EOF,
            # so connection.close() alone cannot close that response stream.
            if response is not None:
                response.close()
            connection.close()

    def manifest(self):
        value = self._request('manifest', {}, MAX_MANIFEST)
        if set(value) != {'employee_id', 'name', 'records'} or not isinstance(value['records'], list) or len(value['records']) > MAX_RECORDS:
            raise ValueError('员工日志清单格式无效')
        _name(value['name'])
        seen = set()
        for row in value['records']:
            if not isinstance(row, dict) or set(row) != {'id', 'digest'}:
                raise ValueError('员工日志清单格式无效')
            key = _identifier(row['id'])
            if key in seen or not isinstance(row['digest'], str) or not _HASH.fullmatch(row['digest']):
                raise ValueError('员工日志清单存在重复或无效摘要')
            seen.add(key)
        return value

    def record(self, record_id, digest):
        record_id = _identifier(record_id)
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError('日志版本摘要无效')
        value = self._request('record', {'id': record_id, 'digest': digest}, MAX_RESPONSE)
        row = value.get('record')
        if (set(value) != {'employee_id', 'record'} or not isinstance(row, dict) or
                row.get('employee_id') != self.employee_id or row.get('id') != record_id or row.get('digest') != digest):
            raise ValueError('返回日志与请求的员工、记录或版本不符')
        return row

    def report_manifest(self):
        from evaluation.report_archive import manifest
        value = self._request('report_manifest', {}, MAX_MANIFEST)
        if set(value) != {'employee_id', 'result'}:
            raise ValueError('完整报告清单格式无效')
        return manifest(value['result'])

    def report(self, version, path, offset):
        from evaluation.report_archive import source_path
        source_path(path)
        if not isinstance(version, str) or not _HASH.fullmatch(version) or type(offset) is not int or offset < 0:
            raise ValueError('完整报告读取参数无效')
        value = self._request('report', dict(version=version, path=path, offset=offset), MAX_RESPONSE)
        result = value.get('result')
        if set(value) != {'employee_id', 'result'} or not isinstance(result, dict) or set(result) != {'data'} or not isinstance(result['data'], str):
            raise ValueError('完整报告内容格式无效')
        if len(result['data']) > (256 * 1024 + 2) // 3 * 4:
            raise ValueError('完整报告分块超过限制')
        return result

    def attachments(self, task_id):
        task_id = _task_uuid(task_id)
        value = self._request('attachments', {'task_id': task_id}, MAX_MANIFEST)
        if set(value) != {'employee_id', 'task_id', 'files'} or value['task_id'] != task_id:
            raise ValueError('返回资料不属于请求的任务')
        files = value['files']
        if not isinstance(files, list) or len(files) > MAX_ATTACHMENTS:
            raise ValueError('汇报资料清单超过限制')
        result, seen = [], set()
        for row in files:
            metadata = _attachment_metadata(row)
            if metadata['path'] in seen:
                raise ValueError('汇报资料相对路径重复')
            result.append(metadata)
            seen.add(metadata['path'])
        return result

    def attachment(self, task_id, path):
        task_id, path = _task_uuid(task_id), _attachment_path(path)
        value = self._request('attachment', {'task_id': task_id, 'path': path}, MAX_ATTACHMENT_RESPONSE)
        if set(value) != {'employee_id', 'task_id', 'file'} or value['task_id'] != task_id:
            raise ValueError('返回资料不属于请求的任务')
        row = value['file']
        if not isinstance(row, dict) or 'content' not in row or not isinstance(row['content'], str):
            raise ValueError('汇报资料内容格式无效')
        metadata = _attachment_metadata({key: item for key, item in row.items() if key != 'content'})
        if metadata['path'] != path or not metadata['previewable']:
            raise ValueError('返回资料与请求不符或不能在线预览')
        encoded = row['content']
        if len(encoded) > (MAX_ATTACHMENT_BYTES + 2) // 3 * 4:
            raise ValueError('汇报资料超过在线预览大小限制')
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('汇报资料编码无效') from None
        return dict(metadata, content=_attachment_content(content, metadata))


class Discovery:
    """Bounded LAN presence. port=0 listens without advertising an employee."""
    def __init__(self, employee_id, name, port, fingerprint):
        self.employee_id = _identifier(employee_id)
        self.name = _name(name)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('共享端口无效')
        self.port = port
        self.fingerprint = _fingerprint(fingerprint) if port else ''
        self.peers = {}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.socket = None
        self.error = ''

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self.error = ''
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(('', DISCOVERY_PORT))
            sock.settimeout(.3)
        except OSError:
            sock.close()
            self.error = '局域网自动发现不可用，可手动导入员工邀请'
            return
        self.socket = sock
        self.thread = threading.Thread(target=self._run, name='orbit-evaluator-discovery', daemon=True)
        self.thread.start()

    def _accept(self, raw, address):
        try:
            if len(raw) > 1024 or not _local_address(address):
                return
            value = _json(raw)
            if not isinstance(value, dict) or set(value) != {'protocol', 'employee_id', 'name', 'port', 'fingerprint'} or value['protocol'] != PROTOCOL:
                return
            employee_id = _identifier(value['employee_id'])
            name = _name(value['name'])
            fingerprint = _fingerprint(value['fingerprint'])
            port = value['port']
            if type(port) is not int or not 1 <= port <= 65535 or employee_id == self.employee_id:
                return
            with self.lock:
                key = (employee_id, fingerprint)
                now = time.monotonic()
                self.peers = {key: row for key, row in self.peers.items() if now - row['seen'] < 35}
                if key not in self.peers and len(self.peers) >= 256:
                    return
                self.peers[key] = {'employee_id': employee_id, 'name': name, 'address': address,
                    'port': port, 'fingerprint': fingerprint, 'seen': now}
        except (ValueError, TypeError, KeyError, UnicodeError):
            return

    def _run(self):
        next_send = 0
        while not self.stopping.is_set():
            if self.port and time.monotonic() >= next_send:
                packet = _json_bytes({'protocol': PROTOCOL, 'employee_id': self.employee_id,
                    'name': self.name, 'port': self.port, 'fingerprint': self.fingerprint})
                try:
                    self.socket.sendto(packet, ('255.255.255.255', DISCOVERY_PORT))
                except OSError:
                    self.error = '广播未送达，可手动导入员工邀请'
                next_send = time.monotonic() + 10
            try:
                raw, address = self.socket.recvfrom(1025)
                self._accept(raw, address[0])
            except (OSError, ValueError):
                continue

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            self.peers = {key: row for key, row in self.peers.items() if now - row['seen'] < 35}
            return [dict(row) for row in self.peers.values()]

    def stop(self):
        self.stopping.set()
        if self.socket:
            self.socket.close()
        if self.thread:
            self.thread.join(2)
            if self.thread.is_alive():
                raise RuntimeError('局域网发现线程未停止')
        self.socket = self.thread = None
