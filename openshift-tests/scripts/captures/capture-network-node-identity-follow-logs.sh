#!/usr/bin/bash
# Follow network-node-identity logs when the deployment exists (annotation / webhook debugging).
# stdout — redirect in run-all-captures.
#
# Polls forever: re-checks for the deployment and reconnects after each stream ends, so a
# pod reschedule during a recovery test (or the deployment appearing late) is not missed.
#
# Env: KUBECONFIG. Optional: NNID_RECONNECT_SLEEP (default 15).

# Omit errexit intentionally: keep retrying after API outages or interrupted log streams.
set -uo pipefail

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../utils/capture-helpers.sh"

NNID_NS="openshift-network-operator"
RECONNECT_SLEEP="${NNID_RECONNECT_SLEEP:-15}"
require_capture_interval "${RECONNECT_SLEEP}" NNID_RECONNECT_SLEEP

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [nnid-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

echo "$(ts) [nnid-follow] start — polling for ${NNID_NS}/deployment/network-node-identity (reconnects every ${RECONNECT_SLEEP}s)"

capture_network_node_identity() {
    # OCP: often openshift-network-operator/network-node-identity
    if oc get deployment network-node-identity -n "${NNID_NS}" &>/dev/null; then
        echo "$(ts) [nnid-follow] streaming ${NNID_NS}/deployment/network-node-identity"
        oc logs -n "${NNID_NS}" deployment/network-node-identity -f --timestamps=true 2>&1 || true
    else
        echo "$(ts) [nnid-follow] deployment/network-node-identity not found in ${NNID_NS}; retry in ${RECONNECT_SLEEP}s"
    fi
}

capture_retry_loop "${RECONNECT_SLEEP}" capture_network_node_identity
