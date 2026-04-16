#!/bin/bash
set -euo pipefail

# Prow Jobs Analyzer
# Fetches periodic job status from the Prow data.js API
# Output: JSON array of job objects on stdout
# Progress/errors: stderr
#
# Usage:
#   prow-jobs-for-release.sh --filter microshift 4.22
#   prow-jobs-for-release.sh --filter lvm 4.22
#   prow-jobs-for-release.sh --filter lvm --mode status 4.22

PROW_URL="https://prow.ci.openshift.org/data.js"

# Fetch all jobs matching a filter for a release, return latest run per job as JSON
fetch_latest_per_job() {
    local release="${1}"
    local filter="${2}"
    curl -s --max-time 60 "${PROW_URL}" | jq --arg release "${release}" --arg filter "${filter}" '
        [.[] | select((.job | contains($filter)) and (.job | contains($release)))] |
        group_by(.job) |
        map(sort_by(.started | tonumber) | reverse | first) |
        [.[] | {
            job: .job,
            type: .type,
            status: .state,
            finished: .finished,
            duration: .duration,
            url: .url,
            build_id: .build_id
        }]
    '
}

usage() {
    echo "Usage: ${0} --filter FILTER [--mode MODE] <release>" >&2
    echo "  --filter FILTER: Job name filter (e.g., microshift, lvm)" >&2
    echo "  --mode MODE: Operation mode (default: failed)" >&2
    echo "    status: Latest run status for each job" >&2
    echo "    failed: Only jobs with failure status" >&2
    echo "  release: OpenShift release version (e.g., 4.22, main)" >&2
    exit 1
}

main() {
    local mode="failed"
    local filter=""
    local release=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --mode)
                [[ ${#} -lt 2 ]] && { echo "Error: mode requires an argument" >&2; usage; }
                mode="${2}"; shift 2 ;;
            --filter)
                [[ ${#} -lt 2 ]] && { echo "Error: filter requires an argument" >&2; usage; }
                filter="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *) release="${1}"; shift ;;
        esac
    done

    [[ -z "${filter}" ]] && { echo "Error: --filter is required" >&2; usage; }
    [[ -z "${release}" ]] && { echo "Error: release argument is required" >&2; usage; }

    case "${mode}" in
        status) fetch_latest_per_job "${release}" "${filter}" ;;
        failed) fetch_latest_per_job "${release}" "${filter}" | jq '[.[] | select(.status == "failure")]' ;;
        *) echo "Error: Unknown mode '${mode}'" >&2; usage ;;
    esac
}

main "${@}"
