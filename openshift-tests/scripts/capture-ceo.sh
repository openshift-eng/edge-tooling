#!/usr/bin/bash
# Stream logs from ALL cluster-etcd-operator (CEO) pods in openshift-etcd-operator.
# Watches for pods matching label app=etcd-operator and starts oc logs -f --timestamps
# for each one, writing a per-pod file. This survives pod reschedules: the CEO is a
# single-replica Deployment, but its pod is recreated repeatedly during disruptive
# recovery tests (rescheduled between control-plane nodes), and each new pod runs its
# own leader election. A plain `oc logs deployment/etcd-operator -f` follows only the
# first pod and never reconnects, so the acting healthcheck-controller leader is easily
# missed. Discovering pods on a poll loop captures every successor (and any future
# multi-replica rollout) so the leader's log is always recorded.
#
# On first sight of a pod, its previous-container log (if any) is also grabbed one-shot
# so a crash-and-restart-in-place is not lost.
#
# Prerequisites: KUBECONFIG set.
# Env:
#   CAPTURE_LOG_DIR   output directory (default: scratch/runs)
#   CAPTURE_TIMESTAMP timestamp suffix (default: now)
#   CEO_POLL_SEC      pod discovery poll interval seconds (default: 10)
#
# Output files:
#   ${CAPTURE_LOG_DIR}/ceo-<pod>-<timestamp>.log           (live -f stream)
#   ${CAPTURE_LOG_DIR}/ceo-<pod>-<timestamp>.previous.log  (prior container, if it restarted)
#
# This script is intended to run in background from run-all-captures.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${CAPTURE_LOG_DIR:-${SCRATCH_ROOT}/runs}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
POLL_SEC="${CEO_POLL_SEC:-10}"
NS="openshift-etcd-operator"
LABEL="app=etcd-operator"
CONTAINER="etcd-operator"

mkdir -p "${LOG_DIR}"

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "capture-ceo: KUBECONFIG not set; skipping."
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
    if [[ -n "${started[$pod]:-}" ]]; then
        return 0
    fi
    started["$pod"]=1

    local out_file="${LOG_DIR}/ceo-${pod}-${TIMESTAMP}.log"
    local prev_file="${LOG_DIR}/ceo-${pod}-${TIMESTAMP}.previous.log"

    # Best-effort one-shot: prior container instance (only exists if it restarted in place).
    if oc logs -n "${NS}" "${pod}" -c "${CONTAINER}" --previous --timestamps >>"${prev_file}" 2>/dev/null; then
        echo "capture-ceo: captured previous-container log ${NS}/${pod} -> ${prev_file}"
    else
        rm -f "${prev_file}" 2>/dev/null || true
    fi

    echo "capture-ceo: streaming ${NS}/${pod} -> ${out_file}"
    oc logs -n "${NS}" "${pod}" -c "${CONTAINER}" -f --timestamps >>"${out_file}" 2>&1 &
    child_pids+=($!)
}

echo "capture-ceo: watching namespace ${NS} (label=${LABEL}, poll=${POLL_SEC}s)"
while true; do
    while IFS= read -r pod; do
        [[ -z "${pod}" ]] && continue
        start_stream_for_pod "${pod}"
    done < <(oc get pods -n "${NS}" -l "${LABEL}" -o name 2>/dev/null | sed -n 's#^pod/##p')
    sleep "${POLL_SEC}"
done
