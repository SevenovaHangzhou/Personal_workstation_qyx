"""Device-local onboarding. Secrets stay between the native form and credential store."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from credentials import CredentialStore
from linear_support import LinearError, graphql
from .core import OrbitError, absolute_directory


class Settings:
    def __init__(self, directory, credential_directory=None, *, store=None, request=None):
        self.directory = Path(directory)
        self.credential_directory = Path(credential_directory or directory)
        self.store = store or CredentialStore
        self.request = request or graphql

    def credentials(self):
        value = os.environ.get('LINEAR_API_KEY', '').strip()
        if value:
            return value, 'environment'
        value, _ = self.store(self.directory).load()
        if value:
            return value, 'plugin'
        if self.credential_directory.resolve() != self.directory.resolve():
            value, _ = self.store(self.credential_directory).load()
            if value:
                return value, 'desktop'
        return '', 'none'

    def preferences(self):
        path = self.directory / 'preferences.json'
        if not path.exists():
            return {'default_work_dir': ''}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if type(data) is not dict or set(data) != {'default_work_dir'} or not isinstance(data['default_work_dir'], str):
                raise ValueError()
        except (OSError, ValueError, UnicodeError):
            raise OrbitError('settings_unreadable', '插件设置无法读取，原文件已保留。') from None
        return data

    def summary(self):
        value, source = self.credentials()
        return {'configured': bool(value), 'source': source, **self.preferences()}

    def test_connection(self, key=''):
        value = key.strip() or self.credentials()[0]
        if not value or len(value) > 4096 or any(character.isspace() for character in value):
            raise OrbitError('invalid_credentials', '请填写有效的 Linear API Key。')
        try:
            data = self.request(value, {}, 'query OrbitPluginConnection { viewer { id name } }')
            viewer = data['viewer']
            if not isinstance(viewer, dict) or not isinstance(viewer.get('id'), str) or not viewer['id'] or not isinstance(viewer.get('name'), str):
                raise ValueError()
            return {'account': viewer['name']}
        except LinearError:
            raise OrbitError('connection_failed', '连接未通过，请检查密钥权限、有效期或网络。') from None
        except (KeyError, TypeError, ValueError):
            raise OrbitError('connection_failed', 'Linear 未返回完整账号信息，请稍后重试。') from None

    def save(self, key='', default_work_dir=''):
        work_dir = absolute_directory(default_work_dir) if default_work_dir.strip() else ''
        key = key.strip()
        if key and os.environ.get('LINEAR_API_KEY', '').strip():
            raise OrbitError('environment_override', '当前由环境变量提供密钥，请先移除 LINEAR_API_KEY 再通过卡片更换。')
        # Verify the actual effective key before changing credentials or preferences.
        verified = self.test_connection(key)
        if key:
            self.store(self.directory).save(key)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'default_work_dir': work_dir}, stream, ensure_ascii=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / 'preferences.json')
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {**verified, **self.summary(), 'saved': True}

    def clear_plugin_key(self):
        # A shared desktop credential remains owned by the desktop application.
        self.store(self.directory).clear()
        return self.summary()


def desktop_environment():
    """Restore only GUI launch variables when Codex did not inherit the desktop session."""
    env = dict(os.environ)
    if not sys.platform.startswith('linux') or env.get('DISPLAY') or env.get('WAYLAND_DISPLAY') or env.get('QT_QPA_PLATFORM') in ('offscreen', 'minimal'):
        return env
    runtime = Path('/run/user') / str(os.getuid())
    if runtime.is_dir():
        env.setdefault('XDG_RUNTIME_DIR', str(runtime))
    if (runtime / 'bus').exists():
        env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=' + str(runtime / 'bus'))
    try:
        result = subprocess.run(['systemctl', '--user', 'show-environment'], env=env,
                                capture_output=True, text=True, timeout=2)
        allowed = {'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                name, separator, _ = line.partition('=')
                if separator and name in allowed:
                    parsed = shlex.split(line)
                    if len(parsed) == 1 and '=' in parsed[0]:
                        value = parsed[0].split('=', 1)[1]
                        if value and '\x00' not in value:
                            env.setdefault(name, value)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return env


def open_card(directory, credential_directory, *, automatic=False):
    """Start a detached native card. No secret is passed in arguments or IPC."""
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / 'scripts/mcp.py'), '--settings', '--data-dir', str(directory),
               '--credentials-dir', str(credential_directory)]
    if importlib.util.find_spec('PyQt5') is None:
        return {'state': 'ui_unavailable', 'reason': 'missing_pyqt', 'message': '当前 Python 缺少设置卡片依赖，请安装插件 requirements-ui.txt。',
                'local_command': command}
    env = desktop_environment()
    if env.get('QT_QPA_PLATFORM') in ('offscreen', 'minimal') or (sys.platform.startswith('linux') and not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'))):
        return {'state': 'ui_unavailable', 'reason': 'no_display', 'message': '当前会话没有可见桌面，请在本机终端打开设置卡片。',
                'local_command': command}
    options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, env=env, **options)
    return {'state': 'opening', 'message': '已请求打开本机星序设置卡片，请在卡片中输入 Linear API Key。', 'automatic': automatic}
