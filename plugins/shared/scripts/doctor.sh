#!/usr/bin/bash
set -euo pipefail

# Deterministic orchestration for CI doctor workflows.
# Shared across components (MicroShift, LVMS, etc.) via symlinks in each
# plugin's scripts/ directory.
#
# Two phases called by the doctor skill with LLM steps in between:
#
#   doctor.sh prepare --component <component> --workdir DIR <releases> [--rebase]
#     - Collects failed jobs for each release
#     - Downloads all artifacts in parallel
#     - Writes per-release and PR jobs JSON files to ${WORKDIR}/jobs/
#
#   doctor.sh graphs --component <component> --workdir DIR [--timezone TZ]
#     - Generates PCP performance graphs for all jobs with pmlogs
#     - Outputs PNG files to ${WORKDIR}/graphs/<build_id>/
#
#   doctor.sh finalize --component <component> --workdir DIR <releases>
#     - Runs aggregate.py for each release and PRs (reads/writes ${WORKDIR}/jobs/)
#     - Runs create-report.py to generate HTML (reads jobs/ and bugs/)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKDIR=""
COMPONENT=""

# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

cmd_prepare() {
    local releases_arg=""
    local do_rebase=false

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) WORKDIR="${2}"; shift 2 ;;
            --component) COMPONENT="${2}"; shift 2 ;;
            --rebase) do_rebase=true; shift ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) releases_arg="${1}"; shift ;;
        esac
    done

    [[ -z "${COMPONENT}" ]] && { echo "Error: --component is required" >&2; return 1; }

    WORKDIR="${WORKDIR:-/tmp/${COMPONENT}-ci-claude-workdir.$(date +%y%m%d)}"

    if [[ -z "${releases_arg}" ]]; then
        echo "Error: releases argument required" >&2
        echo "Usage: $(basename "$0") prepare --component <component> [--workdir DIR] <release1,release2,...> [--rebase]" >&2
        return 1
    fi

    mkdir -p "${WORKDIR}/jobs"

    IFS=',' read -ra RELEASES <<< "${releases_arg}"
    local total_jobs=0
    declare -A release_errors

    # Collect and download for each release
    for release in "${RELEASES[@]}"; do
        release=$(echo "${release}" | xargs)  # trim whitespace
        echo "=== Release ${release} ===" >&2

        local jobs_file="${WORKDIR}/jobs/release-${release}-jobs.json"

        echo "  Collecting failed periodic jobs..." >&2
        local raw_json raw_err
        raw_err=$(mktemp)
        if ! raw_json=$(bash "${SCRIPT_DIR}/prow-jobs-for-release.sh" "${COMPONENT}" "${release}" 2>"${raw_err}"); then
            echo "  ERROR: failed to collect jobs for release ${release}:" >&2
            local err_msg
            err_msg=$(cat "${raw_err}")
            echo "${err_msg}" >&2
            rm -f "${raw_err}"
            echo "[]" > "${jobs_file}"
            release_errors["${release}"]="${err_msg:-data collection failed}"
            echo "${release_errors["${release}"]}" > "${WORKDIR}/jobs/release-${release}-error.txt"
            continue
        fi
        rm -f "${raw_err}"

        local filtered_json
        filtered_json=$(echo "${raw_json}" | jq '[.[] | select(.type == "periodic")]')

        local count
        count=$(echo "${filtered_json}" | jq 'length')

        if [[ "${count}" -eq 0 ]]; then
            echo "  No failed periodic jobs found" >&2
            echo "[]" > "${jobs_file}"
            continue
        fi

        echo "  Found ${count} failed periodic jobs, downloading artifacts..." >&2
        local dl_err
        dl_err=$(mktemp)
        echo "${filtered_json}" | \
            bash "${SCRIPT_DIR}/download-jobs.sh" --workdir "${WORKDIR}" 2>"${dl_err}" \
            > "${jobs_file}"
        [[ -s "${dl_err}" ]] && cat "${dl_err}" >&2
        rm -f "${dl_err}"

        total_jobs=$((total_jobs + count))
        echo "  Done: ${jobs_file}" >&2
    done

    # Collect and download for rebase PRs
    if ${do_rebase}; then
        echo "=== Rebase Pull Requests ===" >&2

        local prs_file="${WORKDIR}/jobs/prs-jobs.json"
        local prs_status_file="${WORKDIR}/jobs/prs-status.json"

        echo "  Collecting rebase PRs..." >&2
        local pr_json pr_err
        pr_err=$(mktemp)
        local prs_error=""
        if ! pr_json=$(bash "${SCRIPT_DIR}/prow-jobs-for-pull-requests.sh" \
            --mode detail --author "microshift-rebase-script[bot]" 2>"${pr_err}"); then
            echo "  ERROR: failed to collect rebase PRs:" >&2
            prs_error=$(cat "${pr_err}")
            echo "${prs_error}" >&2
            rm -f "${pr_err}"
            echo "[]" > "${prs_file}"
            echo "[]" > "${prs_status_file}"
            echo "${prs_error:-rebase PR collection failed}" > "${WORKDIR}/jobs/prs-error.txt"
        else
            rm -f "${pr_err}"

            local pr_count
            pr_count=$(echo "${pr_json}" | jq 'length')

            if [[ "${pr_count}" -eq 0 ]]; then
                echo "  No rebase PRs found" >&2
                echo "[]" > "${prs_file}"
                echo "[]" > "${prs_status_file}"
            else
                # Save job status snapshot for all PRs (used by HTML report)
                echo "${pr_json}" | jq '[.[] | {
                    pr_number, title, url,
                    passed:  [.jobs[] | select(.status == "SUCCESS")] | length,
                    failed:  [.jobs[] | select(.status == "FAILURE")] | length,
                    pending: [.jobs[] | select(.status != "SUCCESS" and .status != "FAILURE")] | length,
                    total:   (.jobs | length)
                }]' > "${prs_status_file}"
                echo "  Saved status for ${pr_count} rebase PRs" >&2

                # Filter to PRs with failed jobs for artifact download
                local failed_prs
                failed_prs=$(echo "${pr_json}" | \
                    jq '[.[] | select(.jobs | map(select(.status == "FAILURE")) | length > 0)]')

                local failed_pr_count
                failed_pr_count=$(echo "${failed_prs}" | jq 'length')

                if [[ "${failed_pr_count}" -eq 0 ]]; then
                    echo "  No PRs with failures to investigate" >&2
                    echo "[]" > "${prs_file}"
                else
                    local job_count
                    job_count=$(echo "${failed_prs}" | jq '[.[].jobs[] | select(.status == "FAILURE")] | length')

                    echo "  Downloading artifacts for ${job_count} failed jobs across ${failed_pr_count} PRs..." >&2
                    local dl_err
                    dl_err=$(mktemp)
                    echo "${failed_prs}" | \
                        bash "${SCRIPT_DIR}/download-jobs.sh" --workdir "${WORKDIR}" 2>"${dl_err}" \
                        > "${prs_file}"
                    [[ -s "${dl_err}" ]] && cat "${dl_err}" >&2
                    rm -f "${dl_err}"

                    total_jobs=$((total_jobs + job_count))
                    echo "  Done: ${prs_file}" >&2
                fi
            fi
        fi
    fi

    echo "" >&2
    echo "Prepare complete: ${total_jobs} total jobs ready for analysis in ${WORKDIR}" >&2

    # Output a JSON summary for the LLM to consume
    local releases_json="[]"
    for release in "${RELEASES[@]}"; do
        release=$(echo "${release}" | xargs)
        local jobs_file="${WORKDIR}/jobs/release-${release}-jobs.json"
        local count=0
        if [[ -f "${jobs_file}" ]]; then
            count=$(jq 'length' "${jobs_file}")
        fi
        local error="${release_errors["${release}"]:-}"
        if [[ -n "${error}" ]]; then
            releases_json=$(echo "${releases_json}" | jq \
                --arg r "${release}" --argjson c "${count}" --arg f "${jobs_file}" --arg e "${error}" \
                '. + [{release: $r, jobs: $c, jobs_file: $f, error: $e}]')
        else
            releases_json=$(echo "${releases_json}" | jq \
                --arg r "${release}" --argjson c "${count}" --arg f "${jobs_file}" \
                '. + [{release: $r, jobs: $c, jobs_file: $f}]')
        fi
    done

    local result
    result=$(jq -n --arg w "${WORKDIR}" --argjson rel "${releases_json}" \
        '{workdir: $w, releases: $rel}')

    if ${do_rebase}; then
        local prs_file="${WORKDIR}/jobs/prs-jobs.json"
        local pr_job_count=0
        if [[ -f "${prs_file}" ]]; then
            pr_job_count=$(jq 'length' "${prs_file}")
        fi
        if [[ -n "${prs_error:-}" ]]; then
            result=$(echo "${result}" | jq \
                --argjson c "${pr_job_count}" --arg f "${prs_file}" --arg e "${prs_error}" \
                '. + {prs: {jobs: $c, jobs_file: $f, error: $e}}')
        else
            result=$(echo "${result}" | jq \
                --argjson c "${pr_job_count}" --arg f "${prs_file}" \
                '. + {prs: {jobs: $c, jobs_file: $f}}')
        fi
    fi

    echo "${result}"
}

# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

cmd_finalize() {
    local releases_arg=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) WORKDIR="${2}"; shift 2 ;;
            --component) COMPONENT="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) releases_arg="${1}"; shift ;;
        esac
    done

    [[ -z "${COMPONENT}" ]] && { echo "Error: --component is required" >&2; return 1; }

    WORKDIR="${WORKDIR:-/tmp/${COMPONENT}-ci-claude-workdir.$(date +%y%m%d)}"

    if [[ -z "${releases_arg}" ]]; then
        echo "Error: releases argument required" >&2
        echo "Usage: $(basename "$0") finalize --component <component> [--workdir DIR] <release1,release2,...>" >&2
        return 1
    fi

    IFS=',' read -ra RELEASES <<< "${releases_arg}"

    # Aggregate each release
    for release in "${RELEASES[@]}"; do
        release=$(echo "${release}" | xargs)
        echo "=== Aggregating release ${release} ===" >&2
        python3 "${SCRIPT_DIR}/aggregate.py" \
            --release "${release}" --workdir "${WORKDIR}" >/dev/null || \
            echo "  WARNING: aggregation failed for ${release}" >&2
    done

    # Aggregate PRs (if job files exist)
    local pr_files
    pr_files=$(find "${WORKDIR}/jobs" -name 'prs-job-*.txt' 2>/dev/null | head -1)
    if [[ -n "${pr_files}" ]]; then
        echo "=== Aggregating PRs ===" >&2
        python3 "${SCRIPT_DIR}/aggregate.py" \
            --prs --workdir "${WORKDIR}" >/dev/null || \
            echo "  WARNING: PR aggregation failed" >&2
    fi

    # Generate HTML report
    echo "=== Generating HTML report ===" >&2
    python3 "${SCRIPT_DIR}/create-report.py" \
        --component "${COMPONENT}" --workdir "${WORKDIR}" "${releases_arg}"
}

