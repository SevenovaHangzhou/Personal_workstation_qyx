"""Install a self-contained ORBIT marketplace with preflight and rollback.

Only Codex's public plugin commands manage its registry and cache. Installation
never opens the task database or copies credentials. Source and ZIP installs use
the same stable marketplace directory, independent of the download location.
"""
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

PLUGIN_RELATIVE = Path('plugins/codex-orbit')
MARKETPLACE_MANIFEST = Path('.agents/plugins/marketplace.json')
MARKETPLACE = 'sevenova'
PLUGIN = 'orbit'


class InstallError(Exception):
    pass


def fail(message):
    raise InstallError(message)


def info(message):
    print(message, flush=True)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        fail(f'{path} 无法读取或不是有效 JSON。')


def plugin_directory(root):
    return Path(root) / PLUGIN_RELATIVE


def find_root(explicit):
    root = Path(explicit).expanduser().resolve() if explicit else Path(__file__).resolve().parents[3]
    if not (plugin_directory(root) / 'plugin.json').is_file():
        fail(f'在 {root} 下找不到 plugins/codex-orbit/plugin.json；请使用完整源码或分发包。')
    return root


def validate_manifest(root):
    plugin = plugin_directory(root)
    manifest = read_json(plugin / 'plugin.json')
    if type(manifest) is not dict or manifest.get('name') != PLUGIN or not manifest.get('version'):
        fail('plugin.json 缺少 orbit 名称或版本。')
    mcp = read_json(plugin / 'mcp.json')
    servers = mcp.get('mcpServers') if isinstance(mcp, dict) else None
    server = servers.get(PLUGIN) if isinstance(servers, dict) else None
    if not isinstance(server, dict) or server.get('type') != 'stdio' or '${PLUGIN_ROOT}/scripts/mcp.py' not in server.get('args', []):
        fail('mcp.json 缺少 orbit stdio 服务或使用了不可移植的脚本路径。')
    if not any((plugin / 'skills').glob('*/SKILL.md')):
        fail('插件缺少 skills/<名称>/SKILL.md。')
    return manifest['version']


def self_contained(root):
    return ((Path(root) / MARKETPLACE_MANIFEST).is_file()
            and (plugin_directory(root) / 'shared/linear_support.py').is_file())


def default_install_directory():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    # Keep the existing 0.3.0 registration and sibling backups/runtime intact.
    return base / 'Sevenova/OrbitCodexPlugin/marketplace'


