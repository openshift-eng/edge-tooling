# TNF Cluster Knowledge Base

Reference document for the `two-node:cluster-diagnostic` skill. All findings
are validated on HPE ProLiant e920t bare metal hardware across 7 test scenarios
(OCP 4.22.0-rc.3, Pacemaker managing etcd and kubelet via podman-etcd OCF agent).

Source: `TNF_GRACEFUL_SHUTDOWN_TEST.md`

## TNF Architecture Facts

### TNF is not standard OpenShift

In TNF, Pacemaker manages etcd and kubelet as cluster resources. Any operation
that stops Pacemaker resources also stops etcd, which kills the Kubernetes API.
Standard OpenShift shutdown procedures assume etcd is managed by the static pod
lifecycle, not Pacemaker. This fundamental difference invalidates the
documented procedure.

### Pacemaker standby kills the API immediately

`pcs node standby --all` stops all Pacemaker resources including etcd-clone and
kubelet-clone. With etcd down, the API server becomes unreachable within
~5 seconds. Once the API is down, you cannot cordon, drain, or use `oc debug`.
The only recovery path is SSH + `pcs node unstandby --all`.

### Pacemaker standby persists across reboots

The CIB (Cluster Information Base) stores standby state on disk. After a cold
boot, Pacemaker starts but refuses to start any resources because nodes are
still in standby. The cluster will NOT self-recover. Manual
`pcs node unstandby --all` is required.

### Standard OpenShift shutdown procedure triggers fencing

The documented procedure (cordon, drain, sequential `shutdown -h 1`) triggers a
Pacemaker fencing race in TNF. When the first node shuts down, the survivor
detects the departure and may fence the departing node before itself shuts down.
This caused a 12+ minute shutdown (vs ~3 minutes with the correct procedure)
due to unnecessary power cycling.

### Corosync clean departure prevents fencing

When a node shuts down gracefully (Redfish GracefulShutdown or OS-initiated
shutdown), the OS sends a Corosync leave message before the network goes down.
Pacemaker sees a clean departure, NOT a node failure. No STONITH fencing fires.
The surviving node cleanly removes the peer from etcd membership.

### Corosync timeout triggers fencing and force-new-cluster

When a node disappears without a Corosync leave message (network partition,
true hardware crash), Corosync detects the loss via token timeout (~10 seconds).
Pacemaker then fences the lost node. The survivor restarts etcd with
`--force-new-cluster` to create a single-member cluster. The departed node's
address is pre-added as an unstarted learner for later rejoin.

### HPE iLO ForceOff is non-deterministic

Redfish `ForceOff` on HPE ProLiant with iLO does NOT guarantee an instant power
loss. The BMC sends an ACPI signal, and the OS may have ~4 seconds to send
Corosync leave messages before power is cut. This means ForceOff sometimes
produces a clean Corosync departure (no fencing) and sometimes produces a
Corosync timeout (fencing fires). The behavior varies between attempts on the
same hardware.

### HPE iLO ForceOff can silently fail

The BMC can return HTTP 200 with "Success" but not actually power off the node.
This occurred once out of 3 ForceOff attempts during testing. Always verify
PowerState via Redfish after sending ForceOff.

### Double fence after network partition

After a network partition, the fenced node may be fenced twice. After the first
reboot, the node rejoins Pacemaker but can be fenced again ~90 seconds later
due to resource startup instability causing a brief Corosync membership drop.
This is operationally significant on bare metal (~3 minute boot per fence cycle)
but is not a functional failure.

### force-new-cluster vs clean member removal

The etcd recovery path depends on how the peer departed:

- **Clean Corosync departure** (graceful shutdown): etcd performs clean member
  removal via `etcdctl member remove`. No `force-new-cluster` needed. etcd API
  remains functional on the survivor.
- **Corosync timeout** (fencing path): etcd loses quorum. podman-etcd restarts
  etcd with `--force-new-cluster`, creating a single-member cluster. The fenced
  node's address is pre-added as an unstarted learner.

### VIPs do not preempt back after recovery

When the API/Ingress VIP holder fails, Keepalived migrates both VIPs to the
survivor. After the failed node recovers, VIPs stay on the survivor. This is
correct non-preemptive HA behavior. VIPs return to the original node only if
the survivor fails.

### Bare metal POST time dominates recovery

