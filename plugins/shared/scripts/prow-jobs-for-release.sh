#!/usr/bin/bash
set -euo pipefail

# Prow Jobs Analyzer — shared across components.
# Output: JSON array of job objects on stdout
# Progress/errors: stderr

PROW_URL="https://prow.ci.openshift.org/data.js"

# Maximum age (in hours) for non-presubmit jobs.  Jobs whose .finished
# timestamp is older than this are dropped from results.  Presubmit (PR)
# jobs are always kept regardless of age.
MAX_AGE_HOURS=168  # 1 week

# Fetch all jobs matching a component for a release, return latest run per job as JSON.
# When stale_output_file is set (via --stale-output), filtered-out jobs are written there.
fetch_latest_per_job() {
    local release="${1}"
    local component="${2}"
    local stale_output="${3:-}"

    # parse_ts: convert a timestamp value to epoch seconds.
    # Prow's data.js uses numeric strings for most fields but some entries
    # carry ISO 8601 date strings (e.g. "2026-09-09T08:00:00Z").
    local parse_ts='def parse_ts: if . == null or . == "" then 0
        elif test("^[0-9]+(\\.[0-9]+)?$") then tonumber
        else fromdateiso8601 end;'

    local all_jobs
    all_jobs=$(curl -s --max-time 300 --retry 3 --retry-delay 5 --compressed "${PROW_URL}" | jq --arg release "${release}" --arg component "${component}" "${parse_ts}"'
        [.[] | select((.job | contains($component)) and (.job | contains($release)))] |
        group_by(.job) |
        map(sort_by(.started | parse_ts) | reverse | first) |
        [.[] | {
            job: .job,
            type: .type,
            status: .state,
            finished: .finished,
            duration: .duration,
            url: .url,
            build_id: .build_id
        }]
    ')

    local filtered
    filtered=$(echo "${all_jobs}" | jq --argjson max_age_hours "${MAX_AGE_HOURS}" "${parse_ts}"'
        [.[] | select(
            .type == "presubmit" or
            ((.finished | parse_ts) >= (now - ($max_age_hours * 3600)))
        )]
    ')

    # Write stale (filtered-out) jobs to a sidecar file when requested.
    if [[ -n "${stale_output}" ]]; then
        echo "${all_jobs}" | jq --argjson max_age_hours "${MAX_AGE_HOURS}" "${parse_ts}"'
            [.[] | select(
                (.finished | parse_ts) < (now - ($max_age_hours * 3600))
            ) | . + {reason: "stale (>1w)"}]
        ' > "${stale_output}"
    fi

    echo "${filtered}"
}

usage() {
    echo "Usage: ${0} [--mode MODE] [--stale-output PATH] <component> <release>" >&2
    echo "  --mode MODE: Operation mode (default: failed)" >&2
    echo "    status: Latest run status for each job" >&2
    echo "    failed: Only jobs with failure status" >&2
    echo "  --stale-output PATH: Write filtered-out stale jobs to PATH as JSON" >&2
    echo "  component: Component name used to filter jobs (e.g., microshift, lvm-operator)" >&2
    echo "  release: OpenShift release version (e.g., 4.22, main)" >&2
    exit 1
}

main() {
    local mode="failed"
    local stale_output=""
    local positional=()

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --mode)
                [[ ${#} -lt 2 ]] && { echo "Error: mode requires an argument" >&2; usage; }
                mode="${2}"; shift 2 ;;
            --stale-output)
                [[ ${#} -lt 2 ]] && { echo "Error: --stale-output requires a path argument" >&2; usage; }
                stale_output="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *) positional+=("${1}"); shift ;;
        esac
    done

    [[ ${#positional[@]} -lt 2 ]] && { echo "Error: component and release arguments are required" >&2; usage; }
    local component="${positional[0]}"
    local release="${positional[1]}"

    case "${mode}" in
        status) fetch_latest_per_job "${release}" "${component}" "${stale_output}" ;;
        failed) fetch_latest_per_job "${release}" "${component}" "${stale_output}" | jq '[.[] | select(.status == "failure")]' ;;
        *) echo "Error: Unknown mode '${mode}'" >&2; usage ;;
    esac
}

main "${@}"
