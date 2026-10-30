#!/usr/bin/bash
# Stream /var/log/pacemaker/pacemaker.log from one master. Reconnects when the node
# goes away (e.g. during replacement) so we capture log after it comes back.
#
# Masters are resolved from live libvirt DHCP leases on the hypervisor by default.
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
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ssh-helpers.sh"
setup_ssh_config
MASTER_SSH_USER="${MASTER_SSH_USER:-core}"
MASTER_0_IP="${MASTER_0_IP:-}"
MASTER_1_IP="${MASTER_1_IP:-}"
VIRSH_LEASE_NETWORK="${VIRSH_LEASE_NETWORK:-ostestbm}"
RECONNECT_SLEEP="${PACEMAKER_RECONNECT_SLEEP:-15}"
if [[ ! "${RECONNECT_SLEEP}" =~ ^0*[1-9][0-9]*$ ]]; then
    echo "Error: PACEMAKER_RECONNECT_SLEEP must be a positive integer (seconds)." >&2
    exit 1
fi

# Nested SSH: local -> hypervisor.
# Keepalives + BatchMode so a hung or half-open hop returns control to the reconnect
# loop (e.g. when a node is ungracefully killed) instead of blocking indefinitely.
setup_hypervisor_ssh 12 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o BatchMode=yes
setup_guest_ssh_options

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/libvirt-helpers.sh"

# Honor an explicit override IP if provided; otherwise resolve from DHCP leases and
# keep re-resolving in the reconnect loop (a rebooted node may get a new lease).
if [[ "${NODE}" == "master-0" ]]; then
    EXPLICIT_IP="${MASTER_0_IP}"
else
    EXPLICIT_IP="${MASTER_1_IP}"
fi
if [[ -n "${EXPLICIT_IP}" ]]; then
    MASTER_IP="${EXPLICIT_IP}"
else
    MASTER_IP="$(resolve_master_ip_from_leases "${NODE}" || true)"
fi

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set."
    exit 1
fi
if [[ -z "${MASTER_IP}" ]]; then
    echo "Warning: could not resolve ${NODE} IP yet from libvirt DHCP leases ${VIRSH_LEASE_NETWORK}; will keep retrying (set MASTER_0_IP/MASTER_1_IP to override)."
fi

echo "Tailing pacemaker.log on ${NODE} (${MASTER_SSH_USER}@${MASTER_IP}) -> ${LOG_FILE}"
echo "Connection will drop when the node is destroyed; will reconnect every ${RECONNECT_SLEEP}s."
echo "Stop with Ctrl+C when the test ends."
echo ""

trap 'exit 0' INT TERM

while true; do
    # Re-resolve the lease each iteration unless an explicit IP was given: after a
    # reboot the node may return on a different DHCP address, and a stale IP would
    # never reconnect. Keep the last known IP if resolution transiently fails.
    if [[ -z "${EXPLICIT_IP}" ]]; then
        new_ip="$(resolve_master_ip_from_leases "${NODE}" || true)"
        if [[ -n "${new_ip}" && "${new_ip}" != "${MASTER_IP}" ]]; then
            echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ${NODE} IP changed ${MASTER_IP:-none} -> ${new_ip} ===" >> "${LOG_FILE}"
            MASTER_IP="${new_ip}"
        fi
    fi

    if [[ -z "${MASTER_IP}" ]]; then
        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ${NODE} IP unresolved, retrying in ${RECONNECT_SLEEP}s ===" >> "${LOG_FILE}"
        sleep "${RECONNECT_SLEEP}"
        continue
    fi

    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) connected to ${NODE} (${MASTER_IP}) ===" >> "${LOG_FILE}"
    run_on_hypervisor ssh "${INNER_SSH_OPTS[@]}" -- "${MASTER_SSH_USER}@${MASTER_IP}" \
        'sudo tail -f /var/log/pacemaker/pacemaker.log' \
        >> "${LOG_FILE}" 2>&1 || true
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) disconnected from ${NODE}, reconnecting in ${RECONNECT_SLEEP}s ===" >> "${LOG_FILE}"
    sleep "${RECONNECT_SLEEP}"
done