Of the ~18 minute total recovery time from a cold boot, ~14 minutes is hardware
POST + RHCOS boot. Pacemaker resource startup takes ~45 seconds. Operator
convergence adds ~7 minutes after nodes reach Ready. Warm boot (restart without
full power cycle) is ~5 minutes for POST.

## Severity Classification

| Finding | Severity | Action |
|---------|----------|--------|
| `pcs node standby` used on TNF cluster | **BLOCKER** | API is down. SSH to node, run `sudo pcs node unstandby --all`. Wait ~45 sec for resources. |
| Sequential shutdown planned or executed | **BLOCKER** | Triggers fencing race. Use simultaneous Redfish `GracefulShutdown` on both nodes. |
| Cordon/drain before TNF shutdown | **WARNING** | Unnecessary for TNF (all nodes are control-plane). Skip unless user workloads are present on TNF nodes. |
| `shutdown -h 1` via `oc debug` | **WARNING** | 1-minute delay during which fencing can fire. Use Redfish `GracefulShutdown` instead. |
| `stonith-enabled=false` as shutdown workaround | **WARNING** | Removes HA protection. Only acceptable as temporary pre-shutdown step if fencing prevention is critical. CEO/TNF controller re-enables on restart. |
| Redfish `ForceOff` used to simulate hardware crash | **INFO** | May not exercise fencing path on HPE iLO. For fencing validation, use iptables network partition instead. |
| Node fenced twice after partition recovery | **INFO** | Expected behavior. Second fence from boot instability. Cluster will converge after second reboot. |
| VIPs on "wrong" node after recovery | **INFO** | Normal non-preemptive Keepalived behavior. VIPs stay on survivor until it fails. Not a problem. |
| BMC unreachable from peer node | **BLOCKER** | Fencing will fail. Check DNS, network path, BMC power. |
| fence_redfish status check timeout | **WARNING** | BMC slow to respond. Verify STONITH timeout exceeds pcmk_delay_base + Redfish response time. |
| Corosync link disconnected to peer | **WARNING** | Cluster network down. Quorum lost if two-node. Check NIC, switch, IP config. |
| API server unreachable on peer node | **WARNING** | Application network partition. Workloads cannot communicate cross-node. |
| Stale STONITH failcount after recovery | **INFO** | Historical timeout, not active failure. Clear with `pcs resource cleanup <stonith-resource>`. |
| etcd NOSPACE alarm active | **BLOCKER** | etcd read-only. Compact, defrag, then disarm alarm. See edge case below. |
| Node in CIB standby state | **BLOCKER** | Resources won't start. Run `sudo pcs node unstandby --all`. |
| Pending CSRs blocking node Ready | **WARNING** | Approve: `oc adm certificate approve <csr-name>` for each pending CSR. |
| DiskPressure on node | **WARNING** | Check /var/lib/etcd and /var/log. Clear journals: `journalctl --vacuum-time=1d`. |
| etcd DB size > 6GB (75% of 8GB quota) | **WARNING** | Approaching quota. Defrag each member individually: `sudo podman exec etcd etcdctl defrag --endpoints=https://<node-ip>:2379`. |
| Pacemaker location constraint banning resource | **WARNING** | Resource cannot start on constrained node. Review: `pcs constraint show`. Remove with `pcs constraint remove <id>`. |
| WaitForAll blocking quorum formation | **INFO** | Normal after cold boot. Both nodes must be up for initial quorum. |
| etcd learner persists during Machine deletion | **BLOCKER** | Deadlock: CEO cannot clear preDrain hook. Requires resource-agents with PR #2156. |
| Node stuck in Starting, learner_node never set | **BLOCKER** | Start deadlock: monitor suppressed during start cycle. Requires resource-agents with PR #2157. |
| Cluster ID mismatch between nodes | **BLOCKER** | Split-brain. Use `force-new-cluster` recovery. See edge case below. |
| etcd start failure: stale data directory | **BLOCKER** | Member ID mismatch. Wipe `/var/lib/etcd/*` on failed node, then `pcs resource cleanup etcd`. |
| Stale `force_new_cluster` CIB attribute | **WARNING** | Should auto-clear on reboot. If stuck: `sudo crm_attribute -D -n force_new_cluster`. |
| Stale `learner_node` or `standalone_node` attribute | **WARNING** | Previous recovery incomplete. Clear: `sudo crm_attribute -D -n <attr>`. |
| Network partition with fencing failure | **BLOCKER** | Both sides quorate independently. Restore network + BMC, check for cluster ID divergence. |

