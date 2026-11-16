"""Select this Python interpreter and optional local data/credential directories."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description='为当前电脑配置星序插件 Python 和本地目录；不保存密钥')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--credentials-from', type=Path, help='只读取另一个星序空间已有凭据，不访问其任务或数据锁')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('请使用 Python 3.11 或更新版本运行本脚本。')
    for value in (args.data_dir, args.credentials_from):
        if value is not None and not value.is_absolute():
            parser.error('目录必须为绝对路径。')
    if args.credentials_from is not None and not args.credentials_from.is_dir():
        parser.error('凭据来源目录不存在。')
    path = Path(__file__).resolve().parents[1] / 'mcp.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    server = payload['mcpServers']['orbit']
    server['command'] = sys.executable
    server['args'] = ['${PLUGIN_ROOT}/scripts/mcp.py']
    settings_path = path.parent / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    if args.data_dir is not None:
        settings['data_dir'] = str(args.data_dir)
    if args.credentials_from is not None:
        settings['credentials_dir'] = str(args.credentials_from)
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # No credentials in manifests; only selected device-local paths.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.mcp-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('已配置本机 Python。请安装或重新安装插件以更新 Codex 缓存。')
    if importlib.util.find_spec('PyQt5') is None:
        print('原生设置卡片需要 PyQt5：请使用当前 Python 安装插件 requirements-ui.txt。')


if __name__ == '__main__':
    main()
