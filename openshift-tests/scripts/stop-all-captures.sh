#!/usr/bin/bash
# Stop all capture processes started by run-all-captures.sh.
# Usage: stop-all-captures.sh [timestamp]
#   If timestamp is omitted, kills the most recent capture-pids-*.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${SCRATCH_ROOT}/runs"

if [[ -n "${1:-}" ]]; then
    if [[ -f "${1}" ]]; then
        PID_FILE="${1}"
    else
        PID_FILE="${LOG_DIR}/capture-pids-${1}.txt"
    fi
else
    PID_FILE=$(ls -t "${LOG_DIR}"/capture-pids-*.txt 2>/dev/null | head -1)
fi

if [[ -z "${PID_FILE}" ]] || [[ ! -f "${PID_FILE}" ]]; then
    echo "No capture PID file found. Specify timestamp or run run-all-captures.sh first."
    exit 1
fi

echo "Stopping captures from ${PID_FILE}"
while read -r p; do
    [[ -z "$p" ]] && continue
    # Kill the process group so children (sleep, oc, ssh) die immediately
    # instead of leaving bash blocked until the foreground child exits.
    if kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null; then
        echo "  killed PID $p (pgid)"
    fi
done < "${PID_FILE}"

sleep 2

while read -r p; do
    [[ -z "$p" ]] && continue
    if kill -0 "$p" 2>/dev/null; then
        kill -9 -- "-$p" 2>/dev/null || kill -9 "$p" 2>/dev/null || true
        echo "  SIGKILL PID $p"
    fi
done < "${PID_FILE}"
echo "Done."
