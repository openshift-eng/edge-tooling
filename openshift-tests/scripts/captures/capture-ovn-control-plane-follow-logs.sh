#!/usr/bin/bash
# Follow one Running ovnkube-control-plane pod log (timestamps) — master/default network reconcile path.
# Picks first container that works among common OCP names.
#
# Reconnects forever, re-discovering the acting Running pod and container on each pass:
# the control-plane pod is rescheduled between masters during recovery tests, so a single
# `oc logs -f` would end (and never resume) the moment its pod is deleted.
#
# Env: KUBECONFIG. Optional: OVN_FOLLOW_RECONNECT_SLEEP (default 15).
# Output: stdout — redirect in run-all-captures to ovn-k-cp-follow-<ts>.log

# Omit errexit intentionally: keep retrying after API outages or interrupted log streams.
set -uo pipefail

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../utils/capture-helpers.sh"

OVN_NS="openshift-ovn-kubernetes"
RECONNECT_SLEEP="${OVN_FOLLOW_RECONNECT_SLEEP:-15}"
require_capture_interval "${RECONNECT_SLEEP}" OVN_FOLLOW_RECONNECT_SLEEP

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [ovn-cp-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

pick_container() {
    local pod="$1" c
    for c in ovnkube-cluster-manager ovnkube-controller kube-rbac-proxy; do
        if oc logs -n "${OVN_NS}" "${pod}" -c "${c}" --tail=1 &>/dev/null; then
            echo "${c}"
            return 0
        fi
    done
    return 1
}

echo "$(ts) [ovn-cp-follow] start — will follow the Running ovnkube-control-plane pod (reconnects every ${RECONNECT_SLEEP}s)"

capture_ovn_control_plane() {
    local pod cont
    pod=$(oc get pods -n "${OVN_NS}" -l app=ovnkube-control-plane -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1) || {
        echo "$(ts) [ovn-cp-follow] pod query failed; retry in ${RECONNECT_SLEEP}s"
        return 1
    }
    if [[ -z "${pod}" ]]; then
        echo "$(ts) [ovn-cp-follow] no Running ovnkube-control-plane pod; retry in ${RECONNECT_SLEEP}s"
        return 1
    fi

    cont=$(pick_container "${pod}") || {
        echo "$(ts) [ovn-cp-follow] no known container in pod ${pod}; retry in ${RECONNECT_SLEEP}s"
        return 1
    }

    echo "$(ts) [ovn-cp-follow] streaming pod=${pod} container=${cont}"
    oc logs -n "${OVN_NS}" "${pod}" -c "${cont}" -f --timestamps=true 2>&1 || true
}

capture_retry_loop "${RECONNECT_SLEEP}" capture_ovn_control_plane
