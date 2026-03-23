# Cluster Snapshot Skill

## Purpose

Generate a comprehensive snapshot of the current OpenShift cluster state, including version information, operator health, node status, and resource utilization.

**When to use this skill:**
- Quick cluster health assessment
- Documenting cluster state before/after changes
- Initial troubleshooting to understand cluster baseline
- Preparing information for support cases

## Trigger

Claude should invoke this skill when the user:
- Mentions "cluster snapshot", "cluster overview", or "cluster status"
- Asks for a health summary
- Needs a quick assessment of cluster state
- Wants to document current cluster configuration

## Prerequisites

- OpenShift cluster accessible (KUBECONFIG set)
- `oc` CLI tool installed
- Read permissions for cluster-scoped resources

## Implementation Steps

### 1. Validate Connectivity

Verify cluster access:
```bash
oc whoami
oc cluster-info
```

### 2. Gather Version Information

Collect cluster and operator versions:
```bash
# Cluster version
oc get clusterversion -o yaml

# Operator versions
oc get clusteroperators
```

### 3. Check Cluster Operators

Assess operator health:
```bash
# All operators with status
oc get co

# Degraded operators
oc get co --no-headers | grep -v "True.*False.*False"
```

### 4. Node Status

Collect node information:
```bash
# Node list with status
oc get nodes -o wide

# Node resource capacity
oc describe nodes | grep -A 5 "Capacity:"

# Unhealthy nodes
oc get nodes --no-headers | grep -v " Ready"
```

### 5. Resource Utilization

Get cluster-wide resource usage:
```bash
# Pod count by namespace
oc get pods --all-namespaces --no-headers | awk '{print $1}' | sort | uniq -c | sort -rn

# PV/PVC status
oc get pv
oc get pvc --all-namespaces
```

### 6. Critical Workload Status

Check key workloads:
```bash
# Pods not running
oc get pods --all-namespaces --field-selector=status.phase!=Running,status.phase!=Succeeded

# Recent events (errors/warnings)
oc get events --all-namespaces --field-selector type!=Normal --sort-by='.lastTimestamp' | tail -20
```

### 7. Generate Summary

Present organized snapshot:

```markdown
# Cluster Snapshot - <cluster-name>
Generated: <timestamp>

## Cluster Version
- OpenShift: <version>
- Kubernetes: <version>
- Update available: Yes/No

## Cluster Operators
- Total: <count>
- Available: <count>
- Degraded: <count>
- Progressing: <count>

### Degraded Operators
- operator-name: <reason>

## Nodes
- Total: <count>
- Ready: <count>
- NotReady: <count>

### Node Summary
<node list with status>

## Workloads
- Total Pods: <count>
- Running: <count>
- Pending: <count>
- Failed: <count>

### Unhealthy Pods
<list of pods not in Running/Succeeded state>

## Storage
- PersistentVolumes: <count>
- PersistentVolumeClaims: <count>
- Storage Classes: <list>

## Recent Issues
<Recent warning/error events>

## Recommendations
<Based on findings, suggest actions>
```

## Examples

### Example 1: Basic Snapshot

**User request:**
> "Generate a cluster snapshot"

**Claude response:**
```
I'll create a comprehensive cluster snapshot.

Checking cluster connectivity... ✓
Gathering cluster version... ✓
Checking operator health... ✓
Collecting node status... ✓
Analyzing workloads... ✓

# Cluster Snapshot - prod-cluster-01
Generated: 2026-03-23 14:30:00 UTC

## Cluster Version
- OpenShift: 4.15.3
- Kubernetes: 1.28.5
- Update channel: stable-4.15
- Update available: Yes (4.15.5)

## Cluster Operators
- Total: 35
- Available: 34
- Degraded: 1
- Progressing: 0

### Degraded Operators
- authentication: OAuth pod crashlooping (investigating)

## Nodes
- Total: 6
- Ready: 5
- NotReady: 1

### Node Summary
master-0: Ready (v1.28.5)
master-1: Ready (v1.28.5)
master-2: Ready (v1.28.5)
worker-0: Ready (v1.28.5)
worker-1: Ready (v1.28.5)
worker-2: NotReady (Kubelet stopped posting node status)

## Workloads
- Total Pods: 247
- Running: 241
- Pending: 3
- Failed: 3

### Unhealthy Pods
openshift-authentication/oauth-openshift-5d7c8f8b-xyz: CrashLoopBackOff
app-namespace/frontend-7f9b6c-abc: Pending (Insufficient CPU)

## Storage
- PersistentVolumes: 12
- PersistentVolumeClaims: 12
- Storage Classes: lvms-vg1, ocs-storagecluster-ceph-rbd

## Recent Issues
- 10 min ago: Node worker-2 became NotReady
- 25 min ago: OAuth pod restarting repeatedly
- 1 hour ago: Image pull backoff in app-namespace

## Recommendations
1. Investigate worker-2 node - check kubelet logs and system resources
2. Debug OAuth authentication operator - check pod logs
3. Address pending pod due to CPU constraints - consider scaling
4. Review image pull issues in app-namespace - verify registry access
```

### Example 2: Quick Health Check

**User request:**
> "Is my cluster healthy?"

**Claude response:**
```
Let me generate a cluster snapshot to assess health...

⚠️ Cluster Health: DEGRADED

Quick Summary:
- 1 operator degraded (authentication)
- 1 node NotReady (worker-2)
- 3 pods in unhealthy state

Critical Issues:
1. authentication operator degraded - impacts cluster login
2. worker-2 node NotReady - reduces capacity
3. OAuth pod crashlooping

Recommended immediate actions:
1. Check authentication operator: oc logs -n openshift-authentication deployment/oauth-openshift
2. Investigate worker-2: oc describe node worker-2
3. Review events: oc get events -n openshift-authentication --sort-by='.lastTimestamp'

Would you like me to dive deeper into any of these issues?
```

## Common Issues and Troubleshooting

### Issue: Cannot connect to cluster

**Solution:**
```bash
# Verify KUBECONFIG
echo $KUBECONFIG

# Test connectivity
oc whoami
oc get nodes
```

### Issue: Insufficient permissions

**Solution:**
Ensure your account has cluster-reader role or equivalent:
```bash
oc auth can-i get clusteroperators
oc auth can-i get nodes
```

### Issue: Slow command execution

**Solution:**
For large clusters, use label selectors or namespace filters to reduce scope:
```bash
# Specific namespace only
oc get pods -n specific-namespace

# Labeled resources
oc get pods -l app=myapp --all-namespaces
```

## Technical Details

### Commands Used

Key commands this skill executes:
- `oc get clusterversion` - Cluster version details
- `oc get clusteroperators` - Operator health
- `oc get nodes` - Node status
- `oc get pods --all-namespaces` - Workload status
- `oc get events` - Recent cluster events
- `oc get pv/pvc` - Storage overview

### Output Format

Snapshot is presented as structured markdown with:
- Section headers for easy navigation
- Counts and statistics
- Lists of degraded/unhealthy resources
- Actionable recommendations

### Performance Considerations

- Commands executed in parallel where possible
- Large clusters (>100 nodes) may take 30-60 seconds
- Consider using sub-agents for very large environments
- Cache results if multiple queries in same session

## Related Skills

Works well with:
- `cluster-health` - Deep health diagnostics
- `must-gather-analysis` - Full diagnostic collection
- `operator-debug` - Specific operator troubleshooting

## Notes for Claude

- Always start with connectivity check
- Present critical issues prominently
- Provide actionable next steps
- Offer to drill into specific issues
- For large clusters, ask if user wants filtered view
- Save snapshot to file if user requests documentation