## Correct Procedures

### Full Cluster Shutdown

```bash
# 1. Send GracefulShutdown to BOTH nodes simultaneously
curl -sk -u $BMC_USER:$BMC_PASS -X POST \
  https://$BMC_HOST_1/redfish/v1/Systems/1/Actions/ComputerSystem.Reset \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "GracefulShutdown"}'

curl -sk -u $BMC_USER:$BMC_PASS -X POST \
  https://$BMC_HOST_2/redfish/v1/Systems/1/Actions/ComputerSystem.Reset \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "GracefulShutdown"}'

# 2. Verify both nodes powered off (poll every 15s, expect ~3 min)
curl -sk -u $BMC_USER:$BMC_PASS \
  https://$BMC_HOST_1/redfish/v1/Systems/1 | jq .PowerState
# Expect: "Off"

# 3. To restart: send On to both nodes
curl -sk -u $BMC_USER:$BMC_PASS -X POST \
  https://$BMC_HOST_1/redfish/v1/Systems/1/Actions/ComputerSystem.Reset \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "On"}'

curl -sk -u $BMC_USER:$BMC_PASS -X POST \
  https://$BMC_HOST_2/redfish/v1/Systems/1/Actions/ComputerSystem.Reset \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "On"}'

# 4. Wait ~12-15 minutes for full recovery
```

**Do NOT**: cordon/drain, use `pcs node standby`, shut down sequentially, or
use `shutdown -h 1` via `oc debug`.

### Single Node Maintenance Shutdown

```bash
# 1. Send GracefulShutdown to the target node only
curl -sk -u $BMC_USER:$BMC_PASS -X POST \
  https://$TARGET_BMC/redfish/v1/Systems/1/Actions/ComputerSystem.Reset \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "GracefulShutdown"}'

# 2. Survivor continues operating (API available, etcd single-member)

# 3. To restart: send On
curl -sk -u $BMC_USER:$BMC_PASS -X POST \
  https://$TARGET_BMC/redfish/v1/Systems/1/Actions/ComputerSystem.Reset \
  -H "Content-Type: application/json" \
  -d '{"ResetType": "On"}'

# 4. Wait ~13 minutes for full recovery
```

### Recovery from Pacemaker Standby

If someone already ran `pcs node standby --all` and the API is unreachable:

```bash
# 1. SSH to a node (API is down, oc debug won't work)
ssh core@<node-ip>

# 2. Remove standby state
sudo pcs node unstandby --all

# 3. Wait ~45 seconds for Pacemaker to start resources
sudo pcs status  # verify resources Starting/Started

# 4. API becomes available within ~45 seconds
# Both nodes Ready within ~2 minutes
```

### After Any Recovery

```bash
# Verify fencing is active
sudo pcs property | grep stonith-enabled
# Must show: stonith-enabled: true

# Clear stale failed actions (cosmetic)
sudo pcs resource cleanup

# Check for pending CSRs
oc get csr | grep Pending

# Verify all operators healthy
oc get co | grep -v 'True.*False.*False'
# Empty output = all healthy
```

## Failure Modes

1. **Never recommend `pcs node standby`** on a TNF cluster. Standby stops all
   resources including etcd, which kills the API immediately. Standby state
   persists in the CIB across reboots.

2. **Never recommend sequential node shutdown** for TNF. Triggers a Pacemaker
   fencing race condition. Always shut down both nodes simultaneously via
   Redfish.

3. **Never recommend `shutdown -h 1` via `oc debug`**. The 1-minute delay
   creates a window during which Pacemaker fencing can fire.

4. **Never assume Redfish `ForceOff` simulates a hardware crash**. On HPE iLO,
   the OS may have ~4 seconds to send Corosync leave messages.

5. **Never assume `ForceOff` succeeded without verifying PowerState**. HPE iLO
   can return HTTP 200 but not power off the node.

6. **Never report VIPs on the "wrong" node as a problem**. Non-preemptive
   failover is correct HA behavior.

7. **Never skip verifying `stonith-enabled=true`** after recovery. Fencing must
   be active for HA protection.

## Recovery Timelines (HPE ProLiant e920t)

