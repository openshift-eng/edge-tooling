#!/usr/bin/bash
# Follow network-node-identity logs when the deployment exists (annotation / webhook debugging).
# stdout — redirect in run-all-captures.

set -uo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [nnid-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

# OCP: often openshift-network-operator/network-node-identity
if oc get deployment network-node-identity -n openshift-network-operator &>/dev/null; then
    echo "$(ts) [nnid-follow] start — openshift-network-operator/network-node-identity"
    oc logs -n openshift-network-operator deployment/network-node-identity \
        -f --timestamps=true 2>&1
    echo "$(ts) [nnid-follow] ended"
    exit 0
fi

echo "$(ts) [nnid-follow] skip: deployment/network-node-identity not found in openshift-network-operator"
exit 0
