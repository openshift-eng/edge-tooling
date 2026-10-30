#!/usr/bin/bash
# Capture evidence for disruption effectiveness:
# - debug/fencing pods and relevant openshift-etcd events
# - ip6tables counters/rules on master-0 and master-1
#
# Output is written to stdout and intended to be redirected by run-all-captures.sh.

set -euo pipefail

HYPERVISOR_IP="${HYPERVISOR_IP:-}"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ssh-helpers.sh"
setup_ssh_config
MASTER_SSH_USER="${MASTER_SSH_USER:-core}"
MASTER_0_IP="${MASTER_0_IP:-}"
MASTER_1_IP="${MASTER_1_IP:-}"
VIRSH_LEASE_NETWORK="${VIRSH_LEASE_NETWORK:-ostestbm}"
POLL_SEC="${DISRUPTION_EVIDENCE_POLL_SEC:-10}"
if [[ ! "${POLL_SEC}" =~ ^0*[1-9][0-9]*$ ]]; then
    echo "Error: DISRUPTION_EVIDENCE_POLL_SEC must be a positive integer (seconds)." >&2
    exit 1
fi

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set."
    exit 1
fi

setup_hypervisor_ssh 12

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/libvirt-helpers.sh"

[[ -z "${MASTER_0_IP}" ]] && MASTER_0_IP="$(resolve_master_ip_from_leases "master-0" || true)"
[[ -z "${MASTER_1_IP}" ]] && MASTER_1_IP="$(resolve_master_ip_from_leases "master-1" || true)"

echo "Disruption evidence capture started: poll=${POLL_SEC}s"
echo "master-0=${MASTER_0_IP} master-1=${MASTER_1_IP}"

trap 'exit 0' INT TERM

while true; do
    echo
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) disruption-evidence ====="
    echo "--- openshift-etcd pods (debug/fencing/status-collector) ---"
    oc get pods -n openshift-etcd -o wide 2>&1 | awk 'NR==1 || /debug|tnf-fencing-job|pacemaker-status-collector/'
    echo "--- openshift-etcd events (disruption/fencing related) ---"
    oc get events -n openshift-etcd --sort-by=.lastTimestamp 2>&1 | awk 'NR==1 || /debug|fenc|stonith|master-0|master-1|network|disrupt/'

    for node in master-0 master-1; do
        ip_var="${node/master-/MASTER_}_IP"
        target="${!ip_var:-}"
        if [[ -z "${target}" ]]; then
            echo "--- ${node}: unresolved IP, skipping ip6tables snapshot ---"
            continue
        fi
        echo "--- ${node} (${target}) ip6tables counters ---"
        run_on_hypervisor ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o LogLevel=ERROR -o ConnectTimeout=10 -- "${MASTER_SSH_USER}@${target}" \
            'sudo ip6tables -nvL INPUT; echo; sudo ip6tables -nvL OUTPUT' 2>&1 || true
    done

    sleep "${POLL_SEC}"
done
