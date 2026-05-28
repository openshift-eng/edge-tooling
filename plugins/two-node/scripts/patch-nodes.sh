#!/usr/bin/bash
# patch-nodes.sh — Patch resource-agents RPM on both cluster nodes
#
# Usage:
#   ./scripts/patch-nodes.sh <path-to-rpm> [grep-pattern-for-fix]
#
# Environment:
#   HYPERVISOR  — IP of the hypervisor (auto-detected from two-node-toolbox if available)
#
# This script:
#   1. Copies the RPM to the hypervisor
#   2. Distributes it to both cluster nodes
#   3. Applies rpm-ostree override replace -C (persistent, survives reboots)
#   4. Reboots both nodes
#   5. Verifies the RPM version after reboot
#   6. Optionally verifies fix code presence

# shellcheck disable=SC2029
set -euo pipefail

RPM_PATH="${1:?Usage: $0 <path-to-rpm> [grep-pattern-for-fix]}"
FIX_GREP_PATTERN="${2:-}"

RPM_FILE=$(basename "$RPM_PATH")
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
echo "=== RPM: $RPM_FILE ==="
echo ""

# Step 1: Copy RPM to hypervisor
echo "=== Copying RPM to hypervisor ==="
scp "$RPM_PATH" "ec2-user@${HYPERVISOR}:/tmp/${RPM_FILE}"
echo ""

# Step 2: Distribute to both nodes
echo "=== Copying RPM to cluster nodes ==="
ssh "ec2-user@${HYPERVISOR}" "
    scp ${SSH_OPTS} /tmp/${RPM_FILE} core@${MASTER_0}:/tmp/ && echo 'master-0 OK'
    scp ${SSH_OPTS} /tmp/${RPM_FILE} core@${MASTER_1}:/tmp/ && echo 'master-1 OK'
"
echo ""

# Step 3: Apply persistent override
echo "=== Applying rpm-ostree override replace -C ==="
for node in "$MASTER_0" "$MASTER_1"; do
    node_name="master-$(echo "$node" | awk -F. '{print $4 - 20}')"
    echo "--- ${node_name} ---"
    raw_output="$(ssh "ec2-user@${HYPERVISOR}" \
        "ssh ${SSH_OPTS} core@${node} 'sudo rpm-ostree override replace /tmp/${RPM_FILE} -C'" 2>&1)"
    rc=$?
    printf '%s\n' "$raw_output" | grep -v '^Warning:' || true
    if [ "$rc" -ne 0 ]; then
        echo "ERROR: rpm-ostree override failed on ${node_name}"
        exit "$rc"
    fi
    echo ""
done

# Step 4: Reboot both nodes
echo "=== Rebooting both nodes ==="
ssh "ec2-user@${HYPERVISOR}" "
    ssh ${SSH_OPTS} core@${MASTER_0} 'sudo systemctl reboot' 2>/dev/null &
    ssh ${SSH_OPTS} core@${MASTER_1} 'sudo systemctl reboot' 2>/dev/null &
    wait
    echo 'Reboot commands sent'
"
echo ""

# Step 5: Wait for nodes to come back
echo "=== Waiting for nodes to come back ==="
for i in $(seq 1 60); do
    m0=$(ssh "ec2-user@${HYPERVISOR}" "ssh ${SSH_OPTS} -o ConnectTimeout=3 core@${MASTER_0} 'echo up'" 2>/dev/null || true)
    m1=$(ssh "ec2-user@${HYPERVISOR}" "ssh ${SSH_OPTS} -o ConnectTimeout=3 core@${MASTER_1} 'echo up'" 2>/dev/null || true)
    if [ "$m0" = "up" ] && [ "$m1" = "up" ]; then
        echo "  Both nodes are back up after $((i*10)) seconds"
        nodes_up=true
        break
    fi
    if [ $((i % 3)) -eq 0 ]; then
        echo "  [$((i*10))s] master-0=$m0 master-1=$m1"
    fi
    sleep 10
done

if [ "${nodes_up:-}" != "true" ]; then
    echo "ERROR: Nodes did not come back up within 10 minutes"
    exit 1
fi
echo ""

# Step 6: Verify RPM version
echo "=== Verifying RPM version ==="
for node in "$MASTER_0" "$MASTER_1"; do
    node_name="master-$(echo "$node" | awk -F. '{print $4 - 20}')"
    version=$(ssh "ec2-user@${HYPERVISOR}" "ssh ${SSH_OPTS} core@${node} 'rpm -qa | grep resource-agents'" 2>/dev/null || true)
    if [ -z "$version" ]; then
        echo "  ERROR: failed to verify RPM version on ${node_name}"
        exit 1
    fi
    echo "  ${node_name}: ${version}"
done
echo ""

# Step 7: Verify fix code (optional)
if [ -n "$FIX_GREP_PATTERN" ]; then
    echo "=== Verifying fix code presence ==="
    for node in "$MASTER_0" "$MASTER_1"; do
        node_name="master-$(echo "$node" | awk -F. '{print $4 - 20}')"
        echo "--- ${node_name} ---"
        printf '%s\n' "$FIX_GREP_PATTERN" | \
            ssh "ec2-user@${HYPERVISOR}" "
                ssh ${SSH_OPTS} core@${node} 'grep -nf /dev/stdin /usr/lib/ocf/resource.d/heartbeat/podman-etcd'
            " 2>/dev/null \
            || echo "  WARNING: fix pattern not found on ${node_name}"
    done
    echo ""
fi

echo "=== Patching complete ==="
