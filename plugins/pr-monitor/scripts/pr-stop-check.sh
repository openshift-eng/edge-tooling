#!/usr/bin/bash
set -euo pipefail

# Stop hook decision script: check whether to restart a pr-monitor session.
# Exit codes: 0=restarted, 1=done or max reached, 2=not a pr-monitor session, 3=internal error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
    echo "Error: $1" >&2
    exit 3
}

log() {
    echo "[pr-monitor-stop] $1" >&2
}

main() {
    local state="${PR_MONITOR_STATE:-}"
    if [[ -z "${state}" ]]; then
        exit 2
    fi

    # Parse fields from state string
    local pr_url restart_count max_restarts addressed
    pr_url=$(echo "${state}" | tr ';' '\n' | grep '^pr_url=' | cut -d'=' -f2-)
    restart_count=$(echo "${state}" | tr ';' '\n' | grep '^restart_count=' | cut -d'=' -f2- || true)
    max_restarts=$(echo "${state}" | tr ';' '\n' | grep '^max_restarts=' | cut -d'=' -f2- || true)
    addressed=$(echo "${state}" | tr ';' '\n' | grep '^addressed=' | cut -d'=' -f2- || true)

    restart_count="${restart_count:-0}"
    max_restarts="${max_restarts:-3}"

    if [[ "${restart_count}" -ge "${max_restarts}" ]]; then
        log "Max restarts reached (${restart_count}/${max_restarts})"
        exit 1
    fi

    # Check current PR status via sibling scripts
    local checks_json="" comments_json=""
    local checks_exit=0 comments_exit=0

    checks_json=$(bash "${SCRIPT_DIR}/pr-checks.sh" "${pr_url}" 2>/dev/null) || checks_exit=$?
    comments_json=$(bash "${SCRIPT_DIR}/pr-comments.sh" "${pr_url}" "${addressed}" 2>/dev/null) || comments_exit=$?

    # Extract counts, defaulting to 0 on error
    local failed=0 pending=0 new_comments=0

    if [[ "${checks_exit}" -eq 0 || "${checks_exit}" -eq 1 || "${checks_exit}" -eq 2 ]]; then
        failed=$(echo "${checks_json}" | jq -r '.summary.failed // 0' 2>/dev/null) || failed=0
        pending=$(echo "${checks_json}" | jq -r '.summary.pending // 0' 2>/dev/null) || pending=0
    fi

    if [[ "${comments_exit}" -eq 0 || "${comments_exit}" -eq 1 ]]; then
        new_comments=$(echo "${comments_json}" | jq -r '.summary.total_new // 0' 2>/dev/null) || new_comments=0
    fi

    # All green — no reason to restart
    if [[ "${failed}" -eq 0 && "${pending}" -eq 0 && "${new_comments}" -eq 0 ]]; then
        log "All CI green and no new comments. PR is ready."
        exit 1
    fi

    # Build reason string from non-zero counts
    local reasons=()
    [[ "${failed}" -gt 0 ]] && reasons+=("${failed} failed checks")
    [[ "${pending}" -gt 0 ]] && reasons+=("${pending} pending checks")
    [[ "${new_comments}" -gt 0 ]] && reasons+=("${new_comments} new comments")
    local reason
    reason=$(IFS=', '; echo "${reasons[*]}")

    # Increment restart count and update state
    local new_restart_count=$((restart_count + 1))
    local new_state
    new_state=$(echo "${state}" | sed "s/restart_count=${restart_count}/restart_count=${new_restart_count}/")

    log "Restarting (${new_restart_count}/${max_restarts}): ${reason}"

    # Spawn new claude session in background
    PR_MONITOR_STATE="${new_state}" nohup claude -p "/pr-monitor:watch ${pr_url}" \
        > "/tmp/pr-monitor-restart-${new_restart_count}.log" 2>&1 &

    exit 0
}

main
