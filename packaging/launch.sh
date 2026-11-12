#!/usr/bin/env bash
set -euo pipefail
ORBIT_APP_DIR="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
ORBIT_PRODUCT="$(cat -- "$ORBIT_APP_DIR/PRODUCT")"
ORBIT_SLUG=orbit
if [[ "$ORBIT_PRODUCT" == evaluator ]]; then
    ORBIT_PRODUCT=work
    ORBIT_SLUG=orbit-evaluator
    export ORBIT_EVALUATOR_BUILD=1 ORBIT_SKIP_OTA=1
fi
export ORBIT_APP_PROFILE="$ORBIT_PRODUCT"
if [[ "$ORBIT_PRODUCT" == study ]]; then ORBIT_SLUG=luma; fi
source "$ORBIT_APP_DIR/ota-launch.sh"
orbit_ota_launch "$(cat -- "$ORBIT_APP_DIR/VERSION")" "$@"
if [[ "${1:-}" == --version || "${1:-}" == --help || "${1:-}" == --self-test ]]; then
    exec "$ORBIT_APP_DIR/$ORBIT_SLUG-bin" "$@"
fi
ORBIT_LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/$ORBIT_SLUG"
mkdir -p -- "$ORBIT_LOG_DIR"
ORBIT_LOG_FILE="$ORBIT_LOG_DIR/startup.log"
if [[ -f "$ORBIT_LOG_FILE" ]] && (( $(stat -c %s -- "$ORBIT_LOG_FILE") > 5242880 )); then
    mv -- "$ORBIT_LOG_FILE" "$ORBIT_LOG_FILE.previous"
fi
exec > >(tee -a -- "$ORBIT_LOG_FILE") 2>&1
printf '\n[%s] ORBIT 启动\n' "$(date '+%Y-%m-%d %H:%M:%S')"
"$ORBIT_APP_DIR/$ORBIT_SLUG-bin" "$@"
