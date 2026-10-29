#!/usr/bin/bash
# Stream Machine API / CAPI controller logs (Machine delete hangs, finalizers, provider errors).
# Starts one "oc logs -f" per deployment that exists; stopping this process stops all tails (trap on SIGTERM/INT).
#
# Prerequisites: KUBECONFIG set (e.g. source proxy.env).
# Env: CAPTURE_LOG_DIR (default: runs), CAPTURE_TIMESTAMP (default: now).
#
# Output: ${CAPTURE_LOG_DIR}/machine-api-<component>-${TIMESTAMP}.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-helpers.sh"
setup_capture_output
RECONNECT_SLEEP="${MACHINE_API_RECONNECT_SLEEP:-15}"
require_capture_interval "${RECONNECT_SLEEP}" MACHINE_API_RECONNECT_SLEEP

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "Error: KUBECONFIG not set."
    exit 1
fi

trap stop_capture_streams EXIT INT TERM

stream_deploy() {
    local ns="$1" dep="$2" slug="$3"
    local f="${LOG_DIR}/machine-api-${slug}-${TIMESTAMP}.log"

    if ! oc get "deployment/${dep}" -n "${ns}" &>/dev/null; then
        echo "capture-machine-api: skip ${ns}/deployment/${dep} (not found)"
        return 0
    fi

    echo "capture-machine-api: streaming ${ns}/deployment/${dep} -> ${f} (reconnects every ${RECONNECT_SLEEP}s)"
    start_capture_stream "${slug}" capture_retry_loop "${RECONNECT_SLEEP}" \
        oc logs -n "${ns}" "deployment/${dep}" -f --timestamps >>"${f}" 2>&1
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

# shellcheck disable=SC2154 # Populated by start_capture_stream in capture-helpers.sh.
if [[ ${#stream_pids[@]} -eq 0 ]]; then
    echo "capture-machine-api: no deployments found (machine-api-operator / machine-api-controllers / cluster-api-*)."
    exit 1
fi

echo "capture-machine-api: ${#stream_pids[@]} log stream(s) running; waiting (Ctrl+C / SIGTERM stops all)."
set +e
wait
set -e
