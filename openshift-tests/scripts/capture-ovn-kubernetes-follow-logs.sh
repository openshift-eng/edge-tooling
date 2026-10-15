#!/usr/bin/bash
# Follow OVN-Kubernetes ovnkube-node logs with server timestamps into runs/.
# Complements capture-ovn-chassis-trace.sh (poll samples); this is a continuous stream.
#
# Reconnects forever: `oc logs -l ... -f` binds to the pods present at connect time and
# ends when any of them is deleted (e.g. a node reboot/reschedule during recovery tests).
# The loop re-selects the current ovnkube-node pods on every reconnect so the stream
# survives DaemonSet churn.
#
# Env: KUBECONFIG. Optional: OVN_FOLLOW_RECONNECT_SLEEP (default 15).
#      Stops when killed (run-all-captures PID file / process-group kill).
# Output: stdout — redirect in run-all-captures to ovn-k-node-follow-<ts>.log

set -uo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
RECONNECT_SLEEP="${OVN_FOLLOW_RECONNECT_SLEEP:-15}"

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [ovn-k-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

trap 'exit 0' INT TERM

echo "$(ts) [ovn-k-follow] start — openshift-ovn-kubernetes app=ovnkube-node -c ovnkube-node (timestamps+prefix, reconnects every ${RECONNECT_SLEEP}s)"

while true; do
    echo "$(ts) [ovn-k-follow] (re)connecting to current ovnkube-node pods"
    # Prefer --max-log-requests (needs a high cap for many nodes); fall back if the
    # server rejects the flag/limit, then reconnect after the stream ends.
    oc logs -n openshift-ovn-kubernetes -l app=ovnkube-node -c ovnkube-node \
        -f --timestamps=true --prefix=true --max-log-requests=30 2>&1 \
    || oc logs -n openshift-ovn-kubernetes -l app=ovnkube-node -c ovnkube-node \
        -f --timestamps=true --prefix=true 2>&1 \
    || true
    echo "$(ts) [ovn-k-follow] stream ended; reconnecting in ${RECONNECT_SLEEP}s"
    sleep "${RECONNECT_SLEEP}"
done
