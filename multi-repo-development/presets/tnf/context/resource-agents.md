<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# resource-agents — TNF Context

**Category**: Development

**Purpose**: OCF-compliant resource agents for Pacemaker and rgmanager

**TNF Relevance**: Contains the `podman-etcd` resource agent that Pacemaker uses to control etcd after CEO hands it over:
- Creates and manages etcd containers via Podman
- Handles etcd cluster membership (adding/removing members)
- Manages learner nodes and standalone scenarios
- Implements force-new-cluster for recovery after fencing
- Monitors certificate changes and restarts etcd accordingly
- Prevents split-brain via careful peer detection

**Key files**:
- `heartbeat/podman-etcd` - The main OCF resource agent script (~75KB, ~2000 lines)
- `heartbeat/podman` - Base podman resource agent

**Key functions in podman-etcd**:
- `podman_start()` - Container startup with cluster state detection
- `leave_etcd_member_list()` - Safe node removal from etcd cluster
- `reconcile_member_state()` - Promotes learners, reconciles cluster
- `container_health_check()` - Advanced health monitoring

**Testing**: **IMPORTANT** - No unit tests exist for podman-etcd. Testing must be done on a live TNF cluster:
- Use `make patch-nodes` in `two-node-toolbox` (from `deploy/` directory)
- This builds the modified resource-agents RPM and deploys it to cluster nodes
- See `two-node-toolbox/helpers/build-and-patch-resource-agents.yml` for the patching playbook

**Build**:
```bash
./autogen.sh
./configure
make
```
