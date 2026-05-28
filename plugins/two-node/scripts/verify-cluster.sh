#!/usr/bin/bash
# verify-cluster.sh — Check cluster health (OCP, pacemaker, etcd)
#
# Usage:
#   ./scripts/verify-cluster.sh
#
# Environment:
#   HYPERVISOR  — IP of the hypervisor (auto-detected from two-node-toolbox if available)
#
# Checks:
#   - OCP cluster version (Available, not Progressing)
#   - Node status
#   - Cluster operators not available
#   - Pacemaker status (pcs status)
#   - etcd member list
#   - etcd endpoint health
#   - resource-agents RPM version on both nodes

# shellcheck disable=SC2029
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/../../.."
TNT_DEPLOY_DIR="${REPO_ROOT}/two-node-toolbox/deploy"

MASTER_0="192.168.111.20"
MASTER_1="192.168.111.21"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

if [ -z "${HYPERVISOR:-}" ]; then
    if [ -d "$TNT_DEPLOY_DIR" ]; then
        HYPERVISOR=$(cd "$TNT_DEPLOY_DIR" && make info 2>/dev/null | grep "Host:" | awk '{print $2}')
    fi
    HYPERVISOR="${HYPERVISOR:?Set HYPERVISOR env var or ensure two-node-toolbox submodule is available}"
fi

echo "=== Hypervisor: $HYPERVISOR ==="
echo ""

ssh "ec2-user@${HYPERVISOR}" "
    export KUBECONFIG=/home/ec2-user/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig

    echo '=== Cluster Version ==='
    oc get clusterversion 2>&1
    echo ''

    echo '=== Nodes ==='
    oc get nodes 2>&1
    echo ''

    echo '=== Cluster Operators Not Healthy ==='
    oc get co 2>&1 | awk 'NR==1 || \$3 != \"True\" || \$4 != \"False\" || \$5 != \"False\"' | head -20
    echo ''

    echo '=== OS Version ==='
    echo -n 'master-0: ' && ssh ${SSH_OPTS} core@${MASTER_0} 'grep PRETTY_NAME /etc/os-release' 2>/dev/null
    echo -n 'master-1: ' && ssh ${SSH_OPTS} core@${MASTER_1} 'grep PRETTY_NAME /etc/os-release' 2>/dev/null
    echo ''

    echo '=== resource-agents RPM ==='
    echo -n 'master-0: ' && ssh ${SSH_OPTS} core@${MASTER_0} 'rpm -qa | grep resource-agents' 2>/dev/null
    echo -n 'master-1: ' && ssh ${SSH_OPTS} core@${MASTER_1} 'rpm -qa | grep resource-agents' 2>/dev/null
    echo ''

    echo '=== PCS Status ==='
    ssh ${SSH_OPTS} core@${MASTER_0} 'sudo pcs status' 2>/dev/null
    echo ''

    echo '=== etcd Member List ==='
    ssh ${SSH_OPTS} core@${MASTER_0} 'sudo podman exec etcd etcdctl member list -w table' 2>/dev/null
    echo ''

    echo '=== etcd Endpoint Health ==='
    ssh ${SSH_OPTS} core@${MASTER_0} 'sudo podman exec etcd etcdctl endpoint health --cluster -w table' 2>/dev/null || true
" 2>/dev/null
