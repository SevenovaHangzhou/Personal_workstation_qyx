"""Opt-in, process-independent LAN journal node using the evaluator wire protocol."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from evaluation.mesh import ShareServer, Discovery, PROTOCOL
from evaluation.protocol import valid_uuid
from evaluation.report_archive import write_json
from evaluation.shared_store import FileTransport, SharedStore
from platform_support import lock_file, process_options
from .journals import JournalLibrary, export_local


class LanConfig:
    def __init__(self, directory):
        self.root = Path(directory) / 'lan'
        self.path = self.root / 'config.json'

    def read(self):
        if not self.path.exists():
            return dict(version=1, employee_id='', name='', department='', enabled=False, task_ids=[], port=0)
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if (type(value) is not dict or set(value) != {'version', 'employee_id', 'name', 'department', 'enabled', 'task_ids', 'port'} or
                value['version'] != 1 or not valid_uuid(value['employee_id']) or type(value['enabled']) is not bool or
                not isinstance(value['name'], str) or not 1 <= len(value['name']) <= 80 or
                any(ord(c) < 32 for c in value['name']) or not isinstance(value['department'], str) or len(value['department']) > 160 or
                type(value['task_ids']) is not list or len(value['task_ids']) > 1000 or not all(valid_uuid(v) for v in value['task_ids']) or
                type(value['port']) is not int or not 0 <= value['port'] <= 65535):
            raise ValueError('局域网设置无效，未启动共享')
        return value

    def save(self, orbit, name, department, enabled, task_ids):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError('请填写 1 至 80 个字符的显示名')
        if not isinstance(department, str) or len(department) > 160 or type(enabled) is not bool:
            raise ValueError('共享设置无效')
        with orbit.db() as db:
            allowed = {row[0] for row in db.execute('SELECT id FROM tasks')}
        if not isinstance(task_ids, list) or set(task_ids) - allowed:
            raise ValueError('只能选择本机已领取的工作任务')
        value = self.read()
        value.update(employee_id=value['employee_id'] or str(uuid.uuid4()), name=name.strip(), department=department.strip(),
                     enabled=enabled, task_ids=sorted(set(task_ids)))
        write_json(self.path, value)
        return value

    def runtime(self):
        try:
            value = json.loads((self.root / 'runtime.json').read_text(encoding='utf-8'))
            if time.time() - value['heartbeat'] < 6:
                return value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return dict(state='stopped', peers=[])


def share_server(orbit, config):
    value = config.read()
    library = JournalLibrary(orbit, config.read)
    request_state = threading.local()

    class LiveGrants(ShareServer):
        def _authorized(self, token, scope=None):
            # UI and daemon are separate processes. Re-read revocations for every request/chunk.
            try:
                with self.lock:
                    current = config.read()
                    if not hasattr(request_state, 'config'):
                        request_state.config = current
                    if current != request_state.config:
                        return False
                    state = json.loads(self.state_path.read_text(encoding='utf-8'))
                    if state.get('employee_id') != self.employee_id:
                        return False
                    self.state = state
                    return super()._authorized(token, scope)
            except (OSError, ValueError, KeyError, TypeError):
                return False

    return LiveGrants(config.root / 'share', value['employee_id'], value['name'], library.records,
                      reports_callback=library.reports, shared_callback=library.shared,
                      authorized_callback=lambda: config.read()['enabled'])


def start_node(orbit):
    config = LanConfig(orbit.directory)
    if not config.read()['enabled']:
        return dict(state='disabled')
    current = config.runtime()
    if current['state'] == 'running':
        return dict(state='running')
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / 'scripts/mcp.py'), '--lan-serve', '--data-dir', str(orbit.directory)]
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **process_options())
    return dict(state='starting')


def run_node(orbit):
    config = LanConfig(orbit.directory)
    config.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    guard = (config.root / 'node.lock').open('a+')
    try:
        lock_file(guard)
    except OSError:
        guard.close()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    server = discovery = None
    active, metadata = None, {}
    try:
        export_local(orbit)
        while not stop.is_set():
            value = config.read()
            if not value['enabled']:
                break
            key = (value['employee_id'], value['name'], value['port'])
            if key != active:
                if discovery:
                    discovery.stop()
                if server:
                    server.stop()
                server = share_server(orbit, config)
                port = value['port']
                if not port:
                    try:
                        prior = json.loads((config.root / 'runtime.json').read_text(encoding='utf-8'))
                        if prior.get('employee_id') == value['employee_id']:
                            port = prior.get('port', 0)
                    except (OSError, ValueError):
                        pass
                metadata = server.start(port=port)
                # Remember the TCP port in daemon metadata, not in user preferences.
                discovery = Discovery(value['employee_id'], value['name'], metadata['port'], metadata['fingerprint'])
                discovery.start()
                active = key
            write_json(config.root / 'runtime.json', dict(state='running', heartbeat=time.time(), **metadata,
                       employee_id=value['employee_id'], peers=discovery.snapshot(), notice=discovery.error))
            stop.wait(1)
        return 0
    except Exception:
        write_json(config.root / 'runtime.json', dict(state='error', heartbeat=time.time(), peers=[],
                   notice='共享启动失败，请检查依赖、端口和目录权限；原有数据保留。'))
        return 2
    finally:
        if discovery:
            discovery.stop()
        if server:
            server.stop()
        try:
            if config.runtime()['state'] != 'error':
                write_json(config.root / 'runtime.json', dict(state='stopped', heartbeat=time.time(), peers=[], **metadata, employee_id=config.read()['employee_id']))
        finally:
            guard.close()


class Contacts:
    """Invitations contain bearer credentials. They are never returned by MCP tools."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'lan/contacts.json'

    def private(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def summary(self):
        return [dict(id=key, name=value['name'], employee_id=value['employee_id'], url=value['url'])
                for key, value in self.private().items()]

    def transport(self, contact_id):
        value = self.private().get(contact_id)
        if not value:
            raise ValueError('未找到已授权的员工，请在本机卡片导入邀请')
        return FileTransport(value['url'], value['fingerprint'], value['token'], value['employee_id'])

    def add(self, invitation, url):
        value = json.loads(invitation)
        if (type(value) is not dict or value.get('protocol') != PROTOCOL or not valid_uuid(value.get('employee_id')) or
                not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 80 or
                not {'journals', 'full_reports', 'shared_store'} <= set(value.get('capabilities', []))):
            raise ValueError('需要该员工为 Shared Store 创建的有效邀请')
        transport = FileTransport(url, value['fingerprint'], value['token'], value['employee_id'])
        transport.shared_manifest()  # Pin + permission + identity readback before storing.
        values = self.private()
        employee = value['employee_id']
        # Do not silently replace a paired device key.
        if employee in values and values[employee]['fingerprint'] != value['fingerprint']:
            raise ValueError('设备证书已变化，请先移除原联系人并重新核对')
        values[employee] = {key: value[key] for key in ('name', 'employee_id', 'fingerprint', 'token')}
        values[employee]['url'] = url
        write_json(self.path, values)
        return dict(name=value['name'], employee_id=employee)

    def remove(self, employee):
        values = self.private()
        values.pop(employee, None)
        write_json(self.path, values)


def invitation(orbit, label):
    config = LanConfig(orbit.directory)
    state, runtime = config.read(), config.runtime()
    if not state['enabled'] or runtime['state'] != 'running':
        raise ValueError('请先启用共享，等待 TCP 服务就绪')
    grant = share_server(orbit, config).grant(label, reports=True)
    return dict(protocol=PROTOCOL, employee_id=state['employee_id'], name=state['name'], department=state['department'],
                port=runtime['port'], fingerprint=runtime['fingerprint'], token=grant['token'], grant_id=grant['id'],
                capabilities=['journals', 'full_reports', 'shared_store'])


def status(orbit):
    config = LanConfig(orbit.directory)
    settings = config.read()
    contacts = Contacts(orbit.directory).summary()
    store = SharedStore(orbit.directory / 'shared-store')
    for contact in contacts:
        contact['snapshot'] = store.current(contact['employee_id'])
    return dict(enabled=settings['enabled'], name=settings['name'], task_ids=settings['task_ids'],
                runtime=config.runtime(), contacts=contacts,
                journal_directory=str(orbit.directory / 'journals'), shared_store_directory=str(orbit.directory / 'shared-store'))


def collect(orbit, contact_id, cancel):
    contacts = Contacts(orbit.directory)
    transport = contacts.transport(contact_id)
    store = SharedStore(orbit.directory / 'shared-store')
    value = store.pull(transport.employee_id, transport, cancel)
    return dict(employee_id=transport.employee_id, version=value['manifest']['version'], collected_at=value['collected_at'],
                files=len(value['manifest']['files']), transferred_bytes=value['transferred_bytes'], reused_files=value['reused_files'])


def open_card(orbit):
    from .settings import desktop_environment
    env = desktop_environment()
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/mcp.py'), '--lan', '--data-dir', str(orbit.directory)]
    if env.get('QT_QPA_PLATFORM') in ('offscreen', 'minimal') or (sys.platform.startswith('linux') and not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'))):
        return dict(state='ui_unavailable', local_command=command)
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=env, close_fds=True, **process_options())
    return dict(state='opening', message='已打开本机局域网日志卡片，邀请只在该卡片输入和显示。')
