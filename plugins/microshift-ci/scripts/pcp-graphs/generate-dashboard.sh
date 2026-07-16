#!/usr/bin/bash
# Generate an interactive PCP performance dashboard.
#
# Two modes:
#   --url  <prow-url>   Download artifacts from GCS (default)
#   --local <path>      Use a local scenario-info/ directory
#
# Usage:
#   generate-dashboard.sh --url <prow-url> [--parallel N] [--timezone TZ]
#   generate-dashboard.sh --local <path> [--build-id ID] [--output FILE] [--title TITLE] [--timezone TZ]
#
# Prerequisites: python3, and one of:
#   - pcp-export-pcp2json (native)
#   - podman (container fallback)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_SCRIPTS="$(cd "${SCRIPT_DIR}/../../../shared/scripts" && pwd)"

URL=""
LOCAL_PATH=""
BUILD_ID=""
OUTPUT=""
TITLE=""
PARALLEL=6
TIMEZONE="UTC"
PCP2JSON_MODE=""  # "native" or "container"
CONTAINER_RT=""   # "podman"
CONTAINER_IMAGE="pcp2json-tool"

usage() {
    echo "Usage:" >&2
    echo "  ${0} --url <prow-url> [--parallel N] [--timezone TZ]" >&2
    echo "  ${0} --local <path> [--build-id ID] [--output FILE] [--title TITLE] [--timezone TZ]" >&2
    echo "" >&2
    echo "  --url URL       : Prow job URL" >&2
    echo "  --local PATH    : path to scenario-info/ directory" >&2
    echo "  --build-id ID   : build label (default: local)" >&2
    echo "  --output FILE   : output HTML file path" >&2
    echo "  --title TITLE   : HTML <title> for the dashboard" >&2
    echo "  --parallel N    : number of parallel extraction jobs (default: 6)" >&2
    echo "  --timezone TZ   : IANA timezone for timestamps (default: UTC)" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)
            [[ $# -lt 2 ]] && { echo "Error: --url requires a URL" >&2; usage; }
            URL="$2"; shift 2 ;;
        --local)
            [[ $# -lt 2 ]] && { echo "Error: --local requires a path" >&2; usage; }
            LOCAL_PATH="$2"; shift 2 ;;
        --build-id)
            [[ $# -lt 2 ]] && { echo "Error: --build-id requires a value" >&2; usage; }
            [[ "$2" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo "Error: --build-id contains invalid characters" >&2; usage; }
            BUILD_ID="$2"; shift 2 ;;
        --output)
            [[ $# -lt 2 ]] && { echo "Error: --output requires a file path" >&2; usage; }
            OUTPUT="$2"; shift 2 ;;
        --title)
            [[ $# -lt 2 ]] && { echo "Error: --title requires a value" >&2; usage; }
            TITLE="$2"; shift 2 ;;
        --parallel)
            [[ $# -lt 2 ]] && { echo "Error: --parallel requires a number" >&2; usage; }
            [[ "$2" =~ ^[1-9][0-9]*$ ]] || { echo "Error: --parallel must be a positive integer" >&2; usage; }
            PARALLEL="$2"; shift 2 ;;
        --timezone)
            [[ $# -lt 2 ]] && { echo "Error: --timezone requires a value" >&2; usage; }
            TIMEZONE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown argument: $1" >&2; usage ;;
    esac
done

if [[ -n "${URL}" && -n "${LOCAL_PATH}" ]]; then
    echo "Error: --url and --local are mutually exclusive" >&2
    usage
fi

if [[ -z "${URL}" && -z "${LOCAL_PATH}" ]]; then
    echo "Error: --url or --local is required" >&2
    usage
fi

# ---------------------------------------------------------------------------
# Set up WORKDIR depending on mode
# ---------------------------------------------------------------------------

if [[ -n "${LOCAL_PATH}" ]]; then
    # Local mode: symlink the local scenario-info into a temp workdir
    LOCAL_PATH="$(cd "${LOCAL_PATH}" && pwd)"
    BUILD_ID="${BUILD_ID:-local}"
    WORKDIR=$(mktemp -d "/tmp/microshift-pcp-local.XXXXXX")
    mkdir -p "${WORKDIR}/artifacts/${BUILD_ID}"
    ln -s "${LOCAL_PATH}" "${WORKDIR}/artifacts/${BUILD_ID}/scenario-info"
    echo "Local mode: ${LOCAL_PATH} -> ${WORKDIR}/artifacts/${BUILD_ID}/scenario-info" >&2
else
    # URL mode: download artifacts via shared script
    BUILD_ID=$(basename "${URL%/}")
    WORKDIR="/tmp/microshift-job-pcp-dashboard.${BUILD_ID}"
    bash "${SHARED_SCRIPTS}/download-jobs.sh" --workdir "${WORKDIR}" --url "${URL}"
fi

# ---------------------------------------------------------------------------
# pcp2json detection: native or container fallback
# ---------------------------------------------------------------------------

detect_pcp2json() {
    if command -v pcp2json >/dev/null 2>&1; then
        PCP2JSON_MODE="native"
        echo "Using native pcp2json" >&2
        return 0
    fi

    if command -v podman >/dev/null 2>&1; then
        CONTAINER_RT="podman"
    else
        echo "ERROR: pcp2json not found and podman is not available." >&2
        echo "Install one of:" >&2
        echo "  - pcp-export-pcp2json (dnf install pcp-export-pcp2json)" >&2
        echo "  - podman (for container fallback)" >&2
        exit 1
    fi

    PCP2JSON_MODE="container"
    ensure_container_image
}

ensure_container_image() {
    if ${CONTAINER_RT} image inspect "${CONTAINER_IMAGE}" >/dev/null 2>&1; then
        echo "Using ${CONTAINER_RT} container (image ${CONTAINER_IMAGE})" >&2
        return 0
    fi

    echo "Building ${CONTAINER_IMAGE} container image..." >&2
    ${CONTAINER_RT} build -t "${CONTAINER_IMAGE}" -f - . <<'DOCKERFILE'
FROM registry.fedoraproject.org/fedora-minimal:41
RUN microdnf install -y pcp-export-pcp2json python3-requests && microdnf clean all
ENTRYPOINT ["pcp2json"]
DOCKERFILE
}

# Run pcp2json on a PCP archive directory.
# Args: pcp_dir metrics...
# Stdout: JSON output
run_pcp2json() {
    local pcp_dir="$1"
    shift

    if [[ "${PCP2JSON_MODE}" == "native" ]]; then
        (cd "${pcp_dir}" && pcp2json -a . -t 15sec "$@") 2>/dev/null || true
    else
        ${CONTAINER_RT} run --rm -v "${pcp_dir}:/data:Z" "${CONTAINER_IMAGE}" \
            -a /data -t 15sec "$@" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# PCP tarball discovery and processing
# ---------------------------------------------------------------------------

find_pcp_tarballs() {
    find -L "${WORKDIR}/artifacts" -name "pcp-archives.tar" -path "*/vms/*/pcp/*" \
        2>/dev/null | sort
}

parse_tarball_path() {
    local tar_path="$1"
    local rel="${tar_path#"${WORKDIR}/artifacts/"}"

    BUILD_ID=$(echo "${rel}" | cut -d/ -f1)
    SCENARIO=$(echo "${tar_path}" | sed -nE 's|.*/scenario-info/([^/]+)/vms/.*|\1|p')
    VM_HOST=$(echo "${tar_path}" | sed -nE 's|.*/vms/([^/]+)/pcp/.*|\1|p')
}

# Extract a single metric type from a PCP archive dir.
# Args: pcp_dir output_json parse_script metrics...
extract_metric() {
    local pcp_dir="$1"
    local output_json="$2"
    local parse_script="$3"
    shift 3

    local tmpfile
    tmpfile=$(mktemp)

    if run_pcp2json "${pcp_dir}" "$@" > "${tmpfile}" && [[ -s "${tmpfile}" ]]; then
        if python3 "${SCRIPT_DIR}/${parse_script}" --timezone "${TIMEZONE}" \
                "${tmpfile}" "${output_json}" 2>/dev/null; then
            rm -f "${tmpfile}"
            return 0
        fi
    fi

    rm -f "${tmpfile}"
    return 1
}

process_tarball() {
    local tar_path="$1"

    parse_tarball_path "${tar_path}"

    if [[ -z "${BUILD_ID}" || -z "${SCENARIO}" || -z "${VM_HOST}" ]]; then
        echo "  SKIP: cannot parse tarball path" >&2
        return 0
    fi

    local output_dir="${WORKDIR}/pcp-dashboard/${BUILD_ID}/${SCENARIO}/${VM_HOST}"
    mkdir -p "${output_dir}"

    local tmp_dir
    tmp_dir=$(mktemp -d)

    if ! tar xf "${tar_path}" -C "${tmp_dir}" 2>/dev/null; then
        echo "  ${SCENARIO}: tar extraction failed" >&2
        rm -rf -- "${tmp_dir}"
        return 0
    fi

    # Find the PCP archive directory (contains the Latest folio or .meta files)
    local pcp_dir=""
    if [[ -f "${tmp_dir}/Latest" ]]; then
        pcp_dir="${tmp_dir}"
    else
        pcp_dir=$(find "${tmp_dir}" -name "Latest" -exec dirname {} \; 2>/dev/null | head -1)
        if [[ -z "${pcp_dir}" ]]; then
            pcp_dir=$(find "${tmp_dir}" -name "*.meta" -exec dirname {} \; 2>/dev/null | head -1)
        fi
    fi

    if [[ -z "${pcp_dir}" ]]; then
        echo "  ${SCENARIO}: no PCP archive found in tarball" >&2
        rm -rf -- "${tmp_dir}"
        return 0
    fi

    local ok=0

    extract_metric "${pcp_dir}" "${output_dir}/cpu.json" "parse_cpu.py" \
        kernel.all.cpu.user kernel.all.cpu.sys kernel.all.cpu.idle kernel.all.cpu.wait.total \
        && ok=$((ok + 1))

    extract_metric "${pcp_dir}" "${output_dir}/mem.json" "parse_mem.py" \
        mem.util.used mem.util.free mem.util.cached mem.physmem \
        && ok=$((ok + 1))

    extract_metric "${pcp_dir}" "${output_dir}/io.json" "parse_pcp.py" \
        disk.dev.read disk.dev.write disk.dev.await disk.dev.aveq \
        && ok=$((ok + 1))

    extract_metric "${pcp_dir}" "${output_dir}/disk.json" "parse_disk_usage.py" \
        filesys.used filesys.capacity filesys.mountdir \
        && ok=$((ok + 1))

    echo "  ${BUILD_ID}/${SCENARIO}/${VM_HOST}: ${ok}/4 metrics" >&2
    rm -rf -- "${tmp_dir}"
}

# ---------------------------------------------------------------------------
# Hypervisor PCP archive discovery and processing
# ---------------------------------------------------------------------------

find_hypervisor_pcp_dirs() {
    find -L "${WORKDIR}/artifacts" -name "Latest" -path "*pmlogs*" \
        -exec dirname {} \; 2>/dev/null | sort
}

process_hypervisor_dir() {
    local pcp_dir="$1"

    local build_id
    build_id=$(echo "${pcp_dir}" | sed -nE 's|.*/artifacts/([0-9]*)/artifacts/.*|\1|p')
    if [[ -z "${build_id}" ]]; then
        echo "  SKIP: cannot extract build_id from hypervisor path" >&2
        return 0
    fi

    local output_dir="${WORKDIR}/pcp-dashboard/${build_id}/hypervisor/hypervisor"
    mkdir -p "${output_dir}"

    local effective_dir="${pcp_dir}"
    if [[ "${PCP2JSON_MODE}" == "container" ]]; then
        # On macOS, podman runs in a VM that shares /Users but not /tmp.
        # Copy PCP files to $TMPDIR so the container can mount them.
        effective_dir=$(mktemp -d)
        cp "${pcp_dir}/"*.0 "${pcp_dir}/"*.index "${pcp_dir}/"*.meta "${effective_dir}/" 2>/dev/null
        if [[ -f "${pcp_dir}/Latest" ]]; then
            cp "${pcp_dir}/Latest" "${effective_dir}/"
        fi
    fi

    local ok=0

    extract_metric "${effective_dir}" "${output_dir}/cpu.json" "parse_cpu.py" \
        kernel.all.cpu.user kernel.all.cpu.sys kernel.all.cpu.idle kernel.all.cpu.wait.total \
        && ok=$((ok + 1))

    extract_metric "${effective_dir}" "${output_dir}/mem.json" "parse_mem.py" \
        mem.util.used mem.util.free mem.util.cached mem.physmem \
        && ok=$((ok + 1))

    extract_metric "${effective_dir}" "${output_dir}/io.json" "parse_pcp.py" \
        disk.dev.read disk.dev.write disk.dev.await disk.dev.aveq \
        && ok=$((ok + 1))

    extract_metric "${effective_dir}" "${output_dir}/disk.json" "parse_disk_usage.py" \
        filesys.used filesys.capacity filesys.mountdir \
        && ok=$((ok + 1))

    echo "  ${build_id}/hypervisor: ${ok}/4 metrics" >&2
    if [[ "${effective_dir}" != "${pcp_dir}" ]]; then
        rm -rf -- "${effective_dir}"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

detect_pcp2json

export -f process_tarball parse_tarball_path run_pcp2json extract_metric process_hypervisor_dir
export SCRIPT_DIR WORKDIR TIMEZONE PCP2JSON_MODE CONTAINER_RT CONTAINER_IMAGE

tarballs=$(find_pcp_tarballs)
hypervisor_dirs=$(find_hypervisor_pcp_dirs)

if [[ -z "${tarballs}" && -z "${hypervisor_dirs}" ]]; then
    echo "No PCP archives found in ${WORKDIR}/artifacts" >&2
    exit 0
fi

if [[ -n "${tarballs}" ]]; then
    total=$(echo "${tarballs}" | wc -l | tr -d ' ')

    # Container mode: run sequentially to avoid overwhelming the container runtime.
    # Native mode: run in parallel.
    if [[ "${PCP2JSON_MODE}" == "container" ]]; then
        echo "Processing PCP archives for ${total} scenario VMs (sequential, container mode)..." >&2
        while IFS= read -r tar_path; do
            process_tarball "${tar_path}"
        done <<< "${tarballs}"
    else
        echo "Processing PCP archives for ${total} scenario VMs (${PARALLEL} parallel)..." >&2
        while IFS= read -r tar_path; do
            process_tarball "${tar_path}" &
            while [[ $(jobs -rp | wc -l) -ge ${PARALLEL} ]]; do
                wait -n 2>/dev/null || true
            done
        done <<< "${tarballs}"
        wait
    fi
fi

if [[ -n "${hypervisor_dirs}" ]]; then
    echo "Processing hypervisor PCP archives..." >&2
    while IFS= read -r hv_dir; do
        process_hypervisor_dir "${hv_dir}"
    done <<< "${hypervisor_dirs}"
fi

# Extract scenario metadata
echo "Extracting scenario metadata..." >&2
python3 "${SCRIPT_DIR}/extract_scenarios.py" --workdir "${WORKDIR}"

# Generate the HTML dashboard
echo "Generating HTML dashboard..." >&2
dashboard_args=(--workdir "${WORKDIR}" --timezone "${TIMEZONE}")
[[ -n "${OUTPUT}" ]] && dashboard_args+=(--output "${OUTPUT}")
[[ -n "${TITLE}" ]] && dashboard_args+=(--title "${TITLE}")

python3 "${SCRIPT_DIR}/create-pcp-dashboard.py" "${dashboard_args[@]}"

output="${OUTPUT:-${WORKDIR}/pcp-dashboard.html}"
if [[ -f "${output}" ]]; then
    echo "Dashboard: ${output}" >&2
else
    echo "ERROR: Dashboard generation failed" >&2
    exit 1
fi
