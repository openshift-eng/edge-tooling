#!/usr/bin/bash
# Stream logs from tnf-fencing-job pods in openshift-etcd.
# Watches for new pods matching ^tnf-fencing-job- and starts oc logs -f --timestamps
# for each one, writing to CAPTURE_LOG_DIR.
#
# Prerequisites: KUBECONFIG set.
# Env:
#   CAPTURE_LOG_DIR   output directory (default: runs)
#   CAPTURE_TIMESTAMP timestamp suffix (default: now)
#   FENCING_POLL_SEC  pod discovery poll interval seconds (default: 10)
#
# Output files:
#   ${CAPTURE_LOG_DIR}/tnf-fencing-job-<pod>-<timestamp>.log
#
# This script is intended to run in background from run-all-captures.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-helpers.sh"
setup_capture_output
POLL_SEC="${FENCING_POLL_SEC:-10}"
require_capture_interval "${POLL_SEC}" FENCING_POLL_SEC
NS="openshift-etcd"

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "capture-fencing-job: KUBECONFIG not set; skipping."
    exit 1
fi

start_stream_for_pod() {
    local pod="$1"
    [[ "${pod}" == tnf-fencing-job-* ]] || return 0
    local out_file="${LOG_DIR}/tnf-fencing-job-${pod}-${TIMESTAMP}.log"
    echo "capture-fencing-job: streaming ${NS}/${pod} -> ${out_file}"
    start_capture_stream "${pod}" oc logs -n "${NS}" "${pod}" \
        -f --timestamps >>"${out_file}" 2>&1
}

echo "capture-fencing-job: watching namespace ${NS} (poll=${POLL_SEC}s)"
capture_pod_streams "${NS}" "${POLL_SEC}"
