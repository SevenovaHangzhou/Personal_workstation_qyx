#!/usr/bin/env bash
# Sourced by source/packaged launchers. The pointer is a version, never shell code.
orbit_ota_version() { [[ "$1" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] && (( ${#1} <= 40 )); }
orbit_ota_newer() {
    orbit_ota_version "$1" && orbit_ota_version "$2" && [[ "$1" != "$2" ]] &&
        [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" == "$1" ]]
}
orbit_ota_pointer() {
    local orbit_tmp
    orbit_tmp="$(mktemp "$1/.pointer.XXXXXX")" || return 1
    printf '%s\n' "$3" > "$orbit_tmp" && mv -f -- "$orbit_tmp" "$1/$2"
}
orbit_ota_launch() {
    local orbit_base="$1"; shift
    [[ "${ORBIT_SKIP_OTA:-0}" != 1 ]] || return 0
    local orbit_arg orbit_expect=0 orbit_data="${XDG_DATA_HOME:-$HOME/.local/share}/MomoLocal/Momo"
    for orbit_arg in "$@"; do
        [[ "$orbit_arg" != --self-test ]] || return 0
        if (( orbit_expect )); then orbit_data="$orbit_arg"; orbit_expect=0; continue; fi
        case "$orbit_arg" in --data-dir) orbit_expect=1;; --data-dir=*) orbit_data="${orbit_arg#--data-dir=}";; esac
    done
    local orbit_updates="$orbit_data/updates" orbit_version='' orbit_target orbit_health orbit_status orbit_previous=''
    [[ -f "$orbit_updates/current-version" && ! -L "$orbit_updates/current-version" ]] || return 0
    IFS= read -r orbit_version < "$orbit_updates/current-version" || return 0
    orbit_ota_newer "$orbit_version" "$orbit_base" || return 0
    orbit_target="$orbit_updates/releases/$orbit_version"
    [[ ! -L "$orbit_target" && -x "$orbit_target/orbit" && -x "$orbit_target/orbit-bin" && -f "$orbit_target/ota-install.json" ]] || return 0
    orbit_health="$(mktemp "$orbit_updates/launch-health.XXXXXX")" || return 0
    if ORBIT_OTA_HEALTH_FILE="$orbit_health" "$orbit_target/orbit" "$@"; then
        rm -f -- "$orbit_health"; exit 0
    else
        orbit_status=$?
    fi
    if [[ "$orbit_status" == 75 || "$(cat -- "$orbit_health")" == "$orbit_version" ]]; then
        rm -f -- "$orbit_health"; exit "$orbit_status"
    fi
    rm -f -- "$orbit_health"
    printf '%s\n' "ORBIT 更新 $orbit_version 启动失败，恢复上次安装。" >&2
    # Another launch may already have changed the pointer: do not overwrite it.
    [[ "$(cat -- "$orbit_updates/current-version")" == "$orbit_version" ]] || return 0
    orbit_ota_pointer "$orbit_updates" failed-version "$orbit_version" || true
    if [[ -f "$orbit_updates/previous-version" && ! -L "$orbit_updates/previous-version" ]]; then
        IFS= read -r orbit_previous < "$orbit_updates/previous-version" || orbit_previous=''
    fi
    if orbit_ota_newer "$orbit_previous" "$orbit_base" && [[ "$orbit_previous" != "$orbit_version" && -x "$orbit_updates/releases/$orbit_previous/orbit" ]]; then
        orbit_ota_pointer "$orbit_updates" current-version "$orbit_previous" || return 0
        # A failed fallback returns to the original installation, without looping.
        if ORBIT_SKIP_OTA=1 "$orbit_updates/releases/$orbit_previous/orbit" "$@"; then exit 0; fi
    fi
    rm -f -- "$orbit_updates/current-version"
    return 0
}
