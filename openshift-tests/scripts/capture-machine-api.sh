#!/usr/bin/bash
# Stream Machine API / CAPI controller logs (Machine delete hangs, finalizers, provider errors).
# Starts one "oc logs -f" per deployment that exists; stopping this process stops all tails (trap on SIGTERM/INT).
#
# Prerequisites: KUBECONFIG set (e.g. source proxy.env).
# Env: CAPTURE_LOG_DIR (default: scratch/runs), CAPTURE_TIMESTAMP (default: now).
#
# Output: ${CAPTURE_LOG_DIR}/machine-api-<component>-${TIMESTAMP}.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${CAPTURE_LOG_DIR:-${SCRATCH_ROOT}/runs}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "${LOG_DIR}"

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "Error: KUBECONFIG not set."
    exit 1
fi

declare -a pids=()

cleanup() {
    local p
    for p in "${pids[@]:-}"; do
        kill "$p" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

stream_deploy() {
    local ns=$1
    local dep=$2
    local slug=$3
    local f="${LOG_DIR}/machine-api-${slug}-${TIMESTAMP}.log"

    if ! oc get "deployment/${dep}" -n "${ns}" &>/dev/null; then
        echo "capture-machine-api: skip ${ns}/deployment/${dep} (not found)"
        return 0
    fi

    echo "capture-machine-api: streaming ${ns}/deployment/${dep} -> ${f}"
    oc logs -n "${ns}" "deployment/${dep}" -f --timestamps >>"${f}" 2>&1 &
    pids+=($!)
}

# openshift-machine-api: MAO + controllers + optional CBO
stream_deploy openshift-machine-api machine-api-operator mao
stream_deploy openshift-machine-api machine-api-controllers mac-controllers
stream_deploy openshift-machine-api cluster-baremetal-operator cbo

# CAPI / cluster-api operator (namespace may not exist on older clusters)
if oc get ns openshift-cluster-api &>/dev/null; then
    stream_deploy openshift-cluster-api cluster-api-operator capi-operator
    # Some releases use a different deployment name
    stream_deploy openshift-cluster-api cluster-capi-operator cluster-capi-operator
fi

if [[ ${#pids[@]} -eq 0 ]]; then
    echo "capture-machine-api: no deployments found (machine-api-operator / machine-api-controllers / cluster-api-*)."
    exit 1
fi

echo "capture-machine-api: ${#pids[@]} log stream(s) running; waiting (Ctrl+C / SIGTERM stops all)."
set +e
wait
set -e
