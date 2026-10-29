#!/usr/bin/bash
# Stream Baremetal Operator logs so provisioning decisions during node replacement
# can be correlated with the test. Run in background before the test; stop when it ends.
#
# Prerequisites: KUBECONFIG set (e.g. source proxy.env).
#
# Output: ${CAPTURE_LOG_DIR:-scripts/debug}/baremetal-operator-<timestamp>.log
# Prints the log path to stdout; use tail -f for a live view.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-helpers.sh"
setup_capture_output "${SCRIPT_DIR}/../debug"
LOG_FILE="${LOG_DIR}/baremetal-operator-${TIMESTAMP}.log"

capture_bmo() {
    local BMO_NAMESPACE="${BMO_NAMESPACE:-}" BMO_DEPLOY="${BMO_DEPLOY:-}"
    # Keep discovery local so automatic selection runs again after a disconnect.
    if [[ -z "${BMO_DEPLOY}" ]] || [[ -z "${BMO_NAMESPACE}" ]]; then
        if oc get deployment/metal3-baremetal-operator -n openshift-machine-api &>/dev/null; then
            BMO_NAMESPACE="openshift-machine-api"
            BMO_DEPLOY="metal3-baremetal-operator"
        elif oc get deployment/baremetal-operator -n openshift-machine-api &>/dev/null; then
            BMO_NAMESPACE="openshift-machine-api"
            BMO_DEPLOY="baremetal-operator"
        elif oc get deployment/baremetal-operator -n openshift-baremetal-operator &>/dev/null; then
            BMO_NAMESPACE="openshift-baremetal-operator"
            BMO_DEPLOY="baremetal-operator"
        else
            echo "No BMO deployment found; waiting for cluster access or deployment availability."
            return 1
        fi
    fi
    echo "Streaming Baremetal Operator logs (${BMO_NAMESPACE}/${BMO_DEPLOY}) to ${LOG_FILE}"
    oc logs -n "${BMO_NAMESPACE}" "deployment/${BMO_DEPLOY}" -f --timestamps
}

echo "Baremetal Operator logs -> ${LOG_FILE}; stop with Ctrl+C when the test ends."
capture_retry_loop 15 capture_bmo >> "${LOG_FILE}" 2>&1
