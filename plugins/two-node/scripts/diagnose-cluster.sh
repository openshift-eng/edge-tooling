#!/usr/bin/bash
# diagnose-cluster.sh — Gather shutdown/recovery diagnostic state from a TNF cluster
#
# Usage:
#   ./scripts/diagnose-cluster.sh
#
# Environment:
#   NODE_0, NODE_1  — Direct SSH targets (bare metal, takes precedence)
#   HYPERVISOR      — IP of hypervisor (auto-detected from two-node-toolbox if available)
#   BMC_0, BMC_1    — Optional BMC addresses for Redfish power state
#   BMC_USER, BMC_PASS — BMC credentials (required if BMC_0/BMC_1 set)
#   SSH_KEY         — Path to SSH private key (default: ~/.ssh/id_rsa if it exists)
#   KUBECONFIG      — Path to kubeconfig (bare metal mode; hypervisor mode auto-detects)
#   DEBUG           — Set to "1" to show SSH/command stderr (default: suppressed)
#
# Collects:
#   - OCP version, node status/conditions, cluster operators, pending CSRs
#   - PCS status, properties, failcounts, standby state, stonith config/history
#   - CIB recovery attributes (standalone_node, learner_node, force_new_cluster)
#   - Cluster ID comparison (split-brain detection)
#   - Pacemaker constraints, resource location bans, corosync status/quorum
#   - Three network paths: cluster, management/BMC, application
#   - etcd container state (podman), member list, endpoint health, DB size, alarms
#   - Node uptime, disk space, resource-agents RPM
#   - Recent non-Normal events, BMC power state (optional)

# shellcheck disable=SC2029
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/../../.."
TNT_DEPLOY_DIR="${REPO_ROOT}/two-node-toolbox/deploy"

if [ -z "${SSH_KEY:-}" ] && [ -f "$HOME/.ssh/id_rsa" ]; then
    SSH_KEY="$HOME/.ssh/id_rsa"
fi

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
SSH_OPTS_STR="${SSH_OPTS[*]}"
STDERR_REDIRECT="/dev/null"
[ "${DEBUG:-}" = "1" ] && STDERR_REDIRECT="/dev/stderr"
ACCESS_MODE=""

# --- Access pattern detection ---

if [ -n "${NODE_0:-}" ] && [ -n "${NODE_1:-}" ]; then
    ACCESS_MODE="direct"
elif [ -n "${HYPERVISOR:-}" ]; then
    ACCESS_MODE="hypervisor"
    NODE_0="192.168.111.20"
    NODE_1="192.168.111.21"
else
    if [ -d "$TNT_DEPLOY_DIR" ]; then
        HYPERVISOR=$(cd "$TNT_DEPLOY_DIR" && make info 2>/dev/null | grep "Host:" | awk '{print $2}') || true
    fi
    if [ -n "${HYPERVISOR:-}" ]; then
        ACCESS_MODE="hypervisor"
        NODE_0="192.168.111.20"
        NODE_1="192.168.111.21"
    else
        echo "ERROR: No access method available." >&2
        echo "Set NODE_0/NODE_1 (bare metal) or HYPERVISOR (dev-scripts), or ensure two-node-toolbox is available." >&2
        exit 1
    fi
fi

# --- Helper functions ---

run_on_node() {
    local node="$1"; shift
    if [ "$node" = "$NODE_1" ] && [ "$NODE_1_REACHABLE" = "false" ]; then
        return 1
    fi
    if [ "$ACCESS_MODE" = "direct" ]; then
        ssh "${SSH_OPTS[@]}" "core@${node}" "$@" 2>"$STDERR_REDIRECT"
    else
        local escaped_cmd
        escaped_cmd=$(printf '%q ' "$@")
        ssh "${SSH_OPTS[@]}" "ec2-user@${HYPERVISOR}" "ssh ${SSH_OPTS_STR} core@${node} ${escaped_cmd}" 2>"$STDERR_REDIRECT"
    fi
}

run_oc() {
    if [ "$ACCESS_MODE" = "direct" ]; then
        if [ -n "${KUBECONFIG:-}" ]; then
            KUBECONFIG="$KUBECONFIG" oc "$@" 2>"$STDERR_REDIRECT"
        else
            oc "$@" 2>"$STDERR_REDIRECT"
        fi
    else
        ssh "${SSH_OPTS[@]}" "ec2-user@${HYPERVISOR}" \
            "export KUBECONFIG=/home/ec2-user/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig; oc $*" 2>"$STDERR_REDIRECT"
    fi
}

