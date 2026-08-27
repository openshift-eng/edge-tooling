#!/usr/bin/bash
# Stream /var/log/pacemaker/pacemaker.log from one master. Reconnects when the node
# goes away (e.g. during replacement) so we capture log after it comes back.
#
# Masters are resolved from virsh net-dhcp-leases on the hypervisor by default.
#
# Usage: capture-pacemaker.sh <master-0|master-1>
#
# Requires: HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH. Optional: MASTER_0_IP, MASTER_1_IP,
#           MASTER_SSH_USER (default: core). Uses nested SSH via hypervisor to reach master.
#
# Output: scratch/debug/pacemaker-<node>-<timestamp>.log

set -euo pipefail

NODE="${1:-}"
if [[ -z "${NODE}" ]] || [[ ! "${NODE}" =~ ^master-[01]$ ]]; then
    echo "Usage: $0 master-0|master-1"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${CAPTURE_LOG_DIR:-${SCRIPT_DIR}/debug}"
mkdir -p "${LOG_DIR}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
LOG_FILE="${LOG_DIR}/pacemaker-${NODE}-${TIMESTAMP}.log"

HYPERVISOR_IP="${HYPERVISOR_IP:-}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
[[ -z "${SSH_KEY_PATH}" || ! -f "${SSH_KEY_PATH}" ]] && SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
MASTER_SSH_USER="${MASTER_SSH_USER:-core}"
MASTER_0_IP="${MASTER_0_IP:-}"
MASTER_1_IP="${MASTER_1_IP:-}"
VIRSH_LEASE_NETWORK="${VIRSH_LEASE_NETWORK:-ostestbm}"
RECONNECT_SLEEP="${PACEMAKER_RECONNECT_SLEEP:-15}"

# Nested SSH: local -> hypervisor
HYPERVISOR_SSH=(ssh -o "ConnectTimeout=12" -o "StrictHostKeyChecking=no" -i "${SSH_KEY_PATH}" "${SSH_USER}@${HYPERVISOR_IP}")

resolve_master_ip_from_leases() {
    local node_name="$1"
    "${HYPERVISOR_SSH[@]}" "virsh -c qemu:///system net-dhcp-leases \"${VIRSH_LEASE_NETWORK}\" 2>/dev/null" \
        | awk -v node="${node_name}" '
            $0 ~ /^[[:space:]]*$/ { next }
            $0 ~ /^[[:space:]]*Expiry/ { next }
            $0 ~ /^[[:space:]]*-+/ { next }
            index($0, node) {
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^[0-9a-fA-F:.]+\/[0-9]+$/) {
                        split($i, a, "/")
                        print a[1]
                        exit
                    }
                }
            }
        '
}

if [[ "${NODE}" == "master-0" ]]; then
    if [[ -z "${MASTER_0_IP}" ]]; then
        MASTER_0_IP="$(resolve_master_ip_from_leases "master-0" || true)"
    fi
    MASTER_IP="${MASTER_0_IP}"
else
    if [[ -z "${MASTER_1_IP}" ]]; then
        MASTER_1_IP="$(resolve_master_ip_from_leases "master-1" || true)"
    fi
    MASTER_IP="${MASTER_1_IP}"
fi

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set."
    exit 1
fi
if [[ -z "${MASTER_IP}" ]]; then
    echo "Error: could not resolve ${NODE} IP from virsh net-dhcp-leases ${VIRSH_LEASE_NETWORK}; set MASTER_0_IP/MASTER_1_IP explicitly."
    exit 1
fi

echo "Tailing pacemaker.log on ${NODE} (${MASTER_SSH_USER}@${MASTER_IP}) -> ${LOG_FILE}"
echo "Connection will drop when the node is destroyed; will reconnect every ${RECONNECT_SLEEP}s."
echo "Stop with Ctrl+C when the test ends."
echo ""

trap 'exit 0' INT TERM

while true; do
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) connected to ${NODE} ===" >> "${LOG_FILE}"
    if "${HYPERVISOR_SSH[@]}" \
        "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=30 ${MASTER_SSH_USER}@${MASTER_IP} 'sudo tail -f /var/log/pacemaker/pacemaker.log'" \
        >> "${LOG_FILE}" 2>&1; then
        :
    fi
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) disconnected from ${NODE}, reconnecting in ${RECONNECT_SLEEP}s ===" >> "${LOG_FILE}"
    sleep "${RECONNECT_SLEEP}"
done
