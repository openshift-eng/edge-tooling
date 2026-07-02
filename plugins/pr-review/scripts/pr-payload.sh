#!/usr/bin/bash
set -euo pipefail

# Detect PR payload test runs, discover appropriate payload jobs, and triage
# failures via payload-monitor.
#
# Three-phase flow:
#   Phase 1: Discovery — find which payload jobs match the PR's changed files
#   Phase 2: Validation — check if existing /payload-job comments match discovery
#   Phase 3: Triage — analyze payload test results if a run exists
#
# Exit codes: 0=results found (triage or discovery), 1=no matches, 3=error

URL_PATTERN='^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/pull/[0-9]+$'
PAYLOAD_URL_PATTERN='https://pr-payload-tests\.ci\.openshift\.org/runs/ci/[^ )"'"'"']*'

die() {
    echo "Error: $1" >&2
    exit 3
}

check_dependencies() {
    command -v gh >/dev/null 2>&1 || die "gh CLI is not installed"
    command -v jq >/dev/null 2>&1 || die "jq is not installed"
    command -v python3 >/dev/null 2>&1 || die "python3 is not installed"
    command -v gsutil >/dev/null 2>&1 || die "gsutil is not installed"
    gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login'"
}

validate_url() {
    local url="$1"
    if [[ ! "${url}" =~ ${URL_PATTERN} ]]; then
        die "Invalid PR URL: ${url}"
    fi
}

parse_url() {
    local url="$1"
    ORG=$(echo "${url}" | cut -d'/' -f4)
    REPO=$(echo "${url}" | cut -d'/' -f5)
    PR_NUMBER=$(echo "${url}" | cut -d'/' -f7)
}

find_payload_monitor_dir() {
    if [[ -n "${PAYLOAD_MONITOR_DIR:-}" ]]; then
        if [[ -d "${PAYLOAD_MONITOR_DIR}" ]]; then
            echo "${PAYLOAD_MONITOR_DIR}"
            return
        fi
        die "PAYLOAD_MONITOR_DIR set but does not exist: ${PAYLOAD_MONITOR_DIR}"
    fi

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    local candidates=(
        "${script_dir}/../../../payload-monitor"
        "${PWD}/repos/edge-tooling/payload-monitor"
    )

    local base_dirs=("${HOME}/Documents/Projects" "${HOME}/Projects" "${HOME}/src")
    for base in "${base_dirs[@]}"; do
        candidates+=("${base}/edge-tooling/payload-monitor")
    done

    for candidate in "${candidates[@]}"; do
        if [[ -d "${candidate}" ]]; then
            echo "$(cd "${candidate}" && pwd)"
            return
        fi
    done

    die "Cannot find payload-monitor directory. Set PAYLOAD_MONITOR_DIR or run from edge-tooling repo."
}

fetch_pr_comments() {
    local org="$1" repo="$2" pr_number="$3"

    gh api "repos/${org}/${repo}/issues/${pr_number}/comments" \
        --paginate 2>/dev/null \
        || die "Failed to fetch PR comments for ${org}/${repo}#${pr_number}"
}

fetch_latest_payload_url() {
    local comments_json="$1"

    echo "${comments_json}" | jq -r '.[].body' 2>/dev/null \
        | grep -oE "${PAYLOAD_URL_PATTERN}" | tail -1
}

fetch_trigger_command() {
    local comments_json="$1"

    echo "${comments_json}" | jq -r '.[].body' 2>/dev/null \
        | grep -oE '^/payload-job .+' | tail -1
}

fetch_payload_comment_timestamp() {
    local comments_json="$1"

    echo "${comments_json}" | jq -r '
        [.[] | select(.body | test("^/payload-job "))] | last | .created_at // empty
    ' 2>/dev/null
}

fetch_latest_commit_timestamp() {
    local org="$1" repo="$2" pr_number="$3"

    gh pr view "${pr_number}" --repo "${org}/${repo}" \
        --json commits --jq '.commits[-1].committedDate' 2>/dev/null
}

run_discovery() {
    local pr_ref="$1" pm_dir="$2"

    local discovery_output
    discovery_output=$(cd "${pm_dir}" && python3 -m payload_monitor \
        --discover --pr "${pr_ref}" --format json 2>/dev/null) || return 1

    echo "${discovery_output}" | jq -c '.' >/dev/null 2>&1 || return 1
    echo "${discovery_output}"
}

