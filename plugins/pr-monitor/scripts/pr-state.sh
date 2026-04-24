#!/usr/bin/bash
set -euo pipefail

# Manage PR monitor state via the PR_MONITOR_STATE environment variable.
# State format: key=value pairs separated by semicolons.
# Exit codes: 0=success, 3=error

die() {
    echo "Error: $1" >&2
    exit 3
}

get_field() {
    local state="$1" field="$2"
    # Add leading semicolon so every key is preceded by one, simplifying the match
    local normalized=";${state}"
    local value
    value=$(echo "${normalized}" | sed -n "s/.*;${field}=\([^;]*\).*/\1/p")
    echo "${value}"
}

set_field() {
    local state="$1" field="$2" value="$3"
    # Add leading semicolon for uniform matching, then strip it after replacement
    local normalized=";${state}"
    if echo "${normalized}" | grep -qF ";${field}="; then
        normalized=$(echo "${normalized}" | sed "s%;${field}=[^;]*%;${field}=${value}%")
        echo "${normalized#;}"
    else
        echo "${state};${field}=${value}"
    fi
}

init_state() {
    local pr_url="$1"
    echo "pr_url=${pr_url};restart_count=0;cycle=0;addressed=;analyzed=;max_restarts=3;status=running;notes="
}

sanitize_notes() {
    local raw="$1"
    echo "${raw}" | tr ';' ',' | tr '%' '_'
}

add_to_list() {
    local state="$1" field="$2" item="$3"
    local current
    current=$(get_field "${state}" "${field}")
    if [[ -z "${current}" ]]; then
        set_field "${state}" "${field}" "${item}"
    else
        set_field "${state}" "${field}" "${current},${item}"
    fi
}

increment_field() {
    local state="$1" field="$2"
    local current
    current=$(get_field "${state}" "${field}")
    if [[ -z "${current}" || ! "${current}" =~ ^[0-9]+$ ]]; then
        die "Field '${field}' is not a valid integer"
    fi
    local new_value=$((current + 1))
    set_field "${state}" "${field}" "${new_value}"
}

decode_state() {
    local state="$1"
    echo "${state}" | awk -F';' '{
        printf "{\n"
        for (i = 1; i <= NF; i++) {
            split($i, kv, "=")
            key = kv[1]
            value = ""
            for (j = 2; j <= length(kv); j++) {
                if (j > 2) value = value "="
                value = value kv[j]
            }
            gsub(/"/, "\\\"", value)
            if (i < NF) {
                printf "  \"%s\": \"%s\",\n", key, value
            } else {
                printf "  \"%s\": \"%s\"\n", key, value
            }
        }
        printf "}\n"
    }'
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
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") init <pr-url>"
            init_state "$1"
            ;;
        get)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") get <field>"
            require_state
            get_field "${PR_MONITOR_STATE}" "$1"
            ;;
        set)
            [[ $# -lt 2 ]] && die "Usage: $(basename "$0") set <field> <value>"
            require_state
            set_field "${PR_MONITOR_STATE}" "$1" "$2"
            ;;
        add-addressed)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") add-addressed <comment-id>"
            require_state
            add_to_list "${PR_MONITOR_STATE}" "addressed" "$1"
            ;;
        add-analyzed)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") add-analyzed <job-key>"
            require_state
            add_to_list "${PR_MONITOR_STATE}" "analyzed" "$1"
            ;;
        increment)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") increment <field>"
            require_state
            increment_field "${PR_MONITOR_STATE}" "$1"
            ;;
        set-notes)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") set-notes <text>"
            require_state
            local sanitized
            sanitized=$(sanitize_notes "$1")
            set_field "${PR_MONITOR_STATE}" "notes" "${sanitized}"
            ;;
        set-status)
            [[ $# -lt 1 ]] && die "Usage: $(basename "$0") set-status <running|complete>"
            require_state
            case "$1" in
                running|complete) ;;
                *) die "Invalid status: $1 (expected running|complete)" ;;
            esac
            set_field "${PR_MONITOR_STATE}" "status" "$1"
            ;;
        decode)
            require_state
            decode_state "${PR_MONITOR_STATE}"
            ;;
        *)
            die "Unknown subcommand: ${subcommand}"
            ;;
    esac
}

main "$@"
