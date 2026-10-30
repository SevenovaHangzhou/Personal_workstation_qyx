#!/usr/bin/env bash
set -euo pipefail
MOMO_ROOT="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
MOMO_LOG_DIR="${MOMO_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/orbit}"
MOMO_LOG_FILE="$MOMO_LOG_DIR/startup.log"
source "$MOMO_ROOT/packaging/ota-launch.sh"
orbit_ota_launch "$(sed -n 's/^APP_VERSION = "\([0-9][0-9.]*\)"$/\1/p' "$MOMO_ROOT/typedefine/TypeDefine.py")" "$@"

momo_finish() {
    local momo_status=$?
    trap - EXIT
    if (( momo_status != 0 )); then
        local momo_message
        momo_message="星序 ORBIT 启动失败（退出码 $momo_status）。
请查看启动日志：$MOMO_LOG_FILE

需要 Python 3.11+ 和 requirements.txt 中的依赖。
可在程序目录创建 .venv，或用 MOMO_PYTHON 指定已安装依赖的解释器。"
        printf '%s\n' "$momo_message" >&2
        if [[ "${MOMO_NO_ERROR_DIALOG:-0}" != 1 && ( -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ) ]]; then
            if command -v zenity >/dev/null 2>&1; then
                zenity --error --no-markup --title="星序 ORBIT 启动失败" --width=560 --text="$momo_message" || true
            elif command -v kdialog >/dev/null 2>&1; then
                kdialog --title "星序 ORBIT 启动失败" --error "$momo_message" || true
            elif command -v xmessage >/dev/null 2>&1; then
                xmessage -center "$momo_message" || true
            fi
        elif [[ -t 0 ]]; then
            read -r -p "按回车关闭此窗口…" _momo_ack || true
        fi
    fi
    exit "$momo_status"
}
trap momo_finish EXIT
mkdir -p -- "$MOMO_LOG_DIR"
if [[ -f "$MOMO_LOG_FILE" ]] && (( $(stat -c %s -- "$MOMO_LOG_FILE") > 5242880 )); then
    mv -- "$MOMO_LOG_FILE" "$MOMO_LOG_FILE.previous"
fi
exec > >(tee -a -- "$MOMO_LOG_FILE") 2>&1
printf '\n[%s] 星序 ORBIT 启动\n' "$(date '+%Y-%m-%d %H:%M:%S')"

MOMO_INTERPRETER="${MOMO_PYTHON:-}"
if [[ -z "$MOMO_INTERPRETER" && -x "$MOMO_ROOT/.venv/bin/python" ]]; then
    MOMO_INTERPRETER="$MOMO_ROOT/.venv/bin/python"
fi
if [[ -z "$MOMO_INTERPRETER" ]]; then
    for momo_candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$momo_candidate" >/dev/null 2>&1 &&
           "$momo_candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' &&
           "$momo_candidate" "$MOMO_ROOT/check_env.py" --quiet; then
            MOMO_INTERPRETER="$momo_candidate"
            break
        fi
    done
fi
if [[ -z "$MOMO_INTERPRETER" ]]; then
    printf '%s\n' "未找到具备依赖的 Python 3.11+；系统默认 Python 可能过旧，且本目录尚无可用 .venv。" >&2
    exit 1
fi
printf 'Python: %s\n' "$MOMO_INTERPRETER"
"$MOMO_INTERPRETER" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "当前解释器低于 Python 3.11，请创建 .venv 或更正 MOMO_PYTHON")'
"$MOMO_INTERPRETER" "$MOMO_ROOT/check_env.py" --quiet
"$MOMO_INTERPRETER" "$MOMO_ROOT/main.py" "$@"
