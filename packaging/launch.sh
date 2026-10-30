#!/usr/bin/env bash
set -euo pipefail
ORBIT_APP_DIR="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
source "$ORBIT_APP_DIR/ota-launch.sh"
orbit_ota_launch "$(cat -- "$ORBIT_APP_DIR/VERSION")" "$@"
if [[ "${1:-}" == --version || "${1:-}" == --help || "${1:-}" == --self-test ]]; then
    exec "$ORBIT_APP_DIR/orbit-bin" "$@"
fi
ORBIT_LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/orbit"
mkdir -p -- "$ORBIT_LOG_DIR"
ORBIT_LOG_FILE="$ORBIT_LOG_DIR/startup.log"
if [[ -f "$ORBIT_LOG_FILE" ]] && (( $(stat -c %s -- "$ORBIT_LOG_FILE") > 5242880 )); then
    mv -- "$ORBIT_LOG_FILE" "$ORBIT_LOG_FILE.previous"
fi
exec > >(tee -a -- "$ORBIT_LOG_FILE") 2>&1
printf '\n[%s] ORBIT 启动\n' "$(date '+%Y-%m-%d %H:%M:%S')"
"$ORBIT_APP_DIR/orbit-bin" "$@"
