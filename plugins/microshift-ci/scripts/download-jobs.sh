#!/bin/bash
set -euo pipefail

# Download Prow job artifacts for analysis.
#
# Accepts JSON on stdin — either flat job array (from prow-jobs-for-release.sh)
# or nested PR array (from prow-jobs-for-pull-requests.sh --mode detail).
# Downloads artifacts into WORKDIR/artifacts/BUILD_ID/ with parallel workers.
# Skips already-downloaded jobs. Outputs JSON job list with local paths on stdout.
#
# Usage:
#   prow-jobs-for-release.sh 4.22 | download-jobs.sh --workdir DIR
#   prow-jobs-for-release.sh 4.22 | download-jobs.sh --workdir DIR --parallel 4
#   prow-jobs-for-pull-requests.sh --mode detail | download-jobs.sh --workdir DIR
#
# Output (stdout): JSON array of job objects with "artifacts_dir" added:
#   [{"job":"...","url":"...","build_id":"...","artifacts_dir":"/tmp/.../artifacts/BUILD_ID"}, ...]
#
# Progress/errors: stderr

WORKDIR=""

# Convert a Prow view URL to a GCS path
url_to_gcs() {
    echo "$1" | sed \
        -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' \
        -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|'
}

# Download a single job's artifacts
# Args: build_id url
# Returns: 0 on success (cached or downloaded), 1 on failure
#
# gsutil cp -r gs://bucket/.../BUILD_ID/ dest/ creates dest/BUILD_ID/...
# so the final layout is: ${WORKDIR}/artifacts/${BUILD_ID}/finished.json etc.
# Uses gsutil anonymous access since the bucket is public.
download_job() {
    local build_id="$1"
    local url="$2"
    local dest="${WORKDIR}/artifacts/${build_id}"

    if [[ -d "${dest}" ]] && [[ -f "${dest}/finished.json" ]]; then
        echo "  cached: ${build_id}" >&2
        return 0
    fi

    local gcs_path
    gcs_path=$(url_to_gcs "${url}")

    # gsutil cp -r .../BUILD_ID/ parent/ → parent/BUILD_ID/...
    # so we download into the parent and let gsutil create the BUILD_ID dir
    local parent="${WORKDIR}/artifacts"
    mkdir -p "${parent}"
    local dl_err
    dl_err=$(mktemp)
    if gsutil -q -m cp -r "${gcs_path}/" "${parent}/" 2>"${dl_err}"; then
        echo "  downloaded: ${build_id}" >&2
        rm -f "${dl_err}"
        return 0
    else
        echo "  FAILED: ${build_id}" >&2
        [[ -s "${dl_err}" ]] && cat "${dl_err}" >&2
        rm -f "${dl_err}"
        return 1
    fi
}

usage() {
    echo "Usage: <jobs-json> | ${0} --workdir DIR [--parallel N]" >&2
    echo "  --workdir DIR: work directory (required)" >&2
    echo "  --parallel N:  number of parallel downloads (default: 6)" >&2
    echo "" >&2
    echo "Accepts JSON on stdin from:" >&2
    echo "  prow-jobs-for-release.sh (flat job array)" >&2
    echo "  prow-jobs-for-pull-requests.sh --mode detail (nested PR array)" >&2
    exit 1
}

main() {
    local parallel=6

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir)
                [[ ${#} -lt 2 ]] && { echo "Error: --workdir requires a directory" >&2; usage; }
                WORKDIR="${2}"; shift 2 ;;
            --parallel)
                [[ ${#} -lt 2 ]] && { echo "Error: --parallel requires a number" >&2; usage; }
                parallel="${2}"; shift 2 ;;
            -h|--help) usage ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *) echo "Unknown argument: ${1}" >&2; usage ;;
        esac
    done

    if [[ -z "${WORKDIR}" ]]; then
        echo "Error: --workdir is required" >&2
        usage
    fi

    mkdir -p "${WORKDIR}/artifacts"

    # Read all stdin into a variable
    local input
    input=$(cat)

    # Normalize input: detect format and extract flat job list
    # Release format: [{"job":...,"url":...,"build_id":...}, ...]
    # PR format: [{"pr_number":...,"jobs":[{"job":...,"url":...,"build_id":...}, ...]}, ...]
    local jobs_json
    if echo "${input}" | jq -e '.[0].jobs' >/dev/null 2>&1; then
        # PR format — flatten nested jobs, carry pr_number into each job
        jobs_json=$(echo "${input}" | jq '[.[] | .pr_number as $pr | .jobs[] | . + {pr_number: $pr}]')
    else
        # Release format — use as-is
        jobs_json="${input}"
    fi

    local total
    total=$(echo "${jobs_json}" | jq 'length')

    if [[ "${total}" -eq 0 ]]; then
        echo "No jobs to download." >&2
        echo "[]"
        return 0
    fi

    echo "Downloading artifacts for ${total} jobs (${parallel} parallel)..." >&2

    # Export functions and vars for subshells
    export WORKDIR
    export -f download_job url_to_gcs

    # Download all jobs in parallel
    local status_file
    status_file=$(mktemp)

    while IFS=$'\t' read -r build_id url; do
        (
            if download_job "${build_id}" "${url}"; then
                echo "${build_id}:ok" >> "${status_file}"
            else
                echo "${build_id}:fail" >> "${status_file}"
            fi
        ) &

        # Limit parallelism
        while [[ $(jobs -rp | wc -l) -ge ${parallel} ]]; do
            wait -n 2>/dev/null || true
        done
    done < <(echo "${jobs_json}" | jq -r '.[] | [.build_id, .url] | @tsv')
    wait

    # Count results and collect failed build IDs
    local ok=0 fail=0 failed_ids=""
    if [[ -f "${status_file}" ]]; then
        ok=$(grep -c ':ok$' "${status_file}" 2>/dev/null || true)
        fail=$(grep -c ':fail$' "${status_file}" 2>/dev/null || true)
        failed_ids=$(grep ':fail$' "${status_file}" 2>/dev/null | cut -d: -f1 || true)
    fi
    rm -f "${status_file}"

    echo "Done: ${ok} downloaded/cached, ${fail} failed." >&2

    # Exclude failed downloads, then add artifacts_dir
    local output_json="${jobs_json}"
    for bid in ${failed_ids}; do
        output_json=$(echo "${output_json}" | jq --arg id "${bid}" '[.[] | select(.build_id != $id)]')
    done
    echo "${output_json}" | jq --arg workdir "${WORKDIR}" '[.[] | . + {artifacts_dir: ($workdir + "/artifacts/" + .build_id)}]'
}

main "${@}"
