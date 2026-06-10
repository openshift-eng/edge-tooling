# PR Draft: Fix cluster ID documentation for force-new-cluster recovery

**Repo:** openshift-eng/two-node-toolbox
**Branch:** fix/cluster-id-docs
**Target:** main

---

## PR Title

Fix incorrect cluster ID behavior documentation for force-new-cluster recovery

## PR Body

```markdown
## Summary

- Fix documentation that claims `--force-new-cluster` assigns a new cluster ID — it preserves the existing one
- Update split-brain detection guidance from cluster ID comparison (ineffective) to CIB `standalone_node` attribute check (reliable)
- No playbook changes needed — `force-new-cluster.yml` does not use cluster ID for decisions

## Problem

The TROUBLESHOOTING_SKILL.md states under "Ungraceful Disruption (4.20+)":

> Surviving node restarts etcd as cluster-of-one. **New cluster ID is assigned.**

This is incorrect. `etcd --force-new-cluster` preserves the cluster ID from the
existing data directory. It rewrites cluster membership but does not generate a
new cluster ID.

The QUICK_REFERENCE.md recommends detecting split-brain by comparing `cluster_id`
CIB attributes between nodes. This check is ineffective because both nodes will
show the same cluster ID even when running as independent single-member clusters
after force-new-cluster.

## Evidence

Validated on HPE ProLiant e920t bare metal (2026-05-28) in openshift-eng/edge-tooling:

1. **Three force-new-cluster operations** — cluster ID (`16699325708140828887`)
   unchanged every time
2. **Deliberate split-brain test** — both nodes ran force-new-cluster
   independently with fencing disabled. Cluster IDs remained identical on both
   sides despite running as independent single-member clusters
3. **podman-etcd source** — `attribute_node_cluster_id()` reads cluster ID from
   etcd via `revision.json`, never generates one
4. **etcd upstream issues** — etcd-io/etcd#2328 documents that
   `--force-new-cluster` retains stale peer membership metadata;
   etcd-io/etcd#8169 reports cluster ID mismatches when peer URLs change
   during recovery. Neither issue explicitly guarantees cluster ID
   preservation, but our tests (above) confirm the ID is unchanged.
   `snapshot restore` generates a new cluster ID

The reliable split-brain signal is both nodes reporting `standalone_node` for
themselves in CIB attributes.

## Changes

### .claude/commands/etcd/TROUBLESHOOTING_SKILL.md

**Failure Scenarios section**, "Ungraceful Disruption (4.20+)":

Before:

```text
- Surviving node restarts etcd as cluster-of-one
- New cluster ID is assigned
- Failed node discards old DB and resyncs on restart
```

After:

```text
- Surviving node restarts etcd with --force-new-cluster
- Cluster ID is preserved (force-new-cluster reuses existing data, does not
  generate a new ID)
- Failed node discards old DB and resyncs on restart
```

**CIB Attributes Analysis section**, update the "Common Issues" for cluster_id:

Before:

```text
- **Different cluster IDs**: Nodes are in different etcd clusters - need force-new-cluster
```

After:

```text
- **Different cluster IDs**: Nodes bootstrapped from different data (e.g.,
  snapshot restore). Note: force-new-cluster preserves cluster IDs, so matching
  IDs do NOT rule out split-brain. Check standalone_node attribute instead.
```

**Analysis Questions**, add:

```text
6. Do both nodes report standalone_node for themselves? (If yes → split-brain,
   even if cluster_id matches)
```

### .claude/commands/etcd/QUICK_REFERENCE.md

**Split-brain diagnosis section**:

Before:

```bash
# Check cluster IDs on both nodes
ansible cluster_vms -i deploy/openshift-clusters/inventory.ini -m shell -a \
  "sudo crm_attribute -G -n cluster_id" -b
```

After:

```bash
# Check for split-brain via standalone_node attribute (reliable)
# If BOTH nodes report standalone_node for themselves → split-brain confirmed
ansible cluster_vms -i deploy/openshift-clusters/inventory.ini -m shell -a \
  "sudo crm_attribute --query --name standalone_node" -b

# Cluster ID comparison (supplementary — catches snapshot-restore divergence
# but NOT force-new-cluster splits, since force-new-cluster preserves the ID)
ansible cluster_vms -i deploy/openshift-clusters/inventory.ini -m shell -a \
  "sudo crm_attribute --type nodes --query --name cluster_id" -b
```

**Split-brain symptoms**:

Before:

```text
- Both nodes have etcd running but with different cluster IDs
- CIB attributes show different cluster_id values
```

After:

```text
- Both nodes have etcd running as independent single-member clusters
- Both nodes report standalone_node for themselves in CIB
- Note: cluster_id may still MATCH — force-new-cluster preserves it
```

## What is NOT changed

The `helpers/force-new-cluster.yml` playbook is correct and does not use
cluster ID for any decisions. It detects the leader via `etcdctl endpoint
status` (member_id == leader_id), which is the right approach. No changes
needed.

## Test plan

- [ ] Verify TROUBLESHOOTING_SKILL.md changes are accurate
- [ ] Verify QUICK_REFERENCE.md detection commands work on a TNF cluster
- [ ] Confirm force-new-cluster.yml playbook is unaffected
- [ ] Review with etcd/Pacemaker team for correctness

## References

- etcd-io/etcd#2328 — force-new-cluster retains old cluster info
- etcd-io/etcd#8169 — cluster ID mismatch after force-new-cluster (because ID is preserved)
- etcd-io/etcd#1242 — original design: force-new-cluster rewrites membership, not IDs
- openshift-eng/edge-tooling bare metal validation (2026-05-28)

```text

---

## File-by-file diff summary

| File | Change |
|------|--------|
| `.claude/commands/etcd/TROUBLESHOOTING_SKILL.md` | Fix "New cluster ID is assigned" → preserved; update split-brain detection guidance |
| `.claude/commands/etcd/QUICK_REFERENCE.md` | Replace cluster ID check with standalone_node check; update symptoms |
| `helpers/force-new-cluster.yml` | No changes |
