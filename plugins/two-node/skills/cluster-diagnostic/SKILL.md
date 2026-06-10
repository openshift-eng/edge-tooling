---
name: two-node:cluster-diagnostic
description: Diagnose TNF cluster issues (shutdown/recovery, etcd, fencing, network, operators), validate proposed procedures, and recommend correct actions based on bare metal test findings
argument-hint: "[diagnose | validate <procedure> | recovery-guide <scenario> | game]"
allowed-tools: Bash, Read, AskUserQuestion
user-invocable: true
---

# two-node:cluster-diagnostic

## Synopsis

```text
/two-node:cluster-diagnostic
/two-node:cluster-diagnostic validate "cordon all nodes, drain, then shutdown -h 1 on each"
/two-node:cluster-diagnostic recovery-guide standby
/two-node:cluster-diagnostic recovery-guide full-shutdown
```

## Description

Diagnose shutdown and recovery issues on TNF (Two Nodes with Fencing) clusters.
Gathers live cluster state via SSH, analyzes it against a validated knowledge
base of 7 bare metal test scenarios, and reports findings with severity
classification. Read-only — never modifies cluster state.

## Prerequisites

SSH access to a running TNF cluster via one of:

1. **Hypervisor hop** (dev-scripts): Set `HYPERVISOR` env var or have
   `two-node-toolbox/` available for auto-detection
2. **Direct SSH** (bare metal): Set `NODE_0` and `NODE_1` env vars to node IPs

Optional: `BMC_0`, `BMC_1`, `BMC_USER`, `BMC_PASS` for Redfish power state.
Optional: `SSH_KEY` for SSH private key path (defaults to `~/.ssh/id_rsa`).

The `validate` and `recovery-guide` modes do not require SSH access.

## Modes

### diagnose (default)

No argument or `diagnose`. Gathers live cluster state and analyzes it.

1. Run the diagnostic script:

   ```bash
   bash "${PLUGIN_DIR}/scripts/diagnose-cluster.sh"
   ```

   Set `HYPERVISOR`, or `NODE_0`/`NODE_1`, or rely on auto-detection from
   `two-node-toolbox/deploy`.

2. Read the knowledge base:

   ```text
   Read ${PLUGIN_DIR}/references/cluster-knowledge-base.md
   ```

3. Parse each `=== Section ===` block from the script output.
4. Check for immediate blockers (see severity table in knowledge base).
5. Match the situation to a known scenario (test run cross-reference).
6. Estimate timeline from the recovery timelines table.
7. Report using the output format below.

### validate

Argument starts with `validate`. Checks a proposed procedure.

1. Read the knowledge base.
2. Parse the procedure from `$ARGUMENTS` (everything after `validate`).
3. Check each step against the 7 failure modes.
4. Report BLOCKER/WARNING/INFO findings for each problematic step.

### recovery-guide

Argument starts with `recovery-guide`. Returns the correct procedure.

Available scenarios: `standby`, `full-shutdown`, `single-node`,
`network-partition`, `power-outage`, `rolling-restart`, `after-recovery`,
`connectivity`, `etcd-nospace`, `pending-csr`, `split-brain`, `stale-data`,
`partition-fencing-failure`.

1. Read the knowledge base.
2. Match the scenario to the correct procedure section.
3. Present the procedure with commands for the user to copy and run.
4. If no match, list available scenarios via `AskUserQuestion`.

## Output Format

```markdown
## TNF Cluster Diagnostic Report

### Cluster State Summary
| Component | Status |
|-----------|--------|
| Node 0    | (Online / standby / OFFLINE / NotReady) |
| Node 1    | (Online / standby / OFFLINE / NotReady) |
| Pacemaker | (Healthy / Degraded / Down) |
| etcd      | (2 voters / 1 voter + 1 learner / ...) |
| Fencing   | (Enabled / Disabled) |

### Findings
| # | Severity | Finding | Recommended Action |
|---|----------|---------|-------------------|

### Scenario Match
Closest match: (scenario from knowledge base)

### Expected Timeline
(from recovery timelines table)
```

## Rules

1. **Never execute** recovery or shutdown commands. Only recommend.
2. **Always read** `${PLUGIN_DIR}/references/cluster-knowledge-base.md` before
   making recommendations.
3. **Report partial data** if some diagnostic commands fail. Analyze what was
   gathered and note the gaps.
4. **No cluster modification.** This skill is read-only.

## game

Argument starts with `game`. Interactive training mode. No SSH required.

Read `${SKILL_DIR}/game-mode.md` for game content, then follow its instructions.