| Scenario | Total Time | Bottleneck |
|----------|-----------|------------|
| Full cluster cold boot | ~18 min | Hardware POST (~14 min) |
| Full cluster warm boot | ~12 min | Hardware POST (~5 min) |
| Single node rejoin | ~13 min | Boot + operator convergence |
| Network partition recovery | ~17 min | Double fence + boot |
| VIP holder failure recovery | ~18 min | Fence + boot + convergence |
| Pacemaker resource startup | ~45 sec | N/A |
| Operator convergence (after Ready) | ~7 min | API server rollouts |
| Standby recovery (no reboot) | ~3 min | Resource startup only |

## Test Run Cross-Reference

| Run | Scenario | Fencing? | Key Finding |
|-----|----------|----------|-------------|
| 1 | Pacemaker standby then shutdown | N/A | Standby kills API; persists across reboot |
| 2 | Documented procedure (cordon/drain/sequential) | Almost certainly | Sequential shutdown triggers fencing race |
| 3 | Simultaneous Redfish GracefulShutdown | No | Correct procedure. Clean recovery in ~12 min |
| 4 | Single-node GracefulShutdown | No | Corosync clean departure. No fencing. Auto-recovery |
| 5 | Network partition (iptables) | Yes (correct) | Double fence. force-new-cluster. ~17 min recovery |
| 6 | Single-node ForceOff | No | HPE iLO ForceOff gave OS ~4s for Corosync leave |
| 7 | VIP holder ForceOff | Yes | Non-deterministic ForceOff. VIP migration works. BMC can silently fail |

## Edge Cases

### etcd stuck as learner after rejoin

If `etcdctl member list` shows a node as learner for more than 5 minutes after
it rejoined Pacemaker:

1. Check `sudo pcs status` — is etcd-clone Started on both nodes?
2. Check network connectivity between nodes on port 2380.
3. Check etcd logs: `sudo podman logs etcd` for snapshot transfer or connection errors.

From Run 4/6: learner promotion normally completes within ~5 minutes.

### Stale failed actions blocking resource startup

`Failed Resource Actions` in `pcs status` after recovery are typically
historical records, not active failures. If resources show Started, the entries
are cosmetic. Fix with `sudo pcs resource cleanup` after confirming resources
are healthy.

### BMC/management network unavailable

If Redfish is unavailable, fallback: SSH to both nodes and run
`sudo shutdown -h now` simultaneously (two terminals). If only one SSH session
is possible: disable fencing first (`sudo pcs property set stonith-enabled=false`),
then shutdown sequentially. **Not tested.**

### Datacenter power outage

Both nodes lose power simultaneously. On restore, recovery depends on boot
timing. If both boot together, Corosync forms fresh membership. If one boots
first, it may run `force-new-cluster`, then the second joins as learner.
**Key risk**: if CIB had standby state stored before outage, manual
`pcs node unstandby --all` is required.

### Rolling restart (one node at a time)

Validated on bare metal 2026-05-31 with a 2-replica test workload deployed
across both nodes. Total time: ~35-40 minutes.

**Procedure:**

1. Verify all operators Available=True before starting
2. GracefulShutdown node A via Redfish
3. Wait for node A NotReady (~5 min) and workload pods to reschedule (~6 min)
4. Wait for node A to restart and rejoin (~13 min total)
5. Verify all operators Available=True
6. GracefulShutdown node B via Redfish
7. Repeat steps 3-5 for node B

**Findings from bare metal validation:**

- **Zero fencing** across both legs — GracefulShutdown produces clean Corosync
  departure every time
- **Workload survived both transitions** — Kubernetes rescheduled pods within
  ~6 minutes of each node going NotReady. At no point were zero replicas
  running.
- **Pods do not rebalance** after the restarted node comes back. After the
  rolling restart, all workload pods end up on the last-surviving node. This
  is correct Kubernetes behavior (no preemption) but means workload
  distribution is uneven. Use `oc delete pod` to trigger rescheduling if
  needed.
- **VIPs do not migrate back** — same non-preemptive Keepalived behavior as
  all other recovery scenarios.

This is the same sequence that MCO performs during OCP upgrades.

### Boot order after full shutdown

Boot order does not matter functionally. The first node starts as single-member
cluster; the second joins as learner and is promoted. Simultaneous boot (Run 3)
is the tested and recommended approach.