run_triage() {
    local payload_url="$1" pr_ref="$2" pm_dir="$3"

    local triage_output
    triage_output=$(cd "${pm_dir}" && python3 -m payload_monitor \
        --pr-payload-url "${payload_url}" \
        --pr "${pr_ref}" \
        --format json 2>/dev/null) \
        || die "payload-monitor triage failed"

    echo "${triage_output}" | jq -c '.' >/dev/null 2>&1 \
        || die "payload-monitor returned invalid JSON"

    echo "${triage_output}"
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <github-pr-url>"

    local pr_url="$1"

    check_dependencies
    validate_url "${pr_url}"
    parse_url "${pr_url}"

    local pm_dir
    pm_dir=$(find_payload_monitor_dir)

    local pr_ref="${ORG}/${REPO}#${PR_NUMBER}"

    # Fetch comments once — reused by discovery, validation, and triage
    local comments
    comments=$(fetch_pr_comments "${ORG}" "${REPO}" "${PR_NUMBER}")

    # --- Phase 1: Discovery ---
    local discovery_json=""
    discovery_json=$(run_discovery "${pr_ref}" "${pm_dir}" 2>/dev/null || true)

    # --- Phase 2: Validate existing /payload-job comments ---
    local existing_cmd validation_json=""
    existing_cmd=$(fetch_trigger_command "${comments}" 2>/dev/null || true)
    if [[ -n "${existing_cmd}" && -n "${discovery_json}" ]]; then
        local suggestion_names
        suggestion_names=$(echo "${discovery_json}" | jq -r '.suggestions[].as_name' 2>/dev/null || true)

        local job_name
        job_name=$(echo "${existing_cmd}" | sed 's|^/payload-job ||')

        local is_valid="false"
        local note=""
        while IFS= read -r name; do
            if [[ -n "${name}" && "${job_name}" == *"${name}"* ]]; then
                is_valid="true"
                note="Matches discovered job ${name}"
                break
            fi
        done <<< "${suggestion_names}"

        if [[ "${is_valid}" == "false" ]]; then
            note="Does not match any discovered job for the changed files"
        fi

        validation_json=$(jq -n \
            --arg cmd "${existing_cmd}" \
            --argjson valid "${is_valid}" \
            --arg note "${note}" \
            '{existing_command: $cmd, is_valid: $valid, note: $note}')
    fi

    # --- Phase 3: Triage (if payload URL exists) ---
    local payload_url
    payload_url=$(fetch_latest_payload_url "${comments}" 2>/dev/null || true)

    if [[ -n "${payload_url}" ]]; then
        # Staleness check — compare payload comment time vs latest commit
        local is_stale="false"
        local stale_reason=""
        local payload_comment_ts latest_commit_ts
        payload_comment_ts=$(fetch_payload_comment_timestamp "${comments}" 2>/dev/null || true)
        latest_commit_ts=$(fetch_latest_commit_timestamp "${ORG}" "${REPO}" "${PR_NUMBER}" 2>/dev/null || true)

        if [[ -n "${payload_comment_ts}" && -n "${latest_commit_ts}" ]]; then
            if [[ "${latest_commit_ts}" > "${payload_comment_ts}" ]]; then
                is_stale="true"
                stale_reason="New commit pushed after payload was triggered (commit: ${latest_commit_ts}, trigger: ${payload_comment_ts})"
            fi
        fi

        # Triage mode — we have actual payload run results
        local triage_json
        triage_json=$(run_triage "${payload_url}" "${pr_ref}" "${pm_dir}")

        # Build combined output in a single jq call
        local disc_arg="null"
        if [[ -n "${discovery_json}" ]]; then
            disc_arg=$(echo "${discovery_json}" | jq '{suggestions: .suggestions, version: .version}')
        fi
        local val_arg="null"
        if [[ -n "${validation_json}" ]]; then
            val_arg="${validation_json}"
        fi

        echo "${triage_json}" | jq \
            --arg mode "triage" \
            --argjson stale "${is_stale}" \
            --arg stale_reason "${stale_reason}" \
            --argjson disc "${disc_arg}" \
            --argjson val "${val_arg}" \
            --arg cmd "${existing_cmd}" \
            '. + {mode: $mode, stale: $stale}
            | if $stale then . + {stale_reason: $stale_reason} else . end
            | if $disc != null then . + {discovery: $disc} else . end
            | if $val != null then . + {validation: $val} else . end
            | if $cmd != "" then . + {trigger_command: $cmd} else . end'
        exit 0
    fi

    # No payload URL — discovery-only mode
    if [[ -n "${discovery_json}" ]]; then
        local suggestion_count
        suggestion_count=$(echo "${discovery_json}" | jq '.suggestions | length' 2>/dev/null || echo "0")

        if [[ "${suggestion_count}" -gt 0 ]]; then
            # Discovery found matching jobs
            local output
            output=$(echo "${discovery_json}" | jq '. + {mode: "discovery"}')
            echo "${output}"
            exit 0
        fi
    fi

    # Nothing found — no payload URL, no matching jobs
    echo '{"found": false, "mode": "none", "reason": "No payload URL in comments and no matching payload jobs discovered"}'
    exit 1
}

main "$@"
