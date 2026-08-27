#!/usr/bin/bash
# Stream logs from tnf-fencing-job pods in openshift-etcd.
# Watches for new pods matching ^tnf-fencing-job- and starts oc logs -f --timestamps
# for each one, writing to CAPTURE_LOG_DIR.
#
# Prerequisites: KUBECONFIG set.
# Env:
#   CAPTURE_LOG_DIR   output directory (default: scratch/runs)
#   CAPTURE_TIMESTAMP timestamp suffix (default: now)
#   FENCING_POLL_SEC  pod discovery poll interval seconds (default: 10)
#
# Output files:
#   ${CAPTURE_LOG_DIR}/tnf-fencing-job-<pod>-<timestamp>.log
#
# This script is intended to run in background from run-all-captures.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${CAPTURE_LOG_DIR:-${SCRATCH_ROOT}/runs}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
POLL_SEC="${FENCING_POLL_SEC:-10}"
NS="openshift-etcd"

mkdir -p "${LOG_DIR}"

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "capture-fencing-job: KUBECONFIG not set; skipping."
    exit 1
fi

declare -A started=()
declare -a child_pids=()

cleanup() {
    local p
    for p in "${child_pids[@]:-}"; do
        kill "${p}" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit 0' INT TERM

start_stream_for_pod() {
    local pod="$1"
    local out_file="${LOG_DIR}/tnf-fencing-job-${pod}-${TIMESTAMP}.log"
    if [[ -n "${started[$pod]:-}" ]]; then
        return 0
    fi
    started["$pod"]=1
    echo "capture-fencing-job: streaming ${NS}/${pod} -> ${out_file}"
    oc logs -n "${NS}" "${pod}" -f --timestamps >>"${out_file}" 2>&1 &
    child_pids+=($!)
}

echo "capture-fencing-job: watching namespace ${NS} (poll=${POLL_SEC}s)"
while true; do
    while IFS= read -r pod; do
        [[ -z "${pod}" ]] && continue
        start_stream_for_pod "${pod}"
    done < <(oc get pods -n "${NS}" -o name 2>/dev/null | sed -n 's#^pod/\(tnf-fencing-job-[^[:space:]]*\)$#\1#p')
    sleep "${POLL_SEC}"
done
