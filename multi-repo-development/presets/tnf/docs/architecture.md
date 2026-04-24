# TNF Architecture

## Overview

```
                         INSTALLATION PATHS
    +------------------+  +------------------+  +------------------+
    | Assisted Inst.   |  | Agent-Based Inst.|  |   IPI Installer  |
    |   (MCE/ACM)      |  | (openshift-inst) |  |                  |
    +--------+---------+  +--------+---------+  +--------+---------+
             |                     |                     |
             +---------------------+---------------------+
                                   |
                                   v
                    +---------------------------------------------+
                    |     machine-config-operator (MCO)           |
                    |   - Installs HA packages (pacemaker, pcs)   |
                    |   - Creates directories, enables PCSD       |
                    |   - Prepares nodes BEFORE CEO runs          |
                    +----------------------+----------------------+
                                           |
                    +----------------------v----------------------+
                    |       cluster-etcd-operator (CEO)           |
                    |   - Manages etcd during bootstrap           |
                    |   - TNF controller initializes RHEL-HA      |
                    |   - Hands over etcd to Pacemaker            |
                    +----------------------+----------------------+
                                           |
          +--------------------------------+--------------------------------+
          |                                |                                |
+---------v---------+          +-----------v-----------+          +---------v---------+
|     Corosync      |          |      Pacemaker        |          |    podman-etcd    |
| (cluster member-  |<-------->|  (fault tolerance     |<-------->|   (OCF agent      |
|  ship & quorum)   |          |   & failover)         |          |    for etcd)      |
+-------------------+          +-----------+-----------+          +-------------------+
                                           |
                               +-----------v-----------+
                               |    fence-agents        |
                               |  (fence_redfish,       |
                               |   fence_virsh, ...)    |
                               +-----------+-----------+
                                           |
                               +-----------v-----------+
                               |     BMC / Hypervisor  |
                               |   (Redfish, libvirt)  |
                               +-----------------------+
```

## Key Concepts

- **C-quorum**: Quorum as determined by Corosync membership
- **E-quorum**: Quorum as determined by etcd membership
- **Fencing**: Powering off unresponsive nodes via BMC to prevent split-brain. Pacemaker's `fenced` daemon invokes a *fence agent* (e.g., `fence_redfish`) that talks to the BMC API
- **force-new-cluster**: etcd flag to restart as cluster-of-one after peer failure
- **Learner node**: etcd node waiting to become a full voting member
- **STONITH**: "Shoot The Other Node In The Head" — fencing mechanism

## Failure Scenarios

1. **Network failure**: Pacemaker fences one node, survivor restarts etcd as cluster-of-one
2. **Node failure**: Survivor fences peer, continues operating
3. **etcd failure**: OCF agent detects and restarts etcd
4. **Kubelet failure**: Pacemaker manages kubelet restart

## Version Requirements

- **OCP minimum version for TNF**: 4.20
- **BMC protocol**: Redfish required (only supported BMC protocol for TNF fencing)
- **Fencing credentials required**: BMC address, username, password for both nodes
- **Platform support**: `baremetal` or `none` only

## Repository Roles

| Component | Repository | Role |
|-----------|-----------|------|
| Enhancement spec | `enhancements` | Authoritative design document |
| API definitions | `api` | DualReplica FeatureGate, PacemakerCluster CRD |
| Installation (MCE) | `assisted-service` | TNF validation, fencing credentials |
| Installation (ABI) | `installer` | Standalone agent-based install |
| OS preparation | `machine-config-operator` | HA packages, systemd units, dirs |
| etcd management | `cluster-etcd-operator` | TNF controller (auth, setup, fencing, handover) |
| etcd agent | `resource-agents` | podman-etcd OCF agent |
| BMO awareness | `cluster-baremetal-operator` | Avoid power conflicts with Pacemaker |
| Deployment | `two-node-toolbox` | AWS/external host cluster lifecycle |
| Dev clusters | `dev-scripts` | libvirt VMs with virtualbmc |
| E2E tests | `origin` | TNF topology, recovery, degraded tests |
| CI/CD | `release` | Prow jobs, step registry workflows |
| User docs | `openshift-docs` | Installation and operation guides |
| Fence agents | `fence-agents` | STONITH scripts (fence_redfish, fence_virsh) |
| HA reference | `pacemaker` | Upstream Pacemaker (troubleshooting only) |
