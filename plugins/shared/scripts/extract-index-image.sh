#!/usr/bin/bash
set -euo pipefail

# Extract LVMS catalog index image metadata from downloaded job artifacts.
#
# Scans artifacts for lvms-catalogsource/build-log.txt, extracts the
# LVM_INDEX_IMAGE reference, and runs skopeo inspect to resolve digest,
# build date, and source commit. Results are written as JSON files to
# a dedicated ${WORKDIR}/index-image/ subdirectory.
#
# No-op when no lvms-catalogsource logs exist (safe for non-LVMS components).
#
# Usage:
#   extract-index-image.sh --workdir DIR <release1,release2,...>
#
# Output files:
#   ${WORKDIR}/index-image/release-<version>.json
#
# Progress/errors: stderr

WORKDIR=""

# ---------------------------------------------------------------------------
# Extract image reference from a catalogsource build log
# ---------------------------------------------------------------------------

extract_image_ref() {
    local build_log="$1"
    grep -m1 'LVM_INDEX_IMAGE is set to' "${build_log}" 2>/dev/null \
        | sed 's/.*LVM_INDEX_IMAGE is set to[: ]*//' \
        | tr -d '[:space:]'
}

# ---------------------------------------------------------------------------
# Inspect image with skopeo and write JSON
# ---------------------------------------------------------------------------

inspect_and_write() {
    local image="$1"
    local output_file="$2"

    if ! command -v skopeo >/dev/null 2>&1; then
        echo "  WARNING: skopeo not installed, writing image reference only" >&2
        jq -n --arg img "${image}" --arg err "skopeo not installed" \
            '{image: $img, error: $err}' > "${output_file}"
        return
    fi

    local inspect_json inspect_err
    inspect_err=$(mktemp)
    if ! inspect_json=$(skopeo inspect --no-tags "docker://${image}" 2>"${inspect_err}"); then
        rm -f "${inspect_err}"
        echo "  WARNING: skopeo inspect failed for ${image}, writing image reference only" >&2
        jq -n --arg img "${image}" --arg err "image does not exist in the registry" \
            '{image: $img, error: $err}' > "${output_file}"
        return
    fi
    rm -f "${inspect_err}"

    local digest built commit
    digest=$(echo "${inspect_json}" | jq -r '.Digest // empty')
    built=$(echo "${inspect_json}" | jq -r '(.Labels["org.opencontainers.image.created"] // .Created) // empty')
    commit=$(echo "${inspect_json}" | jq -r '(.Labels["io.openshift.build.commit.id"] // .Labels["vcs-ref"]) // empty')

    jq -n \
        --arg img "${image}" \
        --arg dig "${digest}" \
        --arg blt "${built}" \
        --arg cmt "${commit}" \
        '{image: $img, digest: $dig, built: $blt, commit: $cmt}
         | with_entries(select(.value != ""))' \
        > "${output_file}"
}

# ---------------------------------------------------------------------------
# Process a single release
# ---------------------------------------------------------------------------

process_release() {
    local release="$1"
    local jobs_file="${WORKDIR}/jobs/release-${release}-jobs.json"

    if [[ ! -f "${jobs_file}" ]]; then
        return
    fi

    local job_count
    job_count=$(jq 'length' "${jobs_file}")
    if [[ "${job_count}" -eq 0 ]]; then
        return
    fi

    local artifacts_dirs
    artifacts_dirs=$(jq -r '.[].artifacts_dir // empty' "${jobs_file}")

    local build_log=""
    for artifacts_dir in ${artifacts_dirs}; do
        [[ -d "${artifacts_dir}" ]] || continue
        build_log=$(find "${artifacts_dir}" -path '*/lvms-catalogsource/build-log.txt' -print -quit 2>/dev/null)
        [[ -n "${build_log}" ]] && break
    done

    if [[ -z "${build_log}" ]]; then
        echo "  Release ${release}: no lvms-catalogsource logs found" >&2
        return
    fi

    local image_ref
    image_ref=$(extract_image_ref "${build_log}")
    if [[ -z "${image_ref}" ]]; then
        echo "  Release ${release}: no LVM_INDEX_IMAGE found in build log" >&2
        return
    fi

    local build_id
    build_id=$(echo "${build_log}" | grep -oP 'artifacts/\K[^/]+' | head -1)
    echo "  Release ${release}: found image in build ${build_id}" >&2

    local output_dir="${WORKDIR}/index-image"
    mkdir -p "${output_dir}"

    inspect_and_write "${image_ref}" "${output_dir}/release-${release}.json"
    echo "  Release ${release}: wrote ${output_dir}/release-${release}.json" >&2
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

main() {
    local releases_arg=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) WORKDIR="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) releases_arg="${1}"; shift ;;
        esac
    done

    if [[ -z "${WORKDIR}" ]]; then
        echo "Error: --workdir is required" >&2
        echo "Usage: $(basename "$0") --workdir DIR <release1,release2,...>" >&2
        return 1
    fi

    if [[ -z "${releases_arg}" ]]; then
        echo "Error: releases argument required" >&2
        echo "Usage: $(basename "$0") --workdir DIR <release1,release2,...>" >&2
        return 1
    fi

    IFS=',' read -ra RELEASES <<< "${releases_arg}"

    echo "=== Extracting index images ===" >&2
    for release in "${RELEASES[@]}"; do
        release=$(echo "${release}" | xargs)
        process_release "${release}"
    done
}

main "${@}"