### Maximum single-node operation duration

Tested: 1 hour 44 minutes with no issues. For planned maintenance, keep under
24 hours to avoid certificate rotation and etcd compaction edge cases.

### OCP upgrade and node reboots

MCO-initiated reboots should produce Corosync clean departure (no fencing).
Monitor `pcs stonith history` during upgrades to verify. If fencing fires, the
cluster will still recover but extends upgrade time.

### Single-node standby (not both)

`pcs node standby <one-node>` — **not directly tested**. Pacemaker stops
resources on standby node; etcd behavior depends on whether podman-etcd handles
single-member transition. Treat as risky. Recover with
`sudo pcs node unstandby <node-name>`.

### etcd NOSPACE alarm

If `etcdctl alarm list` shows NOSPACE, etcd is read-only and the cluster
cannot make progress. etcd still accepts reads and deletes but rejects
all writes.

Validated on bare metal 2026-05-31 (OCP 4.22.0-rc.5).

```bash
# 1. Delete unnecessary data if applicable (etcd accepts deletes while read-only)
sudo podman exec etcd etcdctl del /prefix/ --prefix

# 2. Get current revision
REV=$(sudo podman exec etcd etcdctl endpoint status -w json | \
  grep -o '"revision":[0-9]*' | head -1 | cut -d: -f2)
echo "Compacting to revision: $REV"

# 3. Compact to current revision
sudo podman exec etcd etcdctl compact "$REV"

# 4. Defrag each member separately (--cluster can timeout on the leader
#    when the DB is large; defrag followers first, then the leader)
sudo podman exec etcd etcdctl defrag --endpoints=https://<follower-ip>:2379
sudo podman exec etcd etcdctl defrag --endpoints=https://<leader-ip>:2379

# 5. Disarm the alarm
sudo podman exec etcd etcdctl alarm disarm

# 6. Verify DB size dropped and alarm cleared
sudo podman exec etcd etcdctl alarm list
# Expected: empty (no alarms)
sudo podman exec etcd etcdctl endpoint status --cluster -w table
```

**Findings from bare metal validation:**

- The `jq` command in the original procedure does not work inside the
  etcd container. Use `grep` to parse the JSON instead.
- `etcdctl defrag --cluster` can timeout on the leader when the DB is
  at quota (~8.6 GB). Defrag each member individually instead.
- Compaction alone does not free space — data must be deleted or
  superseded first. Then compact reclaims old revisions, and defrag
  returns the space to the filesystem.
- The quota is 8.6 GB (not 8 GB). Monitor DB size with
  `etcdctl endpoint status` — treat >6 GB as a warning.

### Pending CSRs after recovery

After fencing or extended single-node operation (>24 hours), the
recovering node's kubelet certificate may expire. The node appears
NotReady until CSRs are approved:

```bash
# 1. Check for pending CSRs
oc get csr | grep Pending

# 2. Approve each pending CSR
oc adm certificate approve <csr-name>

# 3. Node transitions to Ready within ~30 seconds
```

Multiple CSRs may be pending (client and server certs). Approve all of
them. This is most common after extended maintenance windows.

### Split-brain detection

After ungraceful disruptions (power outage, simultaneous fencing with failure),
both nodes may start independent etcd clusters via `--force-new-cluster`.

**Detection (primary — standalone_node attribute):**

```bash
# Check on BOTH nodes — if both report standalone_node for themselves,
# split-brain is confirmed
sudo crm_attribute --query --name standalone_node
```

The diagnostic script checks this in the `=== CIB Recovery Attributes ===`
section. Both nodes claiming `standalone_node` is the reliable signal.

**Detection (supplementary — cluster ID comparison):**

```bash
sudo crm_attribute --type nodes --query --name cluster_id
```

If cluster IDs differ, split-brain is confirmed (snapshot-restore divergence).
If they match, split-brain is **not ruled out** — `--force-new-cluster`
preserves the existing cluster ID.

**Recovery (automated — recommended):**

If two-node-toolbox is available, use the force-new-cluster playbook:

```bash
ansible-playbook helpers/force-new-cluster.yml \
  -i deploy/openshift-clusters/inventory.ini
```

This playbook takes snapshots, clears conflicting CIB attributes, detects the
etcd leader, removes the follower from the member list, and cleans up
automatically.

