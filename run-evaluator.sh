#!/usr/bin/env bash
set -euo pipefail
orbit_evaluator_root="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
orbit_evaluator_python="${MOMO_PYTHON:-}"
if [[ -z "$orbit_evaluator_python" && -x "$orbit_evaluator_root/.venv/bin/python" ]]; then
    orbit_evaluator_python="$orbit_evaluator_root/.venv/bin/python"
fi
if [[ -z "$orbit_evaluator_python" ]]; then
    for orbit_evaluator_candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$orbit_evaluator_candidate" >/dev/null 2>&1 &&
           "$orbit_evaluator_candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' &&
           "$orbit_evaluator_candidate" "$orbit_evaluator_root/check_env.py" --quiet; then
            orbit_evaluator_python="$orbit_evaluator_candidate"
            break
        fi
    done
fi
if [[ -z "$orbit_evaluator_python" ]]; then
    printf '%s\n' '未找到具备依赖的 Python 3.11+，请创建 .venv 或用 MOMO_PYTHON 指定解释器。' >&2
    exit 1
fi
"$orbit_evaluator_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "评估开发版需要 Python 3.11+")'
"$orbit_evaluator_python" "$orbit_evaluator_root/check_env.py" --quiet
exec "$orbit_evaluator_python" "$orbit_evaluator_root/evaluator.py" "$@"
