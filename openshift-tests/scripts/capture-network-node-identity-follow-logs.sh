#!/usr/bin/bash
# Follow network-node-identity logs when the deployment exists (annotation / webhook debugging).
# stdout — redirect in run-all-captures.
#
# Polls forever: re-checks for the deployment and reconnects after each stream ends, so a
# pod reschedule during a recovery test (or the deployment appearing late) is not missed.
#
# Env: KUBECONFIG. Optional: NNID_RECONNECT_SLEEP (default 15).

set -uo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
NNID_NS="openshift-network-operator"
RECONNECT_SLEEP="${NNID_RECONNECT_SLEEP:-15}"

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [nnid-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

trap 'exit 0' INT TERM

echo "$(ts) [nnid-follow] start — polling for ${NNID_NS}/deployment/network-node-identity (reconnects every ${RECONNECT_SLEEP}s)"

while true; do
    # OCP: often openshift-network-operator/network-node-identity
    if oc get deployment network-node-identity -n "${NNID_NS}" &>/dev/null; then
        echo "$(ts) [nnid-follow] streaming ${NNID_NS}/deployment/network-node-identity"
        oc logs -n "${NNID_NS}" deployment/network-node-identity -f --timestamps=true 2>&1 || true
        echo "$(ts) [nnid-follow] stream ended; reconnecting in ${RECONNECT_SLEEP}s"
    else
        echo "$(ts) [nnid-follow] deployment/network-node-identity not found in ${NNID_NS}; retry in ${RECONNECT_SLEEP}s"
    fi
    sleep "${RECONNECT_SLEEP}"
done