**Recovery (manual):**

```bash
# 1. Identify the node with more recent data (higher revision)
sudo podman exec etcd etcdctl endpoint status -w table
# Run on both nodes, compare RAFT INDEX

# 2. On the node with LESS data, wipe etcd
sudo rm -rf /var/lib/etcd/*

# 3. Clear conflicting CIB attributes on both nodes
sudo crm_attribute -D -n force_new_cluster
sudo crm_attribute -D -n learner_node
sudo crm_attribute -D -n standalone_node

# 4. Cleanup Pacemaker on both nodes
sudo pcs resource cleanup etcd
```

Source: two-node-toolbox `QUICK_REFERENCE.md`

### Etcd start failure: stale data directory

After a node is fenced or removed from the cluster, its `/var/lib/etcd`
directory may contain stale data with a member ID that no longer matches the
current cluster. etcd refuses to start with "No such device or address" errors.

**Symptoms:**

- `pcs status` shows: `etcd start on <node> returned 'error'`
- Pacemaker logs: `crm_attribute: Error performing operation: No such device or address`
- `etcdctl member list` shows the node as "unstarted" with `IS_LEARNER: true`

**Recovery (simple case — one node healthy, no split-brain):**

Validated on bare metal 2026-05-28. Wipe + cleanup is sufficient.

```bash
# 1. On the failed node, wipe stale etcd data
sudo rm -rf /var/lib/etcd/*

# 2. Cleanup Pacemaker resource state
sudo pcs resource cleanup etcd

# 3. Monitor recovery — node should rejoin as learner, then promote
sudo pcs status
sudo podman exec etcd etcdctl member list -w table
```

Expected: node joins as learner and is promoted to voter within ~5 minutes.

**Recovery (after split-brain — both nodes ran force-new-cluster):**

Validated on bare metal 2026-05-28. Requires CIB attribute clearing and a
disable/enable cycle. Simple cleanup alone is not sufficient — etcd container
stays down until a full stop/start is forced.

```bash
# 1. Restore network connectivity between nodes

# 2. Pick the winner (node with more recent data / higher raft index)
sudo podman exec etcd etcdctl endpoint status -w table
# Run on both nodes, compare RAFT INDEX

# 3. On the loser node, wipe stale etcd data
sudo rm -rf /var/lib/etcd/*

# 4. On the winner node, clear CIB recovery attributes
sudo crm_attribute -D -n standalone_node
sudo crm_attribute -D -n learner_node

# 5. Force a full restart of etcd — cleanup alone is not enough
sudo pcs resource cleanup etcd
sudo pcs resource disable etcd-clone
sleep 5
sudo pcs resource enable etcd-clone

# 6. Re-enable fencing if it was disabled
sudo pcs property set stonith-enabled=true

# 7. Monitor recovery
sudo pcs status
sudo podman exec etcd etcdctl member list -w table
```

Expected: etcd restarts within ~1 minute, loser rejoins as learner and is
promoted to voter within ~5 minutes.

### Network partition with fencing failure

In a two-node cluster with the `2Node` quorum flag, each node retains quorum
independently when the peer disappears. A network partition does NOT cause
quorum loss — both sides continue operating as quorate. With fencing enabled,
the partition is resolved automatically (one node fences the other, Stage 1
validated 2026-05-28). The dangerous scenario is when fencing also fails.

**How it happens in production:**

A switch failure, VLAN misconfiguration, or routing change blocks all traffic
between nodes. Corosync partitions. Both nodes try to fence the other, but the
BMC/management network goes through the same failed infrastructure — fencing
fails on both sides. Both nodes are now independently quorate, running all
resources, and etcd loses replication.

**What was validated on bare metal (2026-05-28):**

Blocking only corosync (UDP 5405) while leaving etcd (TCP 2380) open: both
nodes retained quorum, etcd stayed healthy and connected, cluster IDs remained
identical. No split-brain occurred because etcd's network path was intact.
Diagnostic correctly reported cluster IDs as MATCH — it was not fooled by the
Pacemaker-level partition.

Blocking all traffic (Stage 1): fencing fired and resolved the partition
automatically. The survivor ran `force-new-cluster`, the fenced node rebooted
and rejoined as a learner.

**Symptoms (full partition + fencing failure):**