# --- Data collection ---

echo "=== Access Mode ==="
echo "Mode: ${ACCESS_MODE}"
echo "Node 0: ${NODE_0}"
echo "Node 1: ${NODE_1}"
[ "$ACCESS_MODE" = "hypervisor" ] && echo "Hypervisor: ${HYPERVISOR}"
echo ""

# --- Pre-check connectivity ---

NODE_1_REACHABLE=true
echo "Verifying connectivity..."
if ! run_on_node "$NODE_0" "true" 2>&1; then
    echo "ERROR: Cannot reach Node 0 (${NODE_0})" >&2
    exit 1
fi
if ! run_on_node "$NODE_1" "true" 2>&1; then
    echo "WARNING: Cannot reach Node 1 (${NODE_1}) — collecting Node 0 data only" >&2
    NODE_1_REACHABLE=false
fi
echo ""

echo "=== OCP Version ==="
run_oc get clusterversion version -o jsonpath='{.status.desired.version}' || echo "WARNING: clusterversion query failed"
echo ""
echo ""

echo "=== Nodes ==="
run_oc get nodes -o wide || echo "WARNING: oc get nodes failed (API may be unreachable)"
echo ""

echo "=== Node Conditions ==="
run_oc get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,MEMORY_PRESSURE:.status.conditions[?(@.type=="MemoryPressure")].status,DISK_PRESSURE:.status.conditions[?(@.type=="DiskPressure")].status,PID_PRESSURE:.status.conditions[?(@.type=="PIDPressure")].status' || echo "WARNING: node conditions query failed"
echo ""

echo "=== Cluster Operators ==="
run_oc get co 2>&1 | awk 'NR==1 || $3 != "True" || $4 != "False" || $5 != "False"' || echo "WARNING: oc get co failed"
echo ""

echo "=== Pending CSRs ==="
run_oc get csr 2>&1 | awk 'NR==1 || /Pending/' || echo "WARNING: csr query failed"
echo ""

echo "=== Recent Events (non-Normal) ==="
run_oc get events -A --sort-by='.lastTimestamp' --field-selector type!=Normal 2>&1 | tail -30 || echo "WARNING: events query failed"
echo ""

echo "=== PCS Status ==="
run_on_node "$NODE_0" "sudo pcs status" || echo "WARNING: pcs status failed on ${NODE_0}"
echo ""

echo "=== Resource Failcounts ==="
run_on_node "$NODE_0" "sudo pcs resource failcount show" || echo "WARNING: pcs resource failcount show failed"
echo ""

echo "=== PCS Properties ==="
run_on_node "$NODE_0" "sudo pcs property" || echo "WARNING: pcs property failed"
echo ""

echo "=== CIB Standby State ==="
run_on_node "$NODE_0" "sudo pcs node attribute" || echo "WARNING: pcs node attribute failed"
echo ""

echo "=== CIB Recovery Attributes ==="
for node in "$NODE_0" "$NODE_1"; do
    echo "Node (${node}):"
    for attr in standalone_node learner_node force_new_cluster; do
        echo -n "  ${attr}: "
        run_on_node "$node" "sudo crm_attribute --query --name ${attr} 2>/dev/null" || echo "(not set)"
    done
done
echo ""

echo "=== Cluster ID Comparison ==="
CID_0=$(run_on_node "$NODE_0" "sudo crm_attribute --type nodes --query --name cluster_id 2>/dev/null" | sed -n 's/.*value=\(\S\+\).*/\1/p') || true
CID_1=$(run_on_node "$NODE_1" "sudo crm_attribute --type nodes --query --name cluster_id 2>/dev/null" | sed -n 's/.*value=\(\S\+\).*/\1/p') || true
echo "Node 0 (${NODE_0}): ${CID_0:-UNKNOWN}"
echo "Node 1 (${NODE_1}): ${CID_1:-UNKNOWN}"
if [ -n "$CID_0" ] && [ -n "$CID_1" ]; then
    if [ "$CID_0" = "$CID_1" ]; then
        echo "MATCH: Cluster IDs are identical (does not rule out split-brain — check standalone_node above)"
    else
        echo "MISMATCH: Split-brain detected — nodes are in different etcd clusters"
    fi
else
    echo "WARNING: Could not compare cluster IDs"
