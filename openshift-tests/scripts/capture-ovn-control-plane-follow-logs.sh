#!/usr/bin/bash
# Follow one Running ovnkube-control-plane pod log (timestamps) — master/default network reconcile path.
# Picks first container that works among common OCP names.

set -uo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
OVN_NS="openshift-ovn-kubernetes"

if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
    echo "$(ts) [ovn-cp-follow] skip: KUBECONFIG unset or oc missing" >&2
    exit 0
fi

pod=$(oc get pods -n "${OVN_NS}" -l app=ovnkube-control-plane -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' 2>/dev/null | head -1)
if [[ -z "${pod}" ]]; then
    echo "$(ts) [ovn-cp-follow] skip: no Running ovnkube-control-plane pod"
    exit 0
fi

pick_container() {
    local c
    for c in ovnkube-cluster-manager ovnkube-controller kube-rbac-proxy; do
        if oc logs -n "${OVN_NS}" "${pod}" -c "${c}" --tail=1 &>/dev/null; then
            echo "${c}"
            return 0
        fi
    done
    return 1
}

cont=$(pick_container) || {
    echo "$(ts) [ovn-cp-follow] skip: no known container in pod ${pod}"
    exit 0
}

echo "$(ts) [ovn-cp-follow] start pod=${pod} container=${cont}"
oc logs -n "${OVN_NS}" "${pod}" -c "${cont}" -f --timestamps=true 2>&1
echo "$(ts) [ovn-cp-follow] ended"
