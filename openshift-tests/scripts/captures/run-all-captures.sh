#!/usr/bin/bash
# Start parallel log capture for node-replacement debugging — unrelated to openshift-tests.
#
# Captures (separate processes, files under runs/):
#   - libvirt VM state/UUID/disks/MACs on hypervisor
#   - Pacemaker journal on master-0 and master-1
#   - Corosync journal on master-0 and master-1
#   - OVN chassis trace (timestamped): API + host OVS + SB Chassis + l3-gateway-config + pod placement (capture-ovn-chassis-trace.sh)
#   - OVN follow streams (timestamped oc logs -f): all ovnkube-node, one ovnkube-control-plane pod, network-node-identity (if present)
#   - Bare Metal Operator pod logs (oc logs)
#   - Cluster etcd operator (CEO) pod logs, ALL incarnations (capture-ceo.sh: poll-discovers pods, survives reschedules)
#   - Machine API / CAPI controller logs (capture-machine-api.sh: MAO, machine-api-controllers, CAPI operator)
#   - Machine API snapshots (capture-machine-api-snapshot.sh: Machine, BMH, events, clusteroperator machine-api)
#   - TNF fencing job pod logs (capture-fencing-job.sh)
#   - TNF update-setup job monitoring (capture-update-setup-job.sh: job status, pod creation/node, container state)
#
# This is NOT "all monitors" (openshift-tests --monitor). Different feature entirely.
#
# Start before the node replacement test; stop with stop-all-captures.sh when the test ends.
#
# Required env (same as run-test.sh):
#   HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH
# For Pacemaker capture, masters must be reachable via ProxyJump (hypervisor);
# optional: MASTER_0_IP, MASTER_1_IP, MASTER_SSH_USER (default: core).
#
# Required: set PROXY_ENV to the cluster proxy.env path; it is sourced for KUBECONFIG.
# One-shot OVN/API/OVS check (no loop): OVN_CHASSIS_ONCE=1 bash scripts/captures/capture-ovn-chassis-trace.sh
#
# Output: runs/{virsh,pacemaker-*,ovn-chassis-trace,ovn-k-*-follow,nnid-follow,baremetal-operator,ceo,machine-api-*,machine-api-snapshot,tnf-fencing-job-*,update-setup-job}-<timestamp>.log
# PIDs: runs/capture-pids-<timestamp>.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-helpers.sh"
CAPTURE_TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date -u +%Y%m%d-%H%M%S)}"
setup_capture_output
PID_FILE="${LOG_DIR}/capture-pids-${TIMESTAMP}.txt"

# Source proxy.env first so we can default HYPERVISOR_IP from EC2_PUBLIC_IP (same as run-test.sh)
load_proxy_env

# When proxy.env was sourced, prefer EC2_PUBLIC_IP so captures match the cluster's hypervisor.
HYPERVISOR_IP="${EC2_PUBLIC_IP:-${HYPERVISOR_IP:-}}"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../utils/ssh-helpers.sh"
setup_ssh_config
export HYPERVISOR_IP SSH_USER SSH_KEY_PATH

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set. Export it or ensure proxy.env sets EC2_PUBLIC_IP."
    exit 1
fi

export CAPTURE_TIMESTAMP="${TIMESTAMP}"
export CAPTURE_LOG_DIR="${LOG_DIR}"
echo "Starting captures with timestamp ${TIMESTAMP}"
echo "Logs -> ${LOG_DIR}"
echo ""

# Clear PID file
: > "${PID_FILE}"

# Launch in a separate process group so the stop helper also stops children.
start_capture() {
    local output="$1"
    shift
    setsid "$@" >> "${LOG_DIR}/${output}" 2>&1 &
    local pid=$!
    printf '%s\n' "${pid}" >> "${PID_FILE}"
    echo "  ${output} (PID ${pid})"
}

start_capture "virsh-${TIMESTAMP}.out" "${SCRIPT_DIR}/capture-virsh-status.sh"
for component in pacemaker corosync; do
    for node in master-0 master-1; do
        start_capture "${component}-${node}-${TIMESTAMP}.out" "${SCRIPT_DIR}/capture-${component}.sh" "${node}"
    done
done
start_capture "ovn-chassis-trace-${TIMESTAMP}.log" bash "${SCRIPT_DIR}/capture-ovn-chassis-trace.sh"
start_capture "disruption-evidence-${TIMESTAMP}.log" bash "${SCRIPT_DIR}/capture-disruption-evidence.sh"

if [[ -n "${KUBECONFIG:-}" ]]; then
    start_capture "ovn-k-node-follow-${TIMESTAMP}.log" bash "${SCRIPT_DIR}/capture-ovn-kubernetes-follow-logs.sh"
    start_capture "ovn-k-cp-follow-${TIMESTAMP}.log" bash "${SCRIPT_DIR}/capture-ovn-control-plane-follow-logs.sh"
    start_capture "nnid-follow-${TIMESTAMP}.log" bash "${SCRIPT_DIR}/capture-network-node-identity-follow-logs.sh"

    # The BMO collector owns deployment discovery and reports absence in its output.
    start_capture "baremetal-operator-${TIMESTAMP}.out" "${SCRIPT_DIR}/capture-baremetal-operator.sh"
    # CEO discovers and follows every pod incarnation, including previous logs.
    start_capture "ceo-streams-${TIMESTAMP}.out" bash "${SCRIPT_DIR}/capture-ceo.sh"
    start_capture "machine-api-streams-${TIMESTAMP}.out" "${SCRIPT_DIR}/capture-machine-api.sh"
    start_capture "machine-api-snapshot-${TIMESTAMP}.log" bash "${SCRIPT_DIR}/capture-machine-api-snapshot.sh"
    start_capture "fencing-job-streams-${TIMESTAMP}.out" bash "${SCRIPT_DIR}/capture-fencing-job.sh"
    start_capture "update-setup-job-${TIMESTAMP}.out" bash "${SCRIPT_DIR}/capture-update-setup-job.sh"
else
    echo "  Kubernetes captures -> skipped (KUBECONFIG not set)"
fi

echo ""
echo "Captures launched. Startup output is in .out files; collected data is in .log files."
echo "When the test ends, stop captures with:"
echo "  \"${SCRIPT_DIR}/stop-all-captures.sh\" \"${PID_FILE}\""
