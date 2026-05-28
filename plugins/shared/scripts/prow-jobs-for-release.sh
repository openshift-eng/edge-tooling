#!/usr/bin/bash
set -euo pipefail

# Prow Jobs Analyzer — shared across components.
# Output: JSON array of job objects on stdout
# Progress/errors: stderr

PROW_URL="https://prow.ci.openshift.org/data.js"

# Fetch all jobs matching a component for a release, return latest run per job as JSON
fetch_latest_per_job() {
    local release="${1}"
    local component="${2}"
    curl -s --max-time 300 --retry 3 --retry-delay 5 --compressed "${PROW_URL}" | jq --arg release "${release}" --arg component "${component}" '
        [.[] | select((.job | contains($component)) and (.job | contains($release)))] |
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
    echo "Usage: ${0} [--mode MODE] <component> <release>" >&2
    echo "  --mode MODE: Operation mode (default: failed)" >&2
    echo "    status: Latest run status for each job" >&2
    echo "    failed: Only jobs with failure status" >&2
    echo "  component: Component name used to filter jobs (e.g., microshift, lvm-operator)" >&2
    echo "  release: OpenShift release version (e.g., 4.22, main)" >&2
    exit 1
}

main() {
    local mode="failed"
    local positional=()

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --mode)
                [[ ${#} -lt 2 ]] && { echo "Error: mode requires an argument" >&2; usage; }
                mode="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *) positional+=("${1}"); shift ;;
        esac
    done

    [[ ${#positional[@]} -lt 2 ]] && { echo "Error: component and release arguments are required" >&2; usage; }
    local component="${positional[0]}"
    local release="${positional[1]}"

    case "${mode}" in
        status) fetch_latest_per_job "${release}" "${component}" ;;
        failed) fetch_latest_per_job "${release}" "${component}" | jq '[.[] | select(.status == "failure")]' ;;
        *) echo "Error: Unknown mode '${mode}'" >&2; usage ;;
    esac
}

main "${@}"
