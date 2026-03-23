# Cluster Snapshot

Generate comprehensive OpenShift cluster snapshots for health assessment and documentation.

## Overview

The cluster-snapshot plugin provides a Claude Code skill that creates detailed snapshots of your OpenShift cluster state, including version information, operator health, node status, workload overview, and recent issues.

**Key features:**
- Comprehensive cluster health assessment
- Version and upgrade path information
- Operator status and degradations
- Node health and capacity
- Workload status across all namespaces
- Recent error events
- Actionable recommendations

## Installation

```bash
./marketplace install cluster-snapshot
```

## Usage

This skill is automatically triggered when you ask Claude to:
- Generate a cluster snapshot
- Show cluster overview
- Check cluster health
- Get cluster status summary

### Basic Example

```
User: "Generate a cluster snapshot"

Claude: I'll create a comprehensive cluster snapshot...

[Detailed snapshot with version, operators, nodes, workloads, and recommendations]
```

### Quick Health Check

```
User: "Is my cluster healthy?"

Claude: Let me check by generating a cluster snapshot...

✓ Cluster Health: HEALTHY

All operators available
All nodes ready
No critical issues detected
```

### Documentation

```
User: "Document the current cluster state before the upgrade"

Claude: I'll generate a detailed snapshot for your records...

[Comprehensive snapshot saved to cluster-snapshot-2026-03-23.md]
```

## Requirements

- **Claude Code:** >= 1.0.0
- **OpenShift:** 4.14, 4.15, 4.16
- **CLI Tools:** `oc`, `kubectl`
- **Permissions:** cluster-reader or equivalent (read access to cluster-scoped resources)

## What Gets Checked

### Cluster Information
- OpenShift version
- Kubernetes version
- Update channel and availability
- Cluster ID and infrastructure

### Operators
- Total operator count
- Available/Degraded/Progressing status
- Individual operator conditions
- Version information

### Nodes
- Node count and roles
- Ready/NotReady status
- Resource capacity (CPU, memory)
- Operating system and kernel
- Container runtime version

### Workloads
- Pod counts by status
- Unhealthy pods (Pending, Failed, CrashLoopBackOff)
- Resource requests and limits
- Top consumers by namespace

### Storage
- PersistentVolume status
- PersistentVolumeClaim usage
- Available storage classes
- Storage capacity and usage

### Events
- Recent warnings and errors
- Operator-related events
- Node events
- Pod scheduling issues

## Sample Output

```markdown
# Cluster Snapshot - prod-cluster
Generated: 2026-03-23 10:00:00 UTC

## Cluster Version
- OpenShift: 4.15.3
- Kubernetes: 1.28.5
- Update available: Yes (4.15.5)

## Cluster Operators
- Total: 35
- All Available: ✓
- None Degraded: ✓

## Nodes
- Total: 6 (3 masters, 3 workers)
- All Ready: ✓

## Workloads
- Total Pods: 247
- Running: 244
- Succeeded: 3
- No issues detected: ✓

## Storage
- PVs: 12 Bound
- Storage Classes: lvms-vg1, local-storage

## Status
✓ Cluster is healthy
```

## Troubleshooting

### Permission errors

Ensure you have cluster-reader permissions:
```bash
oc adm policy add-cluster-role-to-user cluster-reader $(oc whoami)
```

Or check specific permissions:
```bash
oc auth can-i get clusteroperators
oc auth can-i get nodes
oc auth can-i get pods --all-namespaces
```

### Slow execution on large clusters

For clusters with many resources, the snapshot may take longer. Consider:
- Running during off-peak hours
- Using filtered views for specific namespaces
- Leveraging sub-agents for parallel execution

### Missing information

If certain sections are incomplete:
- Verify API server connectivity
- Check for network policies blocking metrics
- Ensure monitoring stack is running

## Advanced Usage

### Save snapshot to file

```
User: "Generate a cluster snapshot and save it to a file"

Claude: I'll create the snapshot and save it...
[Saves to cluster-snapshot-<timestamp>.md]
```

### Compare with previous snapshot

```
User: "Generate a snapshot and compare with yesterday's"

Claude: I'll create a new snapshot and compare...
[Shows differences in operator status, node count, workload health]
```

### Focus on specific area

```
User: "Show me just the operator status"

Claude: I'll check cluster operators...
[Focused operator health report]
```

## Best Practices

1. **Regular snapshots** - Generate snapshots before major changes
2. **Document baselines** - Save healthy cluster snapshots as reference
3. **Pre/post comparison** - Snapshot before and after upgrades or config changes
4. **Troubleshooting** - Use as first step when investigating issues

## Contributing

Found a bug or want to enhance this plugin? See [../../docs/CONTRIBUTING.md](../../docs/CONTRIBUTING.md).

## License

Apache-2.0

## Author

jeff-roche

## Related Resources

- [OpenShift Documentation](https://docs.openshift.com/)
- [Cluster Health Monitoring](https://docs.openshift.com/container-platform/latest/support/troubleshooting/investigating-cluster-issues.html)
- [Must-Gather Analysis](https://docs.openshift.com/container-platform/latest/support/gathering-cluster-data.html)
