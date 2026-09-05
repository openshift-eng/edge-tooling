#!/usr/bin/bash
# Start parallel log capture for node-replacement debugging — unrelated to openshift-tests.
#
# Captures (separate processes, files under runs/):
#   - virsh list on hypervisor
#   - Pacemaker journal on master-0 and master-1
#   - Corosync journal on master-0 and master-1
#   - OVN chassis trace (timestamped): API + host OVS + SB Chassis + virsh dumpxml summary + l3-gateway-config + nnid tail + ovn-k log grep (capture-ovn-chassis-trace.sh)
#   - OVN follow streams (timestamped oc logs -f): all ovnkube-node, one ovnkube-control-plane pod, network-node-identity (if present)
#   - Bare Metal Operator pod logs (oc logs)
#   - Cluster etcd operator (CEO) pod logs (oc logs)
#   - Machine API / CAPI controller logs (capture-machine-api.sh: MAO, machine-api-controllers, CAPI operator)
#   - Machine API snapshots (capture-machine-api-snapshot.sh: Machine, BMH, events, clusteroperator machine-api)
#   - TNF fencing job pod logs (capture-fencing-job.sh)
#   - TNF update-setup job monitoring (capture-update-setup-job.sh: job status, pod creation/node, CEO errors)
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
# Optional: source proxy.env before running so KUBECONFIG is set for BMO and CEO.
# One-shot OVN/API/OVS check (no loop): OVN_CHASSIS_ONCE=1 bash capture-ovn-chassis-trace.sh
#
# Output: scratch/runs/{virsh,pacemaker-*,ovn-chassis-trace,ovn-k-*-follow,nnid-follow,baremetal-operator,ceo,machine-api-*,machine-api-snapshot,tnf-fencing-job-*,update-setup-job}-<timestamp>.log
# PIDs: scratch/runs/capture-pids-<timestamp>.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${CAPTURE_LOG_DIR:-${SCRATCH_ROOT}/runs}"
mkdir -p "${LOG_DIR}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date -u +%Y%m%d-%H%M%S)}"
PID_FILE="${LOG_DIR}/capture-pids-${TIMESTAMP}.txt"

# Source proxy.env first so we can default HYPERVISOR_IP from EC2_PUBLIC_IP (same as run-test.sh)
PROXY_ENV="${PROXY_ENV:?PROXY_ENV must be set to the cluster proxy.env path (e.g. <two-node-toolbox-deploy>/openshift-clusters/proxy.env)}"
if [[ -f "${PROXY_ENV}" ]]; then
    # shellcheck source=/dev/null
    source "${PROXY_ENV}"
    echo "Sourced ${PROXY_ENV} (KUBECONFIG set)"
fi

# When proxy.env was sourced, prefer EC2_PUBLIC_IP so captures match the cluster's hypervisor.
HYPERVISOR_IP="${EC2_PUBLIC_IP:-${HYPERVISOR_IP:-}}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
[[ -z "${SSH_KEY_PATH}" || ! -f "${SSH_KEY_PATH}" ]] && SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
export HYPERVISOR_IP SSH_USER SSH_KEY_PATH

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set. Export it or ensure proxy.env sets EC2_PUBLIC_IP."
    exit 1
fi

export CAPTURE_TIMESTAMP="${TIMESTAMP}"
echo "Starting captures with timestamp ${TIMESTAMP}"
echo "Logs -> ${LOG_DIR}"
echo ""

# Clear PID file
: > "${PID_FILE}"

# 1) virsh list on hypervisor
setsid "${SCRIPT_DIR}/capture-virsh-status.sh" >> "${LOG_DIR}/virsh-${TIMESTAMP}.out" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  virsh status     -> ${LOG_DIR}/virsh-${TIMESTAMP}.log (PID $!)"

# 2) Pacemaker log master-0
setsid "${SCRIPT_DIR}/capture-pacemaker.sh" master-0 >> "${LOG_DIR}/pacemaker-master-0-${TIMESTAMP}.out" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  pacemaker master-0 -> ${LOG_DIR}/pacemaker-master-0-${TIMESTAMP}.log (PID $!)"