def execute(command, *, env=None, input=None, timeout=120):
    try:
        result = subprocess.run([str(p) for p in command], input=input, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        fail(f'无法运行或运行超时：{command[0]}。请检查解释器、Codex 与网络。')
    if result.returncode:
        # Third-party stdout/stderr can contain configuration or credentials.
        fail(f'命令失败（退出码 {result.returncode}）：{command[0]}；未输出可能含敏感信息的诊断。')
    return result.stdout


def python_supported(executable):
    return execute([executable, '-c', 'import sys; print(int(sys.version_info >= (3,11)))']).strip() == '1'


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')


def plugins_feature_state():
    """Return (state, parse_error); state is True, False or None when absent."""
    path = codex_home() / 'config.toml'
    if not path.is_file():
        return None, None
    try:
        import tomllib
    except ImportError:
        return None, None
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f'{path} 无法解析：{exc}'
    features = data.get('features')
    return (features.get('plugins') if isinstance(features, dict) else None), None


def with_plugins_flag(text):
    lines = text.splitlines()
    header = re.compile(r'^\[features\]\s*(?:#.*)?$')
    start = next((index for index, line in enumerate(lines) if header.match(line.strip())), None)
    if start is None:
        return (text + ('\n' if text and not text.endswith('\n') else '') + '\n[features]\nplugins = true\n')
    end = next((index for index in range(start + 1, len(lines))
                if lines[index].strip().startswith('[')), len(lines))
    for index in range(start + 1, end):
        if re.match(r'^plugins\s*=', lines[index].strip()):
            indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f'{indent}plugins = true'
            return '\n'.join(lines) + '\n'
    lines.insert(start + 1, 'plugins = true')
    return '\n'.join(lines) + '\n'


def ensure_plugins_feature(enabled):
    path = codex_home() / 'config.toml'
    state, error = plugins_feature_state()
    if state is True:
        info('Codex 已启用 plugins 功能。')
        return
    if not enabled:
        info('提示：未确认 Codex 的 [features] plugins = true（部分版本需要）。')
        info(f'  可在 {path} 中加入，或下次运行本脚本时加 --enable-plugins 自动写入并备份。')
        if error:
            info(f'  注意：{error}；改动前请先手动检查该文件。')
        return
    if error:
        fail(f'{error}；为避免破坏原有配置，未写入。请手动加入 [features] plugins = true。')
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    if path.is_file():
        backup = path.with_name('config.toml.bak-' + time.strftime('%Y%m%d%H%M%S'))
        shutil.copy2(path, backup)
        info(f'已备份 Codex 配置：{backup}')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.config-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(with_plugins_flag(text))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    info('已在 Codex 配置中启用 plugins 功能。')


def find_codex(explicit):
    found = shutil.which(explicit or 'codex')
    if not found:
        fail('未找到 Codex CLI，请安装新版 Codex，或用 --codex 指定路径。')
    return found


def codex_json(codex, *arguments):
    raw = execute([codex, 'plugin', *arguments, '--json'])
    try:
        result = json.loads(raw)
    except ValueError:
        fail('Codex 未返回有效 JSON，请升级到支持 plugin --json 的版本。')
    if not isinstance(result, dict):
        fail('Codex 返回了未知的插件状态格式。')
    return result


def configured_marketplaces(codex):
    rows = codex_json(codex, 'marketplace', 'list').get('marketplaces')
    if not isinstance(rows, list):
        fail('Codex 未返回 marketplace 清单，请升级 Codex。')
    return {row['name']: row for row in rows if isinstance(row, dict) and isinstance(row.get('name'), str)}


def source_of(row):
    source = row.get('marketplaceSource') or {}
    return source.get('source') or row.get('root')


def same_directory(first, second):
    return bool(first and second) and Path(first).expanduser().resolve() == Path(second).expanduser().resolve()


def validate_target(root, target, protected=()):
    if target.is_symlink():
        fail('安装目录不能是符号链接。')
    target = target.resolve()
    if root in target.parents:
        fail('安装目录必须在源码目录之外，避免将工作副本当作分发目录替换。')
    if codex_home().resolve() in target.parents:
        fail('不能安装到 Codex 自己的配置或缓存目录；请使用独立分发目录。')
    source_paths = [] if target == root and self_contained(root) else [root]
    for path in [Path.home(), codex_home(), *source_paths, *protected]:
        path = Path(path).expanduser().resolve()
        if target == path or target in path.parents:
            fail('安装目录会覆盖源码、用户目录或数据目录，请选择独立空目录。')
    if target.exists() and any(target.iterdir()):
        if not self_contained(target):
            fail('目标目录含有非 ORBIT 分发文件，拒绝替换；请选择空目录。')
        validate_manifest(target)
        catalog = read_json(target / MARKETPLACE_MANIFEST)
        if catalog.get('name') != MARKETPLACE:
            fail('目标目录属于其他 marketplace，拒绝替换。')
        # Older installs put marketplace and backup directories one level deeper.
        if (target / 'marketplace').exists():
            fail('目标是安装父目录，请将 --install-dir 指向其中的 marketplace。')
    return target


@contextmanager
def install_lock(target):
    path = target.with_name(target.name + '.install.lock')
    target.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fail('另一个安装进程正在运行，请待其结束后重试。')
        yield
        # Closing the descriptor releases the lock. Keep its path to avoid races.


def prepare_distribution(root, destination):
    if self_contained(root):
        shutil.copytree(root, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.credentials',
                       '*.sqlite3*', 'settings.json', '.venv', '.git'))
    else:
        spec = importlib.util.spec_from_file_location('build_codex_plugin', root / 'scripts/build_codex_plugin.py')
        if spec is None or spec.loader is None:
            fail('源码缺少 scripts/build_codex_plugin.py。')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build(destination)