- Both nodes show "partition with quorum" (NOT "without quorum")
- Both nodes show the peer as OFFLINE
- `pcs stonith history` shows failed fence attempts
- etcd may show "must force a new cluster" on both nodes
- Cluster IDs may diverge if both nodes run `force-new-cluster` independently

**Recovery:**

```bash
# 1. Restore network connectivity (fix switch, VLAN, routing)

# 2. Restore BMC connectivity (verify fencing path)
sudo fence_redfish --ip=<peer-bmc> --username=<user> --password=<pass> \
  --ssl-insecure --systems-uri=/redfish/v1/Systems/1 --action=status

# 3. If cluster IDs diverged (check both nodes):
sudo crm_attribute --type nodes --query --name cluster_id
# If different → see "Split-brain: cluster ID mismatch" edge case above

# 4. If cluster IDs match, cleanup and verify:
sudo pcs resource cleanup
sudo pcs property set stonith-enabled=true
sudo pcs status
```

**Key insight from testing:** True quorum loss ("partition WITHOUT quorum")
cannot occur in two-node mode once quorum is initially established. The
`2Node` flag allows each node to retain quorum independently. The real danger
is not quorum loss — it is both sides operating independently with fencing
unable to resolve the conflict.

## Network Connectivity Verification

A TNF cluster depends on three distinct network paths. All three must be
healthy for the cluster to detect failures, fence the failed node, and
continue serving workloads.

### The three network paths

| Path | What it carries | What breaks if it's down |
|------|----------------|--------------------------|
| Cluster network (Corosync) | Heartbeats, quorum votes, membership | Nodes lose quorum, split-brain risk |
| Management/BMC network | Fence agent → peer BMC (Redfish HTTPS) | Fencing fails — surviving node cannot power-cycle the dead one, resources stay blocked |
| Application network | API server, etcd replication, pod traffic | Workloads cannot communicate, API unreachable cross-node |

### Verification approach

Start with the fence agent end-to-end check. Only decompose into layers when
it fails:

1. `fence_redfish --action=status` — does fencing work end-to-end?
2. If it fails, decompose:
   - `host <bmc-hostname>` — Layer 1: DNS resolution
   - `ping -c 3 <bmc-hostname>` — Layer 2: Network reachability
   - `curl -sk https://<bmc>:443/redfish/v1/Systems/1` — Layer 3: Redfish API
   - `curl -sk --user <user>:<pass> https://...` — Layer 4: Authentication

### Directionality matters

BMC connectivity must be tested **from the node that would perform the
fencing** (the surviving node) **to the peer's BMC**. Testing from a
workstation proves the BMC is alive but not that the fencing node can reach it.

### Timing budget

The `pcmk_delay_base` setting eats into the STONITH timeout. For example, with
`pcmk_delay_base=10s` and the default 20s STONITH timeout, only ~10 seconds
remain for the actual Redfish call. If the BMC responds in 250ms this is fine,
but a slow or overloaded BMC can exceed this budget and cause timeout failures.

Check: `stonith-timeout` (cluster property) minus `pcmk_delay_base` (per
STONITH resource) must exceed the worst-case Redfish response time.

### Common failure patterns

- **Stale failcount after transient BMC timeout**: The BMC was temporarily
  slow (e.g., during node reboot). Fencing timed out, failcount incremented.
  BMC is now responsive but Pacemaker won't retry until failcount is cleared.
  Fix: `pcs resource cleanup <stonith-resource>`.

- **DNS resolution failure to BMC hostname**: Management hostnames resolve via
  a different DNS path than cluster hostnames. If management DNS is down, the
  fence agent cannot resolve the BMC address even though the BMC is up.

- **Management network unreachable, cluster network fine**: The cluster network
  and management network are often on different VLANs/subnets. A switch or
  routing issue can take down BMC access while Corosync continues operating.
  The cluster appears healthy until a node actually fails and fencing is needed.

### Connectivity verification commands

#### Cluster network

```bash
# Corosync link status (shows connected/disconnected per peer)
corosync-cfgtool -s

# Ping peer node on cluster network
ping -c 3 <peer-node-ip>
```

#### Management/BMC network

