#!/usr/bin/bash
# Stream /var/log/pacemaker/pacemaker.log from one master. Reconnects when the node
# goes away (e.g. during replacement) so we capture log after it comes back.
#
# Masters are resolved from live libvirt DHCP leases on the hypervisor by default.
#
# Usage: scripts/captures/capture-pacemaker.sh <master-0|master-1>
#
# Requires: HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH. Optional: MASTER_0_IP, MASTER_1_IP,
#           MASTER_SSH_USER (default: core). Uses nested SSH via hypervisor to reach master.
#
# Output: scripts/debug/pacemaker-<node>-<timestamp>.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-master-log.sh"

capture_master_log "${1:-}" pacemaker PACEMAKER_RECONNECT_SLEEP 'pacemaker.log' \
    'sudo tail -f /var/log/pacemaker/pacemaker.log'
