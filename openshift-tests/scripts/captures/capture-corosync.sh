#!/usr/bin/bash
# Stream corosync unit journal from one master. Reconnects when the node goes away
# so we keep collecting membership/token messages after the node comes back.
#
# Masters are resolved from live libvirt DHCP leases on the hypervisor by default.
#
# Usage: scripts/captures/capture-corosync.sh <master-0|master-1>
#
# Requires: HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH. Optional: MASTER_0_IP, MASTER_1_IP,
#           MASTER_SSH_USER (default: core). Uses nested SSH via hypervisor to reach master.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-master-log.sh"

capture_master_log "${1:-}" corosync COROSYNC_RECONNECT_SLEEP 'corosync journal' \
    'sudo journalctl -u corosync -f -n 200 -o short-iso'
