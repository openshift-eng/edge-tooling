#!/usr/bin/bash
# Periodic snapshot of Machine / BareMetalHost / events / ClusterOperator machine-api (delete debugging).
# Writes timestamped sections to stdout; intended to be redirected by run-all-captures.sh.
#
# Prerequisites: KUBECONFIG set.
# Env: MACHINE_API_SNAPSHOT_POLL_SEC (default: 60)

set -euo pipefail

POLL_SEC="${MACHINE_API_SNAPSHOT_POLL_SEC:-60}"

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "Error: KUBECONFIG not set."
    exit 1
fi

echo "machine-api snapshot capture: poll=${POLL_SEC}s"

trap 'exit 0' INT TERM

while true; do
    echo
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) machine-api-snapshot ====="
    echo "--- clusteroperator machine-api ---"
    oc describe clusteroperator machine-api 2>&1 | tail -80 || true

    echo "--- machines (openshift-machine-api) ---"
    oc get machine.machine.openshift.io -n openshift-machine-api -o wide 2>&1 || true
    echo "--- machines (yaml, first ~200 lines) ---"
    oc get machine.machine.openshift.io -n openshift-machine-api -o yaml 2>&1 | head -200 || true

    echo "--- baremetalhosts ---"
    oc get baremetalhost -n openshift-machine-api -o wide 2>&1 || true

    echo "--- events openshift-machine-api (tail) ---"
    oc get events -n openshift-machine-api --sort-by=".lastTimestamp" 2>&1 | tail -80 || true

    if oc get ns openshift-cluster-api &>/dev/null; then
        echo "--- events openshift-cluster-api (tail) ---"
        oc get events -n openshift-cluster-api --sort-by=".lastTimestamp" 2>&1 | tail -60 || true
        echo "--- deployments openshift-cluster-api ---"
        oc get deploy -n openshift-cluster-api -o wide 2>&1 || true
    fi

    sleep "${POLL_SEC}"
done