def previous_settings(target, old_source, root):
    # The registered directory takes priority for a first migration. An existing
    # managed target takes priority on repeat installs. Never read credential files.
    candidates = [target]
    if old_source:
        candidates.append(Path(old_source))
    if self_contained(root):
        candidates.append(root)
    for directory in candidates:
        path = plugin_directory(directory) / 'settings.json'
        if path.is_file():
            settings = read_json(path)
            if not isinstance(settings, dict) or set(settings) - {'data_dir', 'credentials_dir'} or any(
                    not isinstance(v, str) or not Path(v).is_absolute() for v in settings.values()):
                fail('现有 settings.json 无效；为保留原配置，未执行升级。')
            return settings
    return {}


def prepare_python(base, target, *, headless=False, existing=False):
    if existing or headless:
        python = base
    else:
        runtime = target.with_name(target.name + '-runtime')
        if runtime.is_symlink():
            fail('运行环境目录不能是符号链接。')
        python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.is_file():
            if runtime.exists() and any(runtime.iterdir()):
                fail('运行环境目录不完整，请移走后重试：' + str(runtime))
            info('创建插件专用 Python 环境（不改系统 Python）…')
            execute([base, '-m', 'venv', str(runtime)])
        if not python_supported(python):
            fail('插件专用 Python 版本过低。')
    if not headless:
        has_qt = execute([python, '-c', "import importlib.util; print(int(importlib.util.find_spec('PyQt5') is not None and importlib.util.find_spec('cryptography') is not None))"]).strip() == '1'
        if not has_qt:
            if existing:
                fail('指定解释器缺少 PyQt5 或 cryptography，请先安装 requirements-ui.txt，或使用默认独立环境。')
            info('安装原生卡片与 LAN TLS 依赖（需要网络）…')
            execute([python, '-m', 'pip', 'install', 'PyQt5>=5.15,<6', 'cryptography>=41'], timeout=600)
        execute([python, '-c', 'from PyQt5 import QtWidgets, QtNetwork; from cryptography import x509'])
    return str(python)


