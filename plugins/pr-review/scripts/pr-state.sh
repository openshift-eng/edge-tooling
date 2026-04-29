#!/usr/bin/bash
set -euo pipefail

# Manage PR monitor state as JSON via the PR_MONITOR_STATE environment variable.
# Exit codes: 0=success, 3=error

die() {
    echo "Error: $1" >&2
    exit 3
}

require_state() {
    if [[ -z "${PR_MONITOR_STATE:-}" ]]; then
        die "PR_MONITOR_STATE is not set"
    fi
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <subcommand> [args...]"

    local subcommand="$1"
    shift

    case "${subcommand}" in
        init)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") init <pr-url> [max-iterations]"
            if [[ $# -ge 2 && ! "$2" =~ ^[0-9]+$ ]]; then
                die "max-iterations must be a non-negative integer, got: $2"
            fi
            jq -nc \
                --arg url "$1" \
                --argjson max "${2:-3}" \
                '{pr_url:$url,iteration:0,max_iterations:$max,cycle:0,addressed:[],analyzed:[],status:"running",notes:"",next_check_delay:0,last_push_cycle:0}'
            ;;
        save)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") save <pr-number>"
            require_state
            printf '%s' "${PR_MONITOR_STATE}" > "/tmp/pr-review-yolo-agent-$1.json"
            echo "${PR_MONITOR_STATE}"
            ;;
        load)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") load <pr-number>"
            local state_file="/tmp/pr-review-yolo-agent-$1.json"
            [[ -f "${state_file}" ]] || die "No state file found at ${state_file}"
            cat "${state_file}"
            ;;
        clean)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") clean <pr-number>"
            rm -f "/tmp/pr-review-yolo-agent-$1.json"
            ;;
        get)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") get <field>"
            require_state
            echo "${PR_MONITOR_STATE}" | jq -r --arg f "$1" '.[$f] // empty | if type == "array" then join(",") else tostring end' \
                || die "Failed to get field '$1'"
            ;;
        set)
            [[ $# -lt 2 ]] && die "Usage: $(basename "$0") set <field> <value>"
            require_state
            echo "${PR_MONITOR_STATE}" | jq -c --arg f "$1" --arg v "$2" '.[$f] = ($v | try tonumber // $v)' \
                || die "Failed to set field '$1'"
            ;;
        add-addressed)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") add-addressed <comment-id>"
            require_state
            echo "${PR_MONITOR_STATE}" | jq -c --arg id "$1" '.addressed += [($id | try tonumber // $id)]' \
                || die "Failed to add addressed ID '$1'"
            ;;
        add-analyzed)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") add-analyzed <job-key>"
            require_state
            echo "${PR_MONITOR_STATE}" | jq -c --arg key "$1" '.analyzed += [$key]' \
                || die "Failed to add analyzed key '$1'"
            ;;
        increment)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") increment <field>"
            require_state
            echo "${PR_MONITOR_STATE}" | jq -c --arg f "$1" 'if .[$f] | type != "number" then error("field is not numeric") else .[$f] += 1 end' \
                || die "Failed to increment field '$1'"
            ;;
        set-notes)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") set-notes <text>"
            require_state
            echo "${PR_MONITOR_STATE}" | jq -c --arg v "$1" '.notes = $v' \
                || die "Failed to set notes"
            ;;
        set-status)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") set-status <running|complete|waiting>"
            require_state
            case "$1" in
                running|complete|waiting) ;;
                *) die "Invalid status: $1 (expected running|complete|waiting)" ;;
            esac
            echo "${PR_MONITOR_STATE}" | jq -c --arg v "$1" '.status = $v' \
                || die "Failed to set status to '$1'"
            ;;
        decode)
            require_state
            echo "${PR_MONITOR_STATE}" | jq . \
                || die "Failed to decode state"
            ;;
        *)
            die "Unknown subcommand: ${subcommand}"
            ;;
    esac
}

main "$@"