# 3) Pacemaker log master-1 (reconnects after node comes back)
setsid "${SCRIPT_DIR}/capture-pacemaker.sh" master-1 >> "${LOG_DIR}/pacemaker-master-1-${TIMESTAMP}.out" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  pacemaker master-1 -> ${LOG_DIR}/pacemaker-master-1-${TIMESTAMP}.log (PID $!)"

# 4) Corosync log master-0
setsid "${SCRIPT_DIR}/capture-corosync.sh" master-0 >> "${LOG_DIR}/corosync-master-0-${TIMESTAMP}.out" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  corosync master-0 -> ${LOG_DIR}/corosync-master-0-${TIMESTAMP}.log (PID $!)"

# 5) Corosync log master-1
setsid "${SCRIPT_DIR}/capture-corosync.sh" master-1 >> "${LOG_DIR}/corosync-master-1-${TIMESTAMP}.out" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  corosync master-1 -> ${LOG_DIR}/corosync-master-1-${TIMESTAMP}.log (PID $!)"

# 6) OVN chassis / host OVS paper trail (API + SSH to masters; optional OVN-K log scrape on interval)
setsid bash "${SCRIPT_DIR}/capture-ovn-chassis-trace.sh" >> "${LOG_DIR}/ovn-chassis-trace-${TIMESTAMP}.log" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  ovn-chassis-trace -> ${LOG_DIR}/ovn-chassis-trace-${TIMESTAMP}.log (PID $!) [poll=${OVN_CHASSIS_POLL_INTERVAL_SEC:-2}s sb_every=${OVN_CHASSIS_SB_EVERY:-2} virsh_every=${OVN_CHASSIS_VIRSH_EVERY:-10} … see script header]"

# 7) disruption path evidence: debug/fencing events + ip6tables snapshots
setsid bash "${SCRIPT_DIR}/capture-disruption-evidence.sh" >> "${LOG_DIR}/disruption-evidence-${TIMESTAMP}.log" 2>&1 &
echo $! >> "${PID_FILE}"
echo "  disruption-evidence -> ${LOG_DIR}/disruption-evidence-${TIMESTAMP}.log (PID $!) [poll=${DISRUPTION_EVIDENCE_POLL_SEC:-10}s]"

# 4b) OVN-K follow logs (continuous, server timestamps) — correlate with ovn-chassis-trace samples
if [[ -n "${KUBECONFIG:-}" ]]; then
    setsid bash "${SCRIPT_DIR}/capture-ovn-kubernetes-follow-logs.sh" >> "${LOG_DIR}/ovn-k-node-follow-${TIMESTAMP}.log" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  ovn-k-node-follow -> ${LOG_DIR}/ovn-k-node-follow-${TIMESTAMP}.log (PID $!)"

    setsid bash "${SCRIPT_DIR}/capture-ovn-control-plane-follow-logs.sh" >> "${LOG_DIR}/ovn-k-cp-follow-${TIMESTAMP}.log" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  ovn-k-cp-follow -> ${LOG_DIR}/ovn-k-cp-follow-${TIMESTAMP}.log (PID $!)"

    setsid bash "${SCRIPT_DIR}/capture-network-node-identity-follow-logs.sh" >> "${LOG_DIR}/nnid-follow-${TIMESTAMP}.log" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  nnid-follow -> ${LOG_DIR}/nnid-follow-${TIMESTAMP}.log (PID $!) [exits if no network-node-identity deployment]"
else
    echo "  ovn-k / nnid follow -> skipped (KUBECONFIG not set)"
fi

# 5) Baremetal Operator (requires KUBECONFIG)
# Dev-scripts/metal3 use deployment name metal3-baremetal-operator; OCP uses baremetal-operator.
BMO_FOUND=""
if [[ -n "${KUBECONFIG:-}" ]]; then
    if oc get deployment/metal3-baremetal-operator -n openshift-machine-api &>/dev/null; then
        export BMO_NAMESPACE="openshift-machine-api" BMO_DEPLOY="metal3-baremetal-operator"
        BMO_FOUND=1
    elif oc get deployment/baremetal-operator -n openshift-machine-api &>/dev/null; then
        export BMO_NAMESPACE="openshift-machine-api" BMO_DEPLOY="baremetal-operator"
        BMO_FOUND=1
    elif oc get deployment/baremetal-operator -n openshift-baremetal-operator &>/dev/null; then
        export BMO_NAMESPACE="openshift-baremetal-operator" BMO_DEPLOY="baremetal-operator"
        BMO_FOUND=1
    fi
