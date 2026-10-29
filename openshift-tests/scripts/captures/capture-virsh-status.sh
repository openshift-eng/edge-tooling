#!/usr/bin/bash
# Poll libvirt snapshots so VM up/downtime can be aligned with test phases.
# Run in background before starting the node replacement test; stop when the test ends.
#
# Requires: HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH (same as run-test.sh).
#
# Output: scripts/debug/virsh-<timestamp>.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-helpers.sh"
setup_capture_output "${SCRIPT_DIR}/../debug"
LOG_FILE="${LOG_DIR}/virsh-${TIMESTAMP}.log"

HYPERVISOR_IP="${HYPERVISOR_IP:-}"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../utils/ssh-helpers.sh"
setup_ssh_config
POLL_INTERVAL="${VIRSH_POLL_INTERVAL:-15}"
require_capture_interval "${POLL_INTERVAL}" VIRSH_POLL_INTERVAL

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set. Export it (e.g. from run-test.sh)."
    exit 1
fi

setup_hypervisor_ssh 10
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/libvirt-helpers.sh"

echo "Polling libvirt on ${SSH_USER}@${HYPERVISOR_IP} every ${POLL_INTERVAL}s -> ${LOG_FILE}"
echo "Stop with Ctrl+C when the test ends."
echo ""

trap 'exit 0' INT TERM

while true; do
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "${LOG_FILE}"
    libvirt_domain_snapshot >> "${LOG_FILE}" 2>&1 || true
    echo "" >> "${LOG_FILE}"
    sleep "${POLL_INTERVAL}"
done
