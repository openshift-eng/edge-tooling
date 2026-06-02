#!/usr/bin/bash
set -euo pipefail

# Download CI Doctor analysis artifacts from a completed prow job.
#
# Fetches the doctor's analysis files (per-job reports, summaries, bug
# mappings, HTML report) into a local workdir so the user can continue
# working with them — re-run finalize, create bugs, inspect reports, etc.
#
# The workdir date is derived from the prow job's start timestamp
# (started.json). If a local workdir for that date already exists the
# script refuses to proceed.
#
# Usage:
#   continue-session.sh <prow-url>
#   continue-session.sh <prow-url> --workdir-base /tmp
#
# Output (stdout): JSON summary of downloaded artifacts
# Progress/errors: stderr

WORKDIR_BASE="/tmp"
ARTIFACTS_SUBPATH="artifacts/microshift-ci-doctor/openshift-edge-tooling-microshift-ci-doctor/artifacts"
DL_ERR=$(mktemp)
trap 'rm -f "${DL_ERR}"' EXIT

url_to_gcs() {
    echo "$1" | sed \
        -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' \
        -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|' \
        -e 's|/$||'
}

usage() {
    echo "Usage: ${0} <prow-url> [--workdir-base DIR]" >&2
    echo "" >&2
    echo "Downloads CI Doctor analysis artifacts from a completed prow job." >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  <prow-url>         Prow job URL or GCS web URL" >&2
    echo "  --workdir-base DIR Base directory for workdir (default: /tmp)" >&2
    exit 1
}

