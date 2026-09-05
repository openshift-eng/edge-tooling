#!/usr/bin/bash
# Follow OVN-Kubernetes ovnkube-node logs with server timestamps into runs/.
# Complements capture-ovn-chassis-trace.sh (poll samples); this is a continuous stream.
#
# Env: KUBECONFIG. Stops when killed (run-all-captures PID file).
# Output: stdout — redirect in run-all-captures to ovn-k-node-follow-<ts>.log

set -uo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [ovn-k-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

echo "$(ts) [ovn-k-follow] start — openshift-ovn-kubernetes app=ovnkube-node -c ovnkube-node (timestamps+prefix)"

set +e
oc logs -n openshift-ovn-kubernetes -l app=ovnkube-node -c ovnkube-node \
    -f --timestamps=true --prefix=true --max-log-requests=30 2>&1
rc=$?
set -euo pipefail

if [[ "${rc}" -ne 0 ]]; then
    echo "$(ts) [ovn-k-follow] retry without --max-log-requests (first try rc=${rc})"
    oc logs -n openshift-ovn-kubernetes -l app=ovnkube-node -c ovnkube-node \
        -f --timestamps=true --prefix=true 2>&1 || true
fi

echo "$(ts) [ovn-k-follow] stream ended"
