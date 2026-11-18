#!/bin/sh
# Select a supported interpreter; install.py owns packaging and installation.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for candidate in "$here/../../.venv/bin/python" python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        exec "$candidate" "$here/scripts/install.py" "$@"
    fi
done
echo '未找到 Python 3.11+。请安装新版 Python 后重试。' >&2
exit 2