fi
echo ""

echo "=== Stonith History ==="
run_on_node "$NODE_0" "sudo pcs stonith history" || echo "WARNING: pcs stonith history failed"
echo ""

echo "=== STONITH Configuration ==="
run_on_node "$NODE_0" "sudo pcs stonith config" || echo "WARNING: pcs stonith config failed"
echo ""

echo "=== Pacemaker Constraints ==="
run_on_node "$NODE_0" "sudo pcs constraint" || echo "WARNING: pcs constraint failed"
echo ""

echo "=== Resource Location Bans ==="
run_on_node "$NODE_0" "sudo pcs constraint location show --full" || echo "WARNING: pcs constraint location show failed"
echo ""

echo "=== Corosync Status ==="
run_on_node "$NODE_0" "sudo pcs status corosync" || echo "WARNING: pcs status corosync failed"
echo ""

echo "=== Corosync Quorum ==="
run_on_node "$NODE_0" "sudo pcs quorum status" || echo "WARNING: pcs quorum status failed"
echo ""

echo "=== Cluster Network Connectivity ==="
echo "Node 0 → Node 1:"
run_on_node "$NODE_0" "sudo corosync-cfgtool -s" || echo "WARNING: corosync-cfgtool failed on ${NODE_0}"
run_on_node "$NODE_0" "ping -c 3 -W 2 ${NODE_1}" || echo "WARNING: ping to ${NODE_1} failed"
echo ""
echo "Node 1 → Node 0:"
run_on_node "$NODE_1" "sudo corosync-cfgtool -s" || echo "WARNING: corosync-cfgtool failed on ${NODE_1}"
run_on_node "$NODE_1" "ping -c 3 -W 2 ${NODE_0}" || echo "WARNING: ping to ${NODE_0} failed"
echo ""

echo "=== Management/BMC Network Connectivity ==="
if [ -n "${BMC_0:-}" ] && [ -n "${BMC_1:-}" ] && [ -n "${BMC_USER:-}" ] && [ -n "${BMC_PASS:-}" ]; then
    echo "Node 0 → BMC 1 (${BMC_1}):"
    run_on_node "$NODE_0" "host ${BMC_1}" || echo "WARNING: DNS resolution failed for ${BMC_1}"
    run_on_node "$NODE_0" "ping -c 3 -W 2 ${BMC_1}" || echo "WARNING: ping to ${BMC_1} failed"
    run_on_node "$NODE_0" "curl -sk --connect-timeout 10 --max-time 15 -o /dev/null -w 'HTTP_CODE: %{http_code} TIME_TOTAL: %{time_total}s TIME_CONNECT: %{time_connect}s' https://${BMC_1}:443/redfish/v1/Systems/1" || echo "WARNING: Redfish API unreachable on ${BMC_1}"
    run_on_node "$NODE_0" "sudo fence_redfish --ip=${BMC_1} --username=${BMC_USER} --password=${BMC_PASS} --ssl-insecure --systems-uri=/redfish/v1/Systems/1 --action=status" || echo "WARNING: fence_redfish status failed for ${BMC_1}"
    echo ""
    echo "Node 1 → BMC 0 (${BMC_0}):"
    run_on_node "$NODE_1" "host ${BMC_0}" || echo "WARNING: DNS resolution failed for ${BMC_0}"
    run_on_node "$NODE_1" "ping -c 3 -W 2 ${BMC_0}" || echo "WARNING: ping to ${BMC_0} failed"
    run_on_node "$NODE_1" "curl -sk --connect-timeout 10 --max-time 15 -o /dev/null -w 'HTTP_CODE: %{http_code} TIME_TOTAL: %{time_total}s TIME_CONNECT: %{time_connect}s' https://${BMC_0}:443/redfish/v1/Systems/1" || echo "WARNING: Redfish API unreachable on ${BMC_0}"
    run_on_node "$NODE_1" "sudo fence_redfish --ip=${BMC_0} --username=${BMC_USER} --password=${BMC_PASS} --ssl-insecure --systems-uri=/redfish/v1/Systems/1 --action=status" || echo "WARNING: fence_redfish status failed for ${BMC_0}"
else
    echo "(skipped — BMC_0/BMC_1/BMC_USER/BMC_PASS not set)"
fi
echo ""

