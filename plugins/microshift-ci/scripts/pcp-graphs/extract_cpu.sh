#!/bin/bash
# Extract CPU usage data from PCP archive using pcp2json with 15-second intervals.
# Outputs JSON with arrays: timestamps, user, sys, iowait, idle (all in percentage)
#
# Usage: ./extract_cpu.sh <pcp-archive-dir> [output-json] [timezone]

set -euo pipefail

DATA_DIR="${1:?Usage: $0 <pcp-archive-dir> [output-json] [timezone]}"
OUTPUT="${2:-/data/cpu_data.json}"
TIMEZONE="${3:-UTC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Using archive directory: ${DATA_DIR}"

# Extract CPU metrics in a single pcp2json call
TMPFILE=$(mktemp)
trap 'rm -f "${TMPFILE}"' EXIT

(cd "${DATA_DIR}" && pcp2json -a . -t 15sec \
    kernel.all.cpu.user kernel.all.cpu.sys kernel.all.cpu.idle kernel.all.cpu.wait.total) \
    > "${TMPFILE}" 2>/dev/null || true

# Parse pcp2json output into clean plot-ready JSON
python3 "${SCRIPT_DIR}/parse_cpu.py" --timezone "${TIMEZONE}" \
    "${TMPFILE}" "${OUTPUT}"

echo "Wrote $(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d['timestamps']))" "${OUTPUT}" 2>/dev/null || echo 0) data points to ${OUTPUT}"
