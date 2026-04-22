#!/bin/bash
# Extract memory usage data from PCP archive using pcp2json with 15-second intervals.
# Outputs JSON with arrays: timestamps, used_gb, cached_gb, free_gb, total_gb
#
# Usage: ./extract_mem.sh <pcp-archive-dir> [output-json] [timezone]

set -euo pipefail

DATA_DIR="${1:?Usage: $0 <pcp-archive-dir> [output-json] [timezone]}"
OUTPUT="${2:-/data/mem_data.json}"
TIMEZONE="${3:-UTC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Using archive directory: ${DATA_DIR}"

TMPFILE=$(mktemp)
trap 'rm -f "${TMPFILE}"' EXIT

(cd "${DATA_DIR}" && pcp2json -a . -t 15sec \
    mem.util.used mem.util.free mem.util.cached mem.physmem) \
    > "${TMPFILE}" 2>/dev/null || true

python3 "${SCRIPT_DIR}/parse_mem.py" --timezone "${TIMEZONE}" \
    "${TMPFILE}" "${OUTPUT}"

echo "Wrote $(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d['timestamps']))" "${OUTPUT}" 2>/dev/null || echo 0) data points to ${OUTPUT}"