fi
if [[ -n "${BMO_FOUND}" ]]; then
    setsid "${SCRIPT_DIR}/capture-baremetal-operator.sh" >> "${LOG_DIR}/baremetal-operator-${TIMESTAMP}.out" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  baremetal-operator -> ${LOG_DIR}/baremetal-operator-${TIMESTAMP}.log (PID $!) [${BMO_NAMESPACE}/${BMO_DEPLOY}]"
else
    if [[ -z "${KUBECONFIG:-}" ]]; then
        echo "  baremetal-operator -> skipped (KUBECONFIG not set)"
    else
        echo "  baremetal-operator -> skipped (no BMO deployment found in openshift-machine-api or openshift-baremetal-operator)"
    fi
fi

# 6) CEO (optional, same as capture-ceo-logs.sh)
if [[ -n "${KUBECONFIG:-}" ]] && oc get deployment/etcd-operator -n openshift-etcd-operator &>/dev/null; then
    setsid bash -c "oc logs -n openshift-etcd-operator deployment/etcd-operator -f --timestamps 2>&1 | tee -a '${LOG_DIR}/ceo-${TIMESTAMP}.log'" >> "${LOG_DIR}/ceo-${TIMESTAMP}.out" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  CEO logs        -> ${LOG_DIR}/ceo-${TIMESTAMP}.log (PID $!)"
else
    echo "  CEO logs        -> skipped (KUBECONFIG not set or etcd-operator not found)"
fi

# 7) Machine API / CAPI controller logs (Machine delete, finalizers, provider)
if [[ -n "${KUBECONFIG:-}" ]]; then
    setsid "${SCRIPT_DIR}/capture-machine-api.sh" >> "${LOG_DIR}/machine-api-streams-${TIMESTAMP}.out" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  machine-api     -> ${LOG_DIR}/machine-api-*-${TIMESTAMP}.log (PID $!) [MAO / mac-controllers / CBO / cluster-api if present]"
else
    echo "  machine-api     -> skipped (KUBECONFIG not set)"
fi

# 8) Periodic Machine / BMH / events snapshots
if [[ -n "${KUBECONFIG:-}" ]]; then
    setsid bash "${SCRIPT_DIR}/capture-machine-api-snapshot.sh" >> "${LOG_DIR}/machine-api-snapshot-${TIMESTAMP}.log" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  machine-api-snapshot -> ${LOG_DIR}/machine-api-snapshot-${TIMESTAMP}.log (PID $!) [poll=${MACHINE_API_SNAPSHOT_POLL_SEC:-60}s]"
else
    echo "  machine-api-snapshot -> skipped (KUBECONFIG not set)"
fi

# 9) TNF fencing job pod logs (direct job stdout/stderr in openshift-etcd)
if [[ -n "${KUBECONFIG:-}" ]]; then
    setsid bash "${SCRIPT_DIR}/capture-fencing-job.sh" >> "${LOG_DIR}/fencing-job-streams-${TIMESTAMP}.out" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  tnf-fencing-job  -> ${LOG_DIR}/tnf-fencing-job-<pod>-${TIMESTAMP}.log (PID $!) [poll=${FENCING_POLL_SEC:-10}s]"
else
    echo "  tnf-fencing-job  -> skipped (KUBECONFIG not set)"
fi

# 10) TNF update-setup job monitoring (job status, pod creation, CEO errors)
if [[ -n "${KUBECONFIG:-}" ]]; then
    setsid bash "${SCRIPT_DIR}/capture-update-setup-job.sh" >> "${LOG_DIR}/update-setup-job-${TIMESTAMP}.out" 2>&1 &
    echo $! >> "${PID_FILE}"
    echo "  update-setup-job -> ${LOG_DIR}/update-setup-job-${TIMESTAMP}.log (PID $!) [poll=${UPDATE_SETUP_POLL_SEC:-10}s]"
else
    echo "  update-setup-job -> skipped (KUBECONFIG not set)"
fi

echo ""
echo "All captures started. Run your node replacement test in another terminal."
echo "When the test ends, stop captures with:"
echo "  while read -r p; do kill \$p 2>/dev/null; done < ${PID_FILE}"
echo "Or: xargs kill < ${PID_FILE}"
