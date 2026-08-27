#!/usr/bin/bash
# Poll virsh list on the hypervisor so VM up/downtime can be aligned with test phases.
# Run in background before starting the node replacement test; stop when the test ends.
#
# Requires: HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH (same as run-test.sh).
#
# Output: scratch/debug/virsh-<timestamp>.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/debug"
mkdir -p "${LOG_DIR}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
LOG_FILE="${LOG_DIR}/virsh-${TIMESTAMP}.log"

HYPERVISOR_IP="${HYPERVISOR_IP:-}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
[[ -z "${SSH_KEY_PATH}" || ! -f "${SSH_KEY_PATH}" ]] && SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
POLL_INTERVAL="${VIRSH_POLL_INTERVAL:-15}"

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set. Export it (e.g. from run-test.sh)."
    exit 1
fi

echo "Polling virsh on ${SSH_USER}@${HYPERVISOR_IP} every ${POLL_INTERVAL}s -> ${LOG_FILE}"
echo "Stop with Ctrl+C when the test ends."
echo ""

trap 'exit 0' INT TERM

while true; do
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "${LOG_FILE}"
    ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -i "${SSH_KEY_PATH}" \
        "${SSH_USER}@${HYPERVISOR_IP}" \
        "virsh -c qemu:///system list --all" >> "${LOG_FILE}" 2>&1 || true
    echo "" >> "${LOG_FILE}"
    sleep "${POLL_INTERVAL}"
done