# ---------------------------------------------------------------------------
# graphs
# ---------------------------------------------------------------------------

cmd_graphs() {
    local timezone="UTC"

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) WORKDIR="${2}"; shift 2 ;;
            --component) COMPONENT="${2}"; shift 2 ;;
            --timezone) timezone="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) echo "Unknown argument: ${1}" >&2; return 1 ;;
        esac
    done

    [[ -z "${COMPONENT}" ]] && { echo "Error: --component is required" >&2; return 1; }

    WORKDIR="${WORKDIR:-/tmp/${COMPONENT}-ci-claude-workdir.$(date +%y%m%d)}"

    if [[ ! -d "${WORKDIR}/artifacts" ]]; then
        echo "No artifacts found in ${WORKDIR}, skipping graph generation." >&2
        return 0
    fi

    # Check prerequisites
    if ! command -v pcp2json >/dev/null 2>&1; then
        echo "Error: pcp2json not found. Run: sudo dnf install -y pcp-export-pcp2json" >&2
        return 1
    fi
    if ! python3 -c "from pcp import pmapi" 2>/dev/null; then
        echo "Error: pcp Python module not found. Run: pip install pcp" >&2
        return 1
    fi
    if ! python3 -c "import matplotlib" 2>/dev/null; then
        echo "Error: matplotlib not installed. Run: pip install matplotlib" >&2
        return 1
    fi

    echo "=== Generating PCP graphs ===" >&2
    bash "${SCRIPT_DIR}/pcp-graphs/generate-graphs.sh" \
        --workdir "${WORKDIR}" --timezone "${timezone}"
}

# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------

cmd_refresh() {
    local releases_arg=""
    local ignore_keys=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) WORKDIR="${2}"; shift 2 ;;
            --component) COMPONENT="${2}"; shift 2 ;;
            --ignore)
                if [[ -z "${2:-}" || "${2:-}" == -* ]]; then
                    echo "Error: --ignore requires a non-empty argument" >&2; return 1
                fi
                ignore_keys="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) releases_arg="${1}"; shift ;;
        esac
    done

    [[ -z "${COMPONENT}" ]] && { echo "Error: --component is required" >&2; return 1; }

    WORKDIR="${WORKDIR:-/tmp/${COMPONENT}-ci-claude-workdir.$(date +%y%m%d)}"

    if [[ -z "${releases_arg}" ]]; then
        echo "Error: releases argument required" >&2
        echo "Usage: $(basename "$0") refresh --component <component> [--workdir DIR] [--ignore KEY1,KEY2,...] <release1,release2,...>" >&2
        return 1
    fi

    # Generate HTML report (reads existing summary + bug files)
    echo "=== Generating HTML report ===" >&2
    local -a report_args=(--component "${COMPONENT}" --workdir "${WORKDIR}")
    if [[ -n "${ignore_keys}" ]]; then
        report_args+=(--ignore "${ignore_keys}")
    fi
    python3 "${SCRIPT_DIR}/create-report.py" "${report_args[@]}" "${releases_arg}"
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

usage() {
    echo "Usage: $(basename "$0") <command> --component <component> [--workdir DIR] [options]" >&2
    echo "" >&2
    echo "Commands:" >&2
    echo "  prepare  --component C [--workdir DIR] <releases> [--rebase]  Collect jobs and download artifacts" >&2
    echo "  graphs   --component C [--workdir DIR] [--timezone TZ]       Generate PCP performance graphs" >&2
    echo "  finalize --component C [--workdir DIR] <releases>             Aggregate results and generate HTML" >&2
    echo "  refresh  --component C [--workdir DIR] [--ignore KEY1,KEY2,...] <releases>  Regenerate HTML from existing workdir data" >&2
    echo "" >&2
    echo "  --component C: component name (e.g., microshift, lvms)" >&2
    echo "  <releases>: comma-separated release versions (e.g., 4.18,4.19,4.20,main)" >&2
    echo "  --workdir DIR: work directory (default: /tmp/<component>-ci-claude-workdir.YYMMDD)" >&2
    exit 1
}

main() {
    if [[ ${#} -lt 1 ]]; then
        usage
    fi

    local cmd="${1}"
    shift

    case "${cmd}" in
        prepare)  cmd_prepare "${@}" ;;
        graphs)   cmd_graphs "${@}" ;;
        finalize) cmd_finalize "${@}" ;;
        refresh)  cmd_refresh "${@}" ;;
        *) echo "Unknown command: ${cmd}" >&2; usage ;;
    esac
}

main "${@}"