echo "=== Application Network Connectivity ==="
echo "API server health:"
echo -n "  Node 0 local: "
run_on_node "$NODE_0" "curl -sk --connect-timeout 5 https://localhost:6443/healthz" || echo "FAILED"
echo ""
echo -n "  Node 0 → Node 1: "
run_on_node "$NODE_0" "curl -sk --connect-timeout 5 https://${NODE_1}:6443/healthz" || echo "FAILED"
echo ""
echo -n "  Node 1 local: "
run_on_node "$NODE_1" "curl -sk --connect-timeout 5 https://localhost:6443/healthz" || echo "FAILED"
echo ""
echo -n "  Node 1 → Node 0: "
run_on_node "$NODE_1" "curl -sk --connect-timeout 5 https://${NODE_0}:6443/healthz" || echo "FAILED"
echo ""

echo "=== etcd Container State ==="
echo "Node 0 (${NODE_0}):"
run_on_node "$NODE_0" "sudo podman ps -a --filter name=etcd --format '{{.Names}} {{.Status}}'" || echo "WARNING: podman ps failed on ${NODE_0}"
echo "Node 1 (${NODE_1}):"
run_on_node "$NODE_1" "sudo podman ps -a --filter name=etcd --format '{{.Names}} {{.Status}}'" || echo "WARNING: podman ps failed on ${NODE_1}"
echo ""

echo "=== etcd Member List ==="
run_on_node "$NODE_0" "sudo podman exec etcd etcdctl member list -w table" || echo "WARNING: etcdctl member list failed"
echo ""

echo "=== etcd Endpoint Health ==="
run_on_node "$NODE_0" "sudo podman exec etcd etcdctl endpoint health --cluster -w table" || echo "WARNING: etcdctl endpoint health failed"
echo ""

echo "=== etcd Status (DB size) ==="
run_on_node "$NODE_0" "sudo podman exec etcd etcdctl endpoint status --cluster -w table" || echo "WARNING: etcdctl endpoint status failed"
echo ""

echo "=== etcd Alarms ==="
run_on_node "$NODE_0" "sudo podman exec etcd etcdctl alarm list" || echo "WARNING: etcdctl alarm list failed"
echo ""

echo "=== Node Uptime ==="
echo -n "Node 0 (${NODE_0}): "
run_on_node "$NODE_0" "uptime" || echo "WARNING: uptime failed on ${NODE_0}"
echo -n "Node 1 (${NODE_1}): "
run_on_node "$NODE_1" "uptime" || echo "WARNING: uptime failed on ${NODE_1}"
echo ""

echo "=== Disk Space ==="
echo "Node 0 (${NODE_0}):"
run_on_node "$NODE_0" "df -h / /var/lib/etcd 2>/dev/null || df -h /" || echo "WARNING: df failed on ${NODE_0}"
echo "Node 1 (${NODE_1}):"
run_on_node "$NODE_1" "df -h / /var/lib/etcd 2>/dev/null || df -h /" || echo "WARNING: df failed on ${NODE_1}"
echo ""

echo "=== resource-agents RPM ==="
echo -n "Node 0 (${NODE_0}): "
run_on_node "$NODE_0" "rpm -qa | grep resource-agents" || echo "WARNING: rpm query failed on ${NODE_0}"
echo -n "Node 1 (${NODE_1}): "
run_on_node "$NODE_1" "rpm -qa | grep resource-agents" || echo "WARNING: rpm query failed on ${NODE_1}"
echo ""

echo "=== BMC Power State ==="
if [ -n "${BMC_0:-}" ] && [ -n "${BMC_1:-}" ] && [ -n "${BMC_USER:-}" ] && [ -n "${BMC_PASS:-}" ]; then
    echo -n "BMC 0 (${BMC_0}): "
    curl -sk -u "${BMC_USER}:${BMC_PASS}" "https://${BMC_0}/redfish/v1/Systems/1" 2>/dev/null \
        | grep -o '"PowerState":"[^"]*"' || echo "WARNING: Redfish query failed for ${BMC_0}"
    echo -n "BMC 1 (${BMC_1}): "
    curl -sk -u "${BMC_USER}:${BMC_PASS}" "https://${BMC_1}/redfish/v1/Systems/1" 2>/dev/null \
        | grep -o '"PowerState":"[^"]*"' || echo "WARNING: Redfish query failed for ${BMC_1}"
else
    echo "(skipped — BMC_0/BMC_1/BMC_USER/BMC_PASS not set)"
fi
echo ""

echo "=== Done ==="
