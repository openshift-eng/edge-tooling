#!/usr/bin/bash
# Stream Baremetal Operator logs so provisioning decisions during node replacement
# can be correlated with the test. Run in background before the test; stop when it ends.
#
# Prerequisites: KUBECONFIG set (e.g. source proxy.env).
#
# Output: scratch/debug/baremetal-operator-<timestamp>.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/debug"
mkdir -p "${LOG_DIR}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
LOG_FILE="${LOG_DIR}/baremetal-operator-${TIMESTAMP}.log"

# BMO deployment name: dev-scripts/metal3 use metal3-baremetal-operator; OCP uses baremetal-operator.
# Auto-discover if BMO_NAMESPACE/BMO_DEPLOY not already set (e.g. by run-all-captures.sh).
if [[ -z "${BMO_DEPLOY:-}" ]] || [[ -z "${BMO_NAMESPACE:-}" ]]; then
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
        echo "Error: No BMO deployment found (tried metal3-baremetal-operator and baremetal-operator in openshift-machine-api / openshift-baremetal-operator). KUBECONFIG set?"
        exit 1
    fi
fi
BMO_NAMESPACE="${BMO_NAMESPACE:-openshift-machine-api}"
BMO_DEPLOY="${BMO_DEPLOY:-baremetal-operator}"

if ! oc get "deployment/${BMO_DEPLOY}" -n "${BMO_NAMESPACE}" &>/dev/null; then
    echo "Error: deployment/${BMO_DEPLOY} not found in ${BMO_NAMESPACE}. Is the cluster up? KUBECONFIG set?"
    exit 1
fi

echo "Streaming Baremetal Operator logs (${BMO_NAMESPACE}/${BMO_DEPLOY}) to ${LOG_FILE}"
echo "Stop with Ctrl+C when the test ends."
echo ""

oc logs -n "${BMO_NAMESPACE}" "deployment/${BMO_DEPLOY}" -f --timestamps 2>&1 | tee -a "${LOG_FILE}"