```bash
# DNS resolution of peer BMC
host <peer-bmc-hostname>

# Ping peer BMC
ping -c 3 <peer-bmc-hostname>

# Redfish API reachability (HTTP 401 = API alive, no auth sent)
curl -sk --connect-timeout 10 --max-time 15 \
  -o /dev/null -w "HTTP_CODE: %{http_code}\nTIME_TOTAL: %{time_total}s\nTIME_CONNECT: %{time_connect}s\n" \
  https://<peer-bmc-hostname>:443/redfish/v1/Systems/1

# Fence agent end-to-end status check (read-only, does not fence)
fence_redfish \
  --ip=<peer-bmc-hostname> \
  --username=<bmc-username> \
  --password=<bmc-password> \
  --ssl-insecure \
  --systems-uri=/redfish/v1/Systems/1 \
  --action=status
```

#### Application network

```bash
# API server health (local and cross-node)
curl -sk https://localhost:6443/healthz
curl -sk https://<peer-node-ip>:6443/healthz

# etcd cluster health
sudo podman exec etcd etcdctl member list --write-out=table
sudo podman exec etcd etcdctl endpoint health --cluster
```

## Known Fixes (resource-agents PRs)

Track these when correlating resource-agents RPM version to observed behavior.

### PR #2156: etcd learner deadlock during Machine deletion (OCPBUGS-82190)

**Merged:** 2026-05-18 | **File:** `heartbeat/podman-etcd`

**Problem:** When a Machine is being deleted in TNF, the cluster-etcd-operator
(CEO) keeps the EtcdQuorumOperator preDrain hook active as long as the peer IP
appears in the etcd member list — including learners. podman-etcd was
unconditionally adding/keeping peers as learners, which prevented CEO from
clearing the preDrain hook, causing a deadlock: Machine stuck in Deleting
indefinitely.

**Fix:** Added `is_peer_machine_deleting()` function that queries the Machine
API (MAPI and CAPI) to check whether a peer node's Machine has a
`deletionTimestamp` set. Guards learner addition in `manage_peer_membership()`
and `podman_start()` — skips adding the peer as a learner if its Machine is
being deleted. Also removes stale learners that were added before the Machine
deletion started.

**Diagnostic relevance:** If `etcdctl member list` shows a learner that
persists indefinitely while a Machine is in Deleting state, this is the
deadlock that PR #2156 fixes. Check resource-agents RPM version — the fix
must be present for Machine deletion to complete.

**Symptoms:**

- Machine stuck in `Deleting` phase
- CEO logs: `skip removing the deletion hook from machine ... since its member is still present`
- `etcdctl member list` shows the deleted node as a persistent learner
- Requires resource-agents build containing this fix

### PR #2157: learner addition deadlock during node start (OCPBUGS-83333)

**Merged:** 2026-05-18 | **File:** `heartbeat/podman-etcd`

**Problem:** When a node rejoins a TNF cluster, `podman_start` on the joining
node polls for the `learner_node` CIB attribute to confirm it was added as an
etcd learner. The only code path that sets this attribute is `check_peer`,
which runs during `podman_monitor` on the running peer. But Pacemaker
suppresses the peer's monitor during an active start/notify cycle. Result:
the joiner waits for `learner_node` that can never be set — indefinite
deadlock.

**Fix:** Added a `check_peer` call in `podman_notify()` on
`pre_notify_start`. This fires before the start action begins, so the
running peer adds the joining node as a learner before `podman_start` starts
polling. Breaks the deadlock.

**Diagnostic relevance:** If a node is stuck during rejoin with
`podman_start` timing out waiting for `learner_node` attribute, and the
peer's monitor is not running, this is the deadlock PR #2157 fixes. Check
resource-agents RPM version.

**Symptoms:**

- Joining node stuck in Starting state, never reaches Started
- Pacemaker logs: `podman_start` polling for `learner_node` attribute
- Peer node's `podman_monitor` not running (suppressed during start cycle)
- etcd member list does NOT show the joining node as a learner
- Requires resource-agents build containing this fix

## Cluster Under Test

- **Hardware**: HPE ProLiant e920t (bare metal, 2 nodes)
- **OCP**: 4.22.0-rc.3 (Kubernetes v1.35.4)
- **BMC**: HPE iLO 5, Redfish API
- **Fencing agent**: fence_redfish
- **Pacemaker resources**: kubelet-clone, etcd-clone (via podman-etcd OCF agent), 2x STONITH (fence_redfish)
- **Test date**: 2026-05-15 to 2026-05-17
