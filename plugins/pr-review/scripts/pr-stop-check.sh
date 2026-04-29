#!/usr/bin/bash
set -euo pipefail

# Stop hook decision script: check whether to restart a yolo-agent session.
# Uses status field from PR_MONITOR_STATE (JSON) to determine action:
#   complete → exit (PR is done)
#   waiting  → sleep next_check_delay, then spawn new session
#   running  → unexpected exit (crash), respawn immediately up to max_iterations
# Exit codes: 0=restarted, 1=done/max reached, 2=not a yolo-agent session, 3=internal error

die() {
    echo "Error: $1" >&2
    exit 3
}

log() {
    echo "[yolo-agent-stop] $1" >&2
}

jq_get() {
    echo "${PR_MONITOR_STATE}" | jq -r --arg f "$1" '.[$f] // empty'
}

jq_set() {
    PR_MONITOR_STATE=$(echo "${PR_MONITOR_STATE}" | jq -c --arg f "$1" --arg v "$2" '.[$f] = ($v | try tonumber // $v)')
}

main() {
    if [[ -z "${PR_MONITOR_STATE:-}" ]]; then
        exit 2
    fi

    local status pr_url
    status=$(jq_get "status")
    pr_url=$(jq_get "pr_url")

    if [[ "${status}" == "complete" ]]; then
        log "yolo-agent completed successfully. Not restarting."
        exit 1
    fi

    if [[ "${status}" == "waiting" ]]; then
        local next_check_delay iteration max_iterations notes
        next_check_delay=$(jq_get "next_check_delay")
        next_check_delay="${next_check_delay:-300}"
        iteration=$(jq_get "iteration")
        iteration="${iteration:-0}"
        max_iterations=$(jq_get "max_iterations")
        max_iterations="${max_iterations:-3}"
        notes=$(jq_get "notes")

        local new_iteration=$((iteration + 1))

        if [[ "${max_iterations}" -gt 0 && "${new_iteration}" -ge "${max_iterations}" ]]; then
            log "Max iterations reached (${new_iteration}/${max_iterations}). Not restarting."
            exit 1
        fi

        jq_set "iteration" "${new_iteration}"
        jq_set "status" "running"

        log "Cycle complete. Next check in ${next_check_delay}s (iteration ${new_iteration})."
        [[ -n "${notes}" ]] && log "Previous cycle: ${notes}"

        command -v claude >/dev/null 2>&1 || die "claude CLI is not installed"

        local loop_flag="" yolo_flag="" skip_flag=""
        [[ "${max_iterations}" -eq 0 ]] && loop_flag=" --infinite-loop"
        [[ "$(jq_get "yolo_mode")" == "true" ]] && yolo_flag=" --yolo"
        [[ "$(jq_get "skip_users")" == "true" ]] && skip_flag=" --skip-users"

        (
            sleep "${next_check_delay}"
            PR_MONITOR_STATE="${PR_MONITOR_STATE}" claude -p "/pr-review:yolo-agent ${pr_url}${loop_flag}${yolo_flag}${skip_flag}" \
                > "/tmp/pr-review-yolo-agent-pr$(echo "${pr_url}" | grep -oP '[0-9]+$')-iter-${new_iteration}.log" 2>&1
        ) &
        disown

        exit 0
    fi

    if [[ "${status}" == "running" ]]; then
        local iteration max_iterations notes
        iteration=$(jq_get "iteration")
        iteration="${iteration:-0}"
        max_iterations=$(jq_get "max_iterations")
        max_iterations="${max_iterations:-3}"
        notes=$(jq_get "notes")

        local new_iteration=$((iteration + 1))

        if [[ "${max_iterations}" -gt 0 && "${new_iteration}" -ge "${max_iterations}" ]]; then
            log "Max iterations reached during crash recovery (${new_iteration}/${max_iterations}). Not restarting."
            exit 1
        fi

        jq_set "iteration" "${new_iteration}"

        log "Unexpected exit. Crash restart (iteration ${new_iteration})."
        [[ -n "${notes}" ]] && log "Previous cycle: ${notes}"

        command -v claude >/dev/null 2>&1 || die "claude CLI is not installed"

        local loop_flag="" yolo_flag="" skip_flag=""
        [[ "${max_iterations}" -eq 0 ]] && loop_flag=" --infinite-loop"
        [[ "$(jq_get "yolo_mode")" == "true" ]] && yolo_flag=" --yolo"
        [[ "$(jq_get "skip_users")" == "true" ]] && skip_flag=" --skip-users"

        PR_MONITOR_STATE="${PR_MONITOR_STATE}" nohup claude -p "/pr-review:yolo-agent ${pr_url}${loop_flag}${yolo_flag}${skip_flag}" \
            > "/tmp/pr-review-yolo-agent-pr$(echo "${pr_url}" | grep -oP '[0-9]+$')-crash-${new_iteration}.log" 2>&1 &

        exit 0
    fi

    log "Unknown status: ${status}"
    exit 3
}

main