main() {
    local url=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir-base)
                [[ ${#} -lt 2 ]] && { echo "Error: --workdir-base requires a directory" >&2; usage; }
                WORKDIR_BASE="${2}"; shift 2 ;;
            -h|--help) usage ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *)
                if [[ -z "${url}" ]]; then
                    url="${1}"; shift
                else
                    echo "Error: unexpected argument: ${1}" >&2; usage
                fi ;;
        esac
    done

    if [[ -z "${url}" ]]; then
        echo "Error: prow job URL is required" >&2
        usage
    fi

    # Convert URL to GCS path
    local gcs_base
    gcs_base=$(url_to_gcs "${url}")

    if [[ "${gcs_base}" == "${url}" ]]; then
        echo "Error: unrecognized URL format: ${url}" >&2
        echo "Expected a Prow or GCS web URL" >&2
        exit 1
    fi

    echo "=== Fetching job metadata ===" >&2

    # Fetch started.json to derive the job date
    local started_json
    if ! started_json=$(gsutil cat "${gcs_base}/started.json" 2>/dev/null); then
        echo "Error: failed to fetch ${gcs_base}/started.json" >&2
        echo "Is this a valid CI Doctor prow job URL?" >&2
        exit 1
    fi

    local timestamp
    timestamp=$(echo "${started_json}" | jq -r '.timestamp')
    if [[ -z "${timestamp}" ]] || [[ "${timestamp}" == "null" ]]; then
        echo "Error: no timestamp found in started.json" >&2
        exit 1
    fi

    local yymmdd
    yymmdd=$(date -d "@${timestamp}" +%y%m%d)
    local workdir="${WORKDIR_BASE}/microshift-ci-claude-workdir.${yymmdd}"

    echo "  Job date: $(date -d "@${timestamp}" +%Y-%m-%d)" >&2
    echo "  Workdir:  ${workdir}" >&2

    # Fatal error if workdir already exists
    if [[ -d "${workdir}" ]]; then
        echo "" >&2
        echo "Error: workdir already exists: ${workdir}" >&2
        echo "A local doctor session for this date is already present." >&2
        echo "Remove it first if you want to download from CI." >&2
        exit 1
    fi

    # Download the doctor artifacts directory from GCS.
    # Uses gsutil -m cp -r on the whole directory (same pattern as
    # download-jobs.sh) then flattens into the workdir.
    local gcs_artifacts="${gcs_base}/${ARTIFACTS_SUBPATH}"

    mkdir -p "${workdir}"
    echo "" >&2
    echo "=== Downloading artifacts ===" >&2

    # gsutil cp -r .../artifacts/ workdir/ → workdir/artifacts/...
    # so we download into a temp parent and move files up
    local dl_tmp="${workdir}/.dl_tmp"
    mkdir -p "${dl_tmp}"
    if ! gsutil -q -m cp -r "${gcs_artifacts}/" "${dl_tmp}/" 2>"${DL_ERR}"; then
        echo "Error: download failed" >&2
        [[ -s "${DL_ERR}" ]] && cat "${DL_ERR}" >&2
        rm -rf "${dl_tmp}" "${workdir}"
        exit 1
    fi
    [[ -s "${DL_ERR}" ]] && cat "${DL_ERR}" >&2

    # gsutil cp -r creates a subdirectory named "artifacts" inside dl_tmp;
    # move all files up to the workdir root (flat layout)
    local src_dir="${dl_tmp}"
    if [[ -d "${dl_tmp}/artifacts" ]]; then
        src_dir="${dl_tmp}/artifacts"
    fi

    local kept=0
    for f in "${src_dir}"/*; do
        [[ -f "${f}" ]] || continue
        mv "${f}" "${workdir}/"
        kept=$((kept + 1))
    done
    rm -rf "${dl_tmp}"

    if [[ "${kept}" -eq 0 ]]; then
        echo "Error: no files found in ${gcs_artifacts}/" >&2
        echo "This may not be a CI Doctor job, or artifacts were not uploaded." >&2
        rm -rf "${workdir}"
        exit 1
    fi

    echo "  Downloaded ${kept} files to ${workdir}" >&2

    # Build JSON summary by scanning downloaded files
    echo "" >&2
    echo "=== Building summary ===" >&2

    # Discover releases from jobs JSON files
    local releases_json="[]"
    for jobs_file in "${workdir}"/analyze-ci-release-*-jobs.json; do
        [[ -f "${jobs_file}" ]] || continue
        local basename_f
        basename_f=$(basename "${jobs_file}")
        # Extract release from filename: analyze-ci-release-<VERSION>-jobs.json
        local release
        release=$(echo "${basename_f}" | sed 's/analyze-ci-release-//;s/-jobs\.json//')

        local job_reports
        job_reports=$(find "${workdir}" -maxdepth 1 -name "analyze-ci-release-${release}-job-*.txt" | wc -l)
        local has_summary=false
        [[ -f "${workdir}/analyze-ci-release-${release}-summary.json" ]] && has_summary=true
        local has_bugs=false
        [[ -f "${workdir}/analyze-ci-bugs-${release}.json" ]] && has_bugs=true

        releases_json=$(echo "${releases_json}" | jq \
            --arg r "${release}" \
            --argjson jr "${job_reports}" \
            --argjson hs "${has_summary}" \
            --argjson hb "${has_bugs}" \
            '. + [{release: $r, job_reports: $jr, has_summary: $hs, has_bugs: $hb}]')
    done

    # PR info
    local prs_json="null"
    if [[ -f "${workdir}/analyze-ci-prs-jobs.json" ]]; then
        local pr_reports
        pr_reports=$(find "${workdir}" -maxdepth 1 -name "analyze-ci-prs-job-*.txt" | wc -l)
        local pr_has_summary=false
        [[ -f "${workdir}/analyze-ci-prs-summary.json" ]] && pr_has_summary=true
        prs_json=$(jq -n \
            --argjson jr "${pr_reports}" \
            --argjson hs "${pr_has_summary}" \
            '{job_reports: $jr, has_summary: $hs}')
    fi

    # HTML report path
    local html_report="null"
    if [[ -f "${workdir}/microshift-ci-doctor-report.html" ]]; then
        html_report="\"${workdir}/microshift-ci-doctor-report.html\""
    fi

    # Final JSON
    jq -n \
        --arg workdir "${workdir}" \
        --arg source_url "${url}" \
        --argjson releases "${releases_json}" \
        --argjson prs "${prs_json}" \
        --argjson html_report "${html_report}" \
        --argjson files_downloaded "${kept}" \
        '{
            workdir: $workdir,
            source_url: $source_url,
            releases: $releases,
            prs: $prs,
            html_report: $html_report,
            files_downloaded: $files_downloaded
        }'
}

main "${@}"