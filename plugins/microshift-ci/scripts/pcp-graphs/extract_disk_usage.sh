#!/bin/bash
# Extract disk usage data from PCP archive using pcp2json with 15-second intervals.
# Outputs JSON with arrays: timestamps, used_gb, capacity_gb (for the root partition)
#
# Usage: ./extract_disk_usage.sh <pcp-archive-dir> [output-json] [timezone]

set -euo pipefail

DATA_DIR="${1:?Usage: $0 <pcp-archive-dir> [output-json] [timezone]}"
OUTPUT="${2:-/data/disk_usage.json}"
TIMEZONE="${3:-UTC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Using archive directory: ${DATA_DIR}"

TMPFILE=$(mktemp)
trap 'rm -f "${TMPFILE}"' EXIT

(cd "${DATA_DIR}" && pcp2json -a . -t 15sec \
    filesys.used filesys.capacity filesys.mountdir) \
    > "${TMPFILE}" 2>/dev/null || true

python3 "${SCRIPT_DIR}/parse_disk_usage.py" --timezone "${TIMEZONE}" \
    "${TMPFILE}" "${OUTPUT}"

echo "Wrote $(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d['timestamps']))" "${OUTPUT}" 2>/dev/null || echo 0) data points to ${OUTPUT}"