def configure(python, distribution, settings, data_dir=None, credentials_from=None):
    (plugin_directory(distribution) / 'settings.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    command = [python, plugin_directory(distribution) / 'scripts/configure.py']
    if data_dir:
        command += ['--data-dir', data_dir]
    if credentials_from:
        command += ['--credentials-from', credentials_from]
    execute(command)


def verify_server(python, distribution, version):
    with tempfile.TemporaryDirectory(prefix='orbit-install-check-') as directory:
        env = {key: value for key, value in os.environ.items() if key not in
               ('LINEAR_API_KEY', 'ORBIT_DATA_DIR', 'ORBIT_CREDENTIALS_DIR', 'PYTHONPATH')}
        env.update(ORBIT_DATA_DIR=directory, ORBIT_CREDENTIALS_DIR=directory)
        messages = [dict(jsonrpc='2.0', id=1, method='initialize', params={}),
                    dict(jsonrpc='2.0', id=2, method='tools/list', params={})]
        output = execute([python, plugin_directory(distribution) / 'scripts/mcp.py'], env=env,
                         input=''.join(json.dumps(m)+'\n' for m in messages), timeout=20)
        try:
            replies = {row['id']: row['result'] for row in map(json.loads, output.splitlines())}
            assert replies[1]['serverInfo']['version'] == version
            tools = {tool['name'] for tool in replies[2]['tools']}
            assert {'open_settings', 'sync_issue_chats', 'publish_result'} <= tools
        except (ValueError, KeyError, TypeError, AssertionError):
            fail('MCP 启动或工具发现自检失败，未替换当前插件。')


def install_codex(codex, distribution, version, old):
    if old and not same_directory(source_of(old), distribution):
        codex_json(codex, 'marketplace', 'remove', MARKETPLACE)
    if not old or not same_directory(source_of(old), distribution):
        codex_json(codex, 'marketplace', 'add', str(distribution))
    codex_json(codex, 'add', f'{PLUGIN}@{MARKETPLACE}')
    listing = codex_json(codex, 'list', '--marketplace', MARKETPLACE)
    if not any(row.get('pluginId') == f'{PLUGIN}@{MARKETPLACE}' and row.get('installed') is True
               and row.get('enabled') is True and row.get('version') == version
               for row in listing.get('installed', [])):
        fail('Codex 未确认当前版本 installed/enabled；安装没有完成。')


def restore_registration(codex, old):
    # Best effort after restoring files; use official commands for the old source.
    rows = configured_marketplaces(codex)
    current = rows.get(MARKETPLACE)
    if current and (not old or source_of(current) != source_of(old)):
        codex_json(codex, 'marketplace', 'remove', MARKETPLACE)
    if old:
        if not current or source_of(current) != source_of(old):
            source = old.get('marketplaceSource') or {}
            command = ['marketplace', 'add', source_of(old)]
            if source.get('refName'):
                command += ['--ref', source['refName']]
            codex_json(codex, *command)
        codex_json(codex, 'add', f'{PLUGIN}@{MARKETPLACE}')
    else:
        # No previous plugin belonged to this installer. Do not leave a new cache
        # entry whose source directory has just been rolled back.
        codex_json(codex, 'remove', f'{PLUGIN}@{MARKETPLACE}')


def install(args):
    root = find_root(args.source)
    version = validate_manifest(root)
    if getattr(args,'daily_time',None) and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',args.daily_time):
        fail('日报时间需为 HH:MM，例如 00:00 或 23:30。')
    base = (shutil.which(args.python) or str(Path(args.python).expanduser().absolute())) if args.python else sys.executable
    if not python_supported(base):
        fail('需要 Python 3.11+；请使用 install.sh / install.ps1 自动选择，或指定 --python。')
    target = Path(args.install_dir).expanduser().absolute() if args.install_dir else default_install_directory()
    protected = [Path(p).expanduser() for p in (args.data_dir, args.credentials_from) if p]
    for path in protected:
        if not path.is_absolute():
            fail('数据与凭据来源目录必须为绝对路径。')
    if args.credentials_from and not Path(args.credentials_from).expanduser().is_dir():
        fail('凭据来源目录不存在。')
    target = validate_target(root, target, protected)
    codex = None if args.no_codex else find_codex(args.codex)
    # Check CLI support before creating directories or replacing files.
    old = configured_marketplaces(codex).get(MARKETPLACE) if codex and not args.dry_run else None
    settings = previous_settings(target, source_of(old) if old else None, root)
    data = args.data_dir or settings.get('data_dir', '默认 Sevenova/OrbitCodex')
    info(f'星序 Codex 插件 {version} → {target}')
    if args.dry_run:
        info('预览：准备自包含插件 → 独立运行环境/卡片依赖 → MCP 自检 → Codex 注册与状态核对。')
        info(f'插件数据：{data}；不读取任务或密钥。')
        if codex:
            info('codex plugin marketplace add <分发目录> --json')
            info('codex plugin add orbit@sevenova --json')
            if not getattr(args,'no_schedule',False):
                info('安装后自动注册每日聊天日志收集：'+(getattr(args,'daily_time',None) or '00:00（已有设置保留）'))
        info('dry-run 完成，未修改任何文件。')
        return
    with install_lock(target):
        # Another installer may have completed while preflight was running.
        old = configured_marketplaces(codex).get(MARKETPLACE) if codex else None
        validate_target(root, target, protected)
        settings = previous_settings(target, source_of(old) if old else None, root)
        with tempfile.TemporaryDirectory(prefix='orbit-prepare-', dir=target.parent) as temporary:
            staged = Path(temporary) / 'marketplace'
            prepare_distribution(root, staged)
            python = prepare_python(base, target, headless=args.headless, existing=args.use_existing_python)
            configure(python, staged, settings, args.data_dir, args.credentials_from)
            verify_server(python, staged, version)
            backup = target.with_name(target.name + '.previous-' + uuid.uuid4().hex)
            had_previous = target.exists()
            switched = False
            registry_attempted = False
            try:
                if had_previous:
                    target.rename(backup)
                staged.rename(target)
                switched = True
                if codex:
                    if args.enable_plugins:
                        ensure_plugins_feature(True)
                    registry_attempted = True
                    install_codex(codex, target, version, old)
            except BaseException:
                if switched:
                    shutil.rmtree(target)
                if had_previous and backup.exists():
                    backup.rename(target)
                if registry_attempted:
                    try:
                        restore_registration(codex, old)
                    except InstallError:
                        info('旧分发文件已恢复；Codex 注册恢复失败，请重新运行安装。')
                raise
            else:
                if backup.exists():
                    shutil.rmtree(backup)
    info(f'安装完成：{version}；MCP 自检通过。' + ('已跳过 Codex 注册。' if not codex else 'Codex 已安装并启用。'))
    if codex and not getattr(args,'no_schedule',False):
        command=[python,plugin_directory(target)/'scripts/mcp.py','--install-schedule']
        if getattr(args,'daily_time',None): command+=['--daily-time',args.daily_time]
        try:
            value=json.loads(execute(command))
            info('日报定时任务已注册：每天 '+value['config']['time']+'（本机时间）；'+('已启用。' if value['config']['enabled'] else '保留原停用设置。'))
        except (InstallError,ValueError,KeyError):
            fail('插件文件已安装，但每日任务未注册成功。请在已登录的本机桌面打开「日报定时收集」重试；原日志保留。')
    info('新建 Codex 聊天，使用 $orbit-work；首次对 Codex 说“打开星序设置”。')


def main():
    parser = argparse.ArgumentParser(description='安装/升级 ORBIT Codex 插件；不读取或复制 Linear 密钥。')
    parser.add_argument('--source')
    parser.add_argument('--install-dir', help='专用 marketplace 目录，默认用户目录下 Sevenova/OrbitCodexPlugin/marketplace')
    parser.add_argument('--python', help='Python 3.11+；默认用于创建独立环境')
    parser.add_argument('--use-existing-python', action='store_true', help='使用指定 Python，不建环境；需已安装 PyQt5')
    parser.add_argument('--headless', action='store_true', help='只安装核心 MCP，不建环境、不下载 UI 依赖')
    parser.add_argument('--data-dir')
    parser.add_argument('--credentials-from')
    parser.add_argument('--codex')
    parser.add_argument('--enable-plugins', action='store_true', help='仅兼容旧客户端：备份后启用 plugins 功能')
    parser.add_argument('--install-ui-deps', action='store_true', help=argparse.SUPPRESS)  # Compatibility: now the default.
    parser.add_argument('--no-codex', action='store_true', help='只准备分发与运行环境')
    parser.add_argument('--daily-time', help='每日收集时间 HH:MM，首次默认 00:00，升级保留原设置')
    parser.add_argument('--no-schedule', action='store_true', help='跳过系统定时任务注册（不改变已有任务）')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        install(args)
    except (InstallError, OSError) as exc:
        parser.exit(2, '安装失败：' + str(exc) + '\n')


if __name__ == '__main__':
    main()
