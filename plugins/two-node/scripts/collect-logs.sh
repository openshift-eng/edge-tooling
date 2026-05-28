#!/usr/bin/bash
# collect-logs.sh — Collect podman-etcd and pacemaker logs from cluster nodes
#
# Usage:
#   ./scripts/collect-logs.sh [minutes-ago] [output-dir]
#
# Arguments:
#   minutes-ago  — How far back to collect logs (default: 30)
#   output-dir   — Where to save logs (default: /tmp/bugfix-verify-logs)
#
# Environment:
#   HYPERVISOR  — IP of the hypervisor (auto-detected from two-node-toolbox if available)
#
# Collects from both nodes:
#   - podman-etcd resource agent logs from pacemaker.log
#   - etcd container logs (saved snapshot, member operations)
#   - Full pacemaker journal (last N minutes)
#   - etcd initial cluster config from pacemaker.log

# shellcheck disable=SC2029
set -euo pipefail

MINUTES_AGO="${1:-30}"
OUTPUT_BASE="${2:-/tmp/bugfix-verify-logs}"
if ! [[ "$MINUTES_AGO" =~ ^[0-9]+$ ]]; then
    echo "ERROR: minutes-ago must be a non-negative integer"
    exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/../../.."
TNT_DEPLOY_DIR="${REPO_ROOT}/two-node-toolbox/deploy"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_DIR="${OUTPUT_BASE}/${TIMESTAMP}"

MASTER_0="192.168.111.20"
MASTER_1="192.168.111.21"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

if [ -z "${HYPERVISOR:-}" ]; then
    if [ -d "$TNT_DEPLOY_DIR" ]; then
        HYPERVISOR=$(cd "$TNT_DEPLOY_DIR" && make info 2>/dev/null | grep "Host:" | awk '{print $2}')
    fi
    HYPERVISOR="${HYPERVISOR:?Set HYPERVISOR env var or ensure two-node-toolbox submodule is available}"
fi

mkdir -p "$LOG_DIR"

echo "=== Collecting logs (last ${MINUTES_AGO} minutes) ==="
echo "=== Output: ${LOG_DIR} ==="
echo ""

for node in "$MASTER_0" "$MASTER_1"; do
    node_name="master-$(echo "$node" | awk -F. '{print $4 - 20}')"
    echo "--- ${node_name} ---"

    # podman-etcd resource agent logs
    echo "  Collecting podman-etcd logs..."
    ssh "ec2-user@${HYPERVISOR}" "
        ssh ${SSH_OPTS} core@${node} 'sudo grep \"podman-etcd(etcd)\" /var/log/pacemaker/pacemaker.log | tail -100'
    " 2>/dev/null > "${LOG_DIR}/${node_name}-podman-etcd.log" \
        || echo "  WARNING: failed to collect podman-etcd logs from ${node_name}"

    # etcd container logs (snapshots, member operations)
    echo "  Collecting etcd container logs..."
    ssh "ec2-user@${HYPERVISOR}" "
        ssh ${SSH_OPTS} core@${node} 'sudo podman logs etcd 2>&1 | grep -E \"saved snapshot|member|learner|peerURL|force_new_cluster\" | tail -50'
    " 2>/dev/null > "${LOG_DIR}/${node_name}-etcd-container.log" \
        || echo "  WARNING: failed to collect etcd container logs from ${node_name}"

    # Full pacemaker journal
    echo "  Collecting pacemaker journal..."
    ssh "ec2-user@${HYPERVISOR}" "
        ssh ${SSH_OPTS} core@${node} 'sudo journalctl -u pacemaker --no-pager --since \"${MINUTES_AGO} minutes ago\"'
    " 2>/dev/null > "${LOG_DIR}/${node_name}-pacemaker-journal.log" \
        || echo "  WARNING: failed to collect pacemaker journal from ${node_name}"

    # ETCD initial cluster config
    echo "  Collecting etcd config logs..."
    ssh "ec2-user@${HYPERVISOR}" "
        ssh ${SSH_OPTS} core@${node} 'sudo grep -E \"ETCD_NAME|ETCD_INITIAL_CLUSTER|ETCD_INITIAL_ADVERTISE|ETCD_INITIAL_CLUSTER_STATE\" /var/log/pacemaker/pacemaker.log | tail -10'
    " 2>/dev/null > "${LOG_DIR}/${node_name}-etcd-config.log" \
        || echo "  WARNING: failed to collect etcd config logs from ${node_name}"

    echo ""
done

echo "=== Log files ==="
ls -la "$LOG_DIR/"
echo ""
echo "=== Done ==="
