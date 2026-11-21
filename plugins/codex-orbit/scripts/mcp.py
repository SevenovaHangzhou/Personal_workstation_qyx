"""Launch the bundled stdio server, or configure credentials in a local terminal."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'shared'
sys.path[:0] = [str(ROOT), str(SHARED if SHARED.is_dir() else ROOT.parent.parent)]

from orbit_plugin.core import Orbit, OrbitError


def main():
    parser = argparse.ArgumentParser(description='星序 Codex 插件')
    if sys.version_info < (3, 11):
        parser.error('请使用 Python 3.11 或更新版本。运行 scripts/configure.py 可配置启动解释器。')
    settings_path = ROOT / 'settings.json'
    try:
        settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
        if type(settings) is not dict or set(settings) - {'data_dir', 'credentials_dir'} or any(not isinstance(v, str) for v in settings.values()):
            raise ValueError()
    except (OSError, ValueError, UnicodeError):
        parser.error('本地 settings.json 无效，请重新配置；未修改原数据。')
    parser.add_argument('--data-dir', default=os.environ.get('ORBIT_DATA_DIR', settings.get('data_dir')))
    parser.add_argument('--credentials-dir', default=os.environ.get('ORBIT_CREDENTIALS_DIR', settings.get('credentials_dir')))
    parser.add_argument('--setup', action='store_true', help='在本机终端中输入 Linear API Key；不要把密钥放入聊天或命令参数')
    parser.add_argument('--chat-card', help='打开任务聊天卡片计划')
    parser.add_argument('--settings', action='store_true', help='打开本机原生设置卡片')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--lan', action='store_true', help='打开局域网日志与 Shared Store 卡片')
    parser.add_argument('--lan-serve', action='store_true', help='运行已启用的本机日志节点')
    parser.add_argument('--collect-daily', action='store_true', help='收集已到期的绑定聊天日报，补跑失败周期')
    parser.add_argument('--install-schedule', action='store_true', help='安装或更新本机每日收集任务，默认 00:00')
    parser.add_argument('--daily-time', help='本机时间 HH:MM')
    parser.add_argument('--schedule-enable', action='store_true')
    parser.add_argument('--schedule-disable', action='store_true')
    parser.add_argument('--schedule', action='store_true', help='打开日报定时设置卡片')
    args = parser.parse_args()
    try:
        orbit = Orbit(args.data_dir, credential_directory=args.credentials_dir)
        if args.collect_daily:
            import signal
            import threading
            from orbit_plugin.nightly import DailyCollector
            cancel=threading.Event()
            for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:cancel.set())
            result=DailyCollector(orbit).collect(cancel=cancel)
            print(json.dumps(result,ensure_ascii=False))
            raise SystemExit(2 if result.get('pending') else 0)
        elif args.install_schedule:
            from orbit_plugin.scheduler import Scheduler
            if args.schedule_enable and args.schedule_disable: parser.error('不能同时启用和停用')
            enabled=False if args.schedule_disable else True if args.schedule_enable else None
            value=Scheduler(orbit.directory).configure(args.daily_time,enabled)
            print(json.dumps(value,ensure_ascii=False))
        elif args.schedule:
            from orbit_plugin.schedule_ui import run_card
            raise SystemExit(run_card(orbit))
        elif args.lan_serve:
            from orbit_plugin.lan import run_node
            raise SystemExit(run_node(orbit))
        elif args.lan:
            from orbit_plugin.lan_ui import run_card
            raise SystemExit(run_card(orbit))
        elif args.chat_card:
            from orbit_plugin.chat_card_ui import run_card
            raise SystemExit(run_card(orbit.directory, args.chat_card))
        elif args.settings:
            try:
                from orbit_plugin.settings_ui import run_card
            except ImportError:
                parser.exit(2, '设置卡片需要 PyQt5，请使用当前 Python 安装插件 requirements-ui.txt。\n')
            from orbit_plugin.settings import Settings
            raise SystemExit(run_card(Settings(orbit.directory, orbit.credential_directory)))
        elif args.setup:
            if not sys.stdin.isatty():
                parser.error('--setup 需要本地交互终端。')
            secret = getpass.getpass('Linear API Key（隐藏输入）: ').strip()
            from orbit_plugin.settings import Settings
            Settings(orbit.directory, orbit.credential_directory).save(secret)
            print('已保存本机凭据。新建 Codex 会话后即可拉取 My Issues。')
        elif args.status:
            print(json.dumps(orbit.status(), ensure_ascii=False, indent=2))
        else:
            from orbit_plugin.server import serve
            from orbit_plugin.lan import start_node
            # Existing explicit opt-in persists across Codex sessions. Never enable sharing here.
            try:
                start_node(orbit)
            except (OSError, ValueError):
                pass
            orbit.auto_setup = True
            sys.stdin.reconfigure(encoding='utf-8')
            sys.stdout.reconfigure(encoding='utf-8')
            serve(orbit)
    except (OrbitError,ValueError,InterruptedError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
