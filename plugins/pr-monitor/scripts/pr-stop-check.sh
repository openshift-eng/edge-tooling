#!/usr/bin/bash
set -euo pipefail

# Stop hook decision script: check whether to restart a pr-monitor session.
# Uses status field from PR_MONITOR_STATE to determine action:
#   complete → exit (PR is done)
#   waiting  → sleep next_check_delay, then spawn new session
#   running  → unexpected exit (crash), respawn immediately up to max_iterations
# Exit codes: 0=restarted, 1=done/max reached, 2=not a pr-monitor session, 3=internal error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
    echo "Error: $1" >&2
    exit 3
}

log() {
    echo "[pr-monitor-stop] $1" >&2
}

get_field() {
    local state="$1" field="$2"
    local normalized=";${state}"
    echo "${normalized}" | sed -n "s/.*;${field}=\([^;]*\).*/\1/p"
}

set_field() {
    local state="$1" field="$2" value="$3"
    # Escape sed-special characters in value: \ must be first, then & and the % delimiter
    local escaped_value
    escaped_value=$(printf '%s' "${value}" | sed 's/[\\&%]/\\&/g')
    local normalized=";${state}"
    if printf '%s' "${normalized}" | grep -qF ";${field}="; then
        normalized=$(printf '%s' "${normalized}" | sed "s%;${field}=[^;]*%;${field}=${escaped_value}%")
        echo "${normalized#;}"
    else
        echo "${state};${field}=${escaped_value}"
    fi
}

main() {
    local state="${PR_MONITOR_STATE:-}"
    if [[ -z "${state}" ]]; then
        exit 2
    fi

    local status pr_url iteration max_iterations notes next_check_delay
    status=$(get_field "${state}" "status")
    pr_url=$(get_field "${state}" "pr_url")

    if [[ "${status}" == "complete" ]]; then
        log "PR monitor completed successfully. Not restarting."
        exit 1
    fi

    if [[ "${status}" == "waiting" ]]; then
        next_check_delay=$(get_field "${state}" "next_check_delay")
        next_check_delay="${next_check_delay:-300}"
        iteration=$(get_field "${state}" "iteration")
        iteration="${iteration:-0}"
        max_iterations=$(get_field "${state}" "max_iterations")
        max_iterations="${max_iterations:-3}"
        notes=$(get_field "${state}" "notes")

        local new_iteration=$((iteration + 1))

        if [[ "${max_iterations}" -gt 0 && "${new_iteration}" -ge "${max_iterations}" ]]; then
            log "Max iterations reached (${new_iteration}/${max_iterations}). Not restarting."
            exit 1
        fi

        local new_state
        new_state=$(set_field "${state}" "iteration" "${new_iteration}")
        new_state=$(set_field "${new_state}" "status" "running")

        log "Cycle complete. Next check in ${next_check_delay}s (iteration ${new_iteration})."
        [[ -n "${notes}" ]] && log "Previous cycle: ${notes}"

        command -v claude >/dev/null 2>&1 || die "claude CLI is not installed"

        (
            sleep "${next_check_delay}"
            PR_MONITOR_STATE="${new_state}" claude -p "/pr-monitor:watch ${pr_url}" \
                > "/tmp/pr-monitor-iter-${new_iteration}.log" 2>&1
        ) &
        disown

        exit 0
    fi

    if [[ "${status}" == "running" ]]; then
        iteration=$(get_field "${state}" "iteration")
        iteration="${iteration:-0}"
        max_iterations=$(get_field "${state}" "max_iterations")
        max_iterations="${max_iterations:-3}"
        notes=$(get_field "${state}" "notes")

        local new_iteration=$((iteration + 1))

        if [[ "${max_iterations}" -gt 0 && "${new_iteration}" -ge "${max_iterations}" ]]; then
            log "Max iterations reached during crash recovery (${new_iteration}/${max_iterations}). Not restarting."
            exit 1
        fi

        local new_state
        new_state=$(set_field "${state}" "iteration" "${new_iteration}")

        log "Unexpected exit. Crash restart (iteration ${new_iteration})."
        [[ -n "${notes}" ]] && log "Previous cycle: ${notes}"

        command -v claude >/dev/null 2>&1 || die "claude CLI is not installed"

        PR_MONITOR_STATE="${new_state}" nohup claude -p "/pr-monitor:watch ${pr_url}" \
            > "/tmp/pr-monitor-crash-${new_iteration}.log" 2>&1 &

        exit 0
    fi

    log "Unknown status: ${status}"
    exit 3
}

main
