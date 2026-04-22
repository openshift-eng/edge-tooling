#!/bin/bash
# Generate PCP performance graphs for all jobs in a workdir.
#
# For each job that has PCP archives (pmlogs), extracts metrics and
# produces PNG graphs. Output goes to ${WORKDIR}/graphs/<build_id>/.
#
# Usage: generate-graphs.sh --workdir DIR [--parallel N] [--timezone TZ]
#
# Prerequisites: pcp-export-pcp2json, python3, matplotlib

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WORKDIR=""
PARALLEL=6
TIMEZONE="UTC"

usage() {
    echo "Usage: ${0} --workdir DIR [--parallel N] [--timezone TZ]" >&2
    echo "  --workdir DIR   : work directory containing artifacts/<build_id>/ (required)" >&2
    echo "  --parallel N    : number of parallel graph jobs (default: 6)" >&2
    echo "  --timezone TZ   : IANA timezone for timestamps (default: UTC)" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir)
            [[ $# -lt 2 ]] && { echo "Error: --workdir requires a directory" >&2; usage; }
            WORKDIR="$2"; shift 2 ;;
        --parallel)
            [[ $# -lt 2 ]] && { echo "Error: --parallel requires a number" >&2; usage; }
            PARALLEL="$2"; shift 2 ;;
        --timezone)
            [[ $# -lt 2 ]] && { echo "Error: --timezone requires a value" >&2; usage; }
            TIMEZONE="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown argument: $1" >&2; usage ;;
    esac
done

if [[ -z "${WORKDIR}" ]]; then
    echo "Error: --workdir is required" >&2
    usage
fi

# Find all PCP archive directories across all downloaded jobs
find_pcp_dirs() {
    find "${WORKDIR}/artifacts" -name "Latest" -path "*pmlogs*" \
        -exec dirname {} \; 2>/dev/null | sort
}

# Generate graphs for a single PCP archive directory
# Args: pcp_dir
generate_job_graphs() {
    local pcp_dir="$1"

    # Extract build_id from path: .../artifacts/<build_id>/artifacts/.../pmlogs/...
    local build_id
    build_id=$(echo "${pcp_dir}" | sed -n "s|.*/artifacts/\([0-9]*\)/artifacts/.*|\1|p")
    if [[ -z "${build_id}" ]]; then
        echo "  SKIP: cannot extract build_id from ${pcp_dir}" >&2
        return 0
    fi

    local output_dir="${WORKDIR}/graphs/${build_id}"
    mkdir -p "${output_dir}"

    # CPU usage graph (tab order: 1)
    if "${SCRIPT_DIR}/extract_cpu.sh" "${pcp_dir}" "${output_dir}/1_cpu_usage.json" "${TIMEZONE}" 2>/dev/null; then
        if python3 "${SCRIPT_DIR}/plot_cpu.py" "${output_dir}/1_cpu_usage.json" \
                -o "${output_dir}/1_cpu_usage.png" --timezone "${TIMEZONE}" >/dev/null 2>&1; then
            echo "  ${build_id}: cpu_usage" >&2
        else
            echo "  ${build_id}: cpu_usage plot failed" >&2
        fi
    else
        echo "  ${build_id}: cpu extraction failed" >&2
    fi

    # Memory usage graph (tab order: 2)
    if "${SCRIPT_DIR}/extract_mem.sh" "${pcp_dir}" "${output_dir}/2_mem_usage.json" "${TIMEZONE}" 2>/dev/null; then
        if python3 "${SCRIPT_DIR}/plot_mem.py" "${output_dir}/2_mem_usage.json" \
                -o "${output_dir}/2_mem_usage.png" --timezone "${TIMEZONE}" >/dev/null 2>&1; then
            echo "  ${build_id}: mem_usage" >&2
        else
            echo "  ${build_id}: mem_usage plot failed" >&2
        fi
    fi

    # Disk I/O graph (tab order: 3)
    if "${SCRIPT_DIR}/extract_io.sh" "${pcp_dir}" "${output_dir}/3_disk_io.json" "${TIMEZONE}" 2>/dev/null; then
        if python3 "${SCRIPT_DIR}/plot_io.py" "${output_dir}/3_disk_io.json" \
                -o "${output_dir}/3_disk_io.png" --timezone "${TIMEZONE}" >/dev/null 2>&1; then
            echo "  ${build_id}: disk_io" >&2
        else
            echo "  ${build_id}: disk_io plot failed" >&2
        fi
    fi

    # Disk usage graph (tab order: 4)
    if "${SCRIPT_DIR}/extract_disk_usage.sh" "${pcp_dir}" "${output_dir}/4_disk_usage.json" "${TIMEZONE}" 2>/dev/null; then
        if python3 "${SCRIPT_DIR}/plot_disk_usage.py" "${output_dir}/4_disk_usage.json" \
                -o "${output_dir}/4_disk_usage.png" --timezone "${TIMEZONE}" >/dev/null 2>&1; then
            echo "  ${build_id}: disk_usage" >&2
        else
            echo "  ${build_id}: disk_usage plot failed" >&2
        fi
    fi
}

export -f generate_job_graphs
export SCRIPT_DIR WORKDIR TIMEZONE

pcp_dirs=$(find_pcp_dirs)

if [[ -z "${pcp_dirs}" ]]; then
    echo "No PCP archives found in ${WORKDIR}/artifacts" >&2
    exit 0
fi

total=$(echo "${pcp_dirs}" | wc -l)
echo "Generating graphs for ${total} jobs (${PARALLEL} parallel)..." >&2

# Run in parallel
while IFS= read -r pcp_dir; do
    generate_job_graphs "${pcp_dir}" &

    # Limit parallelism
    while [[ $(jobs -rp | wc -l) -ge ${PARALLEL} ]]; do
        wait -n 2>/dev/null || true
    done
done <<< "${pcp_dirs}"
wait

# Count results
local_ok=$(find "${WORKDIR}/graphs" -name "*.png" 2>/dev/null | wc -l)
echo "Done: ${local_ok} graphs generated." >&2
