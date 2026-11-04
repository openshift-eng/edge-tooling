#!/usr/bin/bash
# Follow OVN-Kubernetes ovnkube-node pods' default container logs with timestamps into runs/.
# Complements capture-ovn-chassis-trace.sh (poll samples); this is a continuous stream.
#
# Polls for pods independently of their log streams, so replacement pods are
# captured while surviving pods continue streaming. Reconnects replay all retained
# logs to cover outages.
#
# Env: KUBECONFIG. Optional: OVN_FOLLOW_RECONNECT_SLEEP (default 15).
#      Stops when killed (run-all-captures PID file / process-group kill).
# Output: stdout — redirect in run-all-captures to ovn-k-node-follow-<ts>.log

# Omit errexit intentionally: keep retrying after API outages or interrupted log streams.
set -uo pipefail

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../utils/capture-helpers.sh"

RECONNECT_SLEEP="${OVN_FOLLOW_RECONNECT_SLEEP:-15}"
require_capture_interval "${RECONNECT_SLEEP}" OVN_FOLLOW_RECONNECT_SLEEP

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [ovn-k-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

echo "$(ts) [ovn-k-follow] start — openshift-ovn-kubernetes app=ovnkube-node (pod default container, timestamps+prefix, polls every ${RECONNECT_SLEEP}s)"

start_stream_for_pod() {
    local pod="$1"
    echo "$(ts) [ovn-k-follow] streaming pod=${pod}"
    start_capture_stream "${pod}" oc logs -n openshift-ovn-kubernetes "${pod}" \
        -f --timestamps=true --prefix=true --tail=-1 2>&1
}

capture_pod_streams openshift-ovn-kubernetes "${RECONNECT_SLEEP}" -l app=ovnkube-node
