#!/usr/bin/bash
# Shared capture setup, retries, pod discovery, and follower cleanup.

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/proxy-helpers.sh"

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }

# File-writing collectors may override the default runs/ directory with $1.
setup_capture_output() {
    local default_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/runs}"
    umask 077
    LOG_DIR="${CAPTURE_LOG_DIR:-${default_dir}}"
    # shellcheck disable=SC2034 # Timestamp is consumed by the collector.
    TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
    mkdir -p "${LOG_DIR}"
}

require_capture_interval() {
    if [[ ! "$1" =~ ^0*[1-9][0-9]*$ ]]; then
        echo "Error: $2 must be a positive integer (seconds)." >&2
        exit 1
    fi
}

capture_retry_loop() {
    local reconnect_sleep="$1" capture_pid=""
    shift
    # Give each attempt its own process group, including function-launched children.
    set -m
    trap 'if [[ -n "${capture_pid}" ]]; then
        kill -- "-${capture_pid}" 2>/dev/null || true
        wait "${capture_pid}" 2>/dev/null || true
    fi; exit 0' INT TERM
    while true; do
        (
            "$@" || true
            printf '=== %s stream ended or unavailable; retrying in %ss ===\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${reconnect_sleep}"
            sleep "${reconnect_sleep}"
        ) &
        capture_pid=$!
        wait "${capture_pid}" 2>/dev/null || true
    done
}

# Collectors register their followers here (pod/component => follower PID).
declare -A stream_pids=()
# Last exit status distinguishes completed log collection from a failed stream.
declare -A stream_status=()

start_capture_stream() {
    local pod="$1"
    shift
    "$@" &
    stream_pids["${pod}"]="$!"
}

# Reconnect active pods, but keep successful collections of terminal pods.
should_start_capture_stream() {
    local pod="$1" phase="$2"
    [[ -z "${stream_pids[$pod]:-}" &&
       ( "${stream_status[$pod]:-}" != 0 || ( "${phase}" != Succeeded && "${phase}" != Failed ) ) ]]
}

reap_capture_streams() {
    local pod
    for pod in "${!stream_pids[@]}"; do
        if ! kill -0 "${stream_pids[$pod]}" 2>/dev/null; then
            stream_status["${pod}"]=0
            wait "${stream_pids[$pod]}" 2>/dev/null || stream_status["${pod}"]=$?
            unset 'stream_pids[$pod]'
        fi
    done
}

stop_capture_streams() {
    local pid
    for pid in "${stream_pids[@]:-}"; do
        kill "${pid}" 2>/dev/null || true
    done
}

# Callers define start_stream_for_pod, launching through start_capture_stream.
# Remaining arguments are oc get pod selectors (e.g. -l app=etcd-operator).
capture_pod_streams() {
    local namespace="$1" poll_sec="$2" pod phase
    shift 2
    trap stop_capture_streams EXIT
    trap 'exit 0' INT TERM
    while true; do
        reap_capture_streams
        while read -r pod phase; do
            [[ -z "${pod}" ]] && continue
            should_start_capture_stream "${pod}" "${phase}" || continue
            start_stream_for_pod "${pod}"
        done < <(oc get pods -n "${namespace}" "$@" --no-headers \
            -o custom-columns=NAME:.metadata.name,PHASE:.status.phase 2>/dev/null)
        sleep "${poll_sec}"
    done
}
