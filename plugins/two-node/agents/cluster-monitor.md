# Cluster Monitor Agent

Wait for the cluster to be healthy after deployment, apply day-1 manifests if needed, and confirm the cluster is ready for bug reproduction steps.

For bugs that manifest DURING installation (e.g., bootstrap MC mismatch), this agent also checks for the bug condition while waiting.

## Inputs

- `{WORKDIR}` — working directory containing `bug-analysis.json` and `deploy-result.json`
- `{EC2_IP}` — EC2 instance IP
- `{TOPOLOGY}` — `arbiter` or `fencing`
- `{MANIFEST_PHASE}` — `day-0`, `day-1`, `unknown`, or `null`
- `{BUG_CONDITION}` — brief description of the expected bug condition
- `{BUG_CATEGORIES}` — list of bug categories (e.g., `["etcd", "fencing"]`)
- `{DETECTION_COMMANDS}` — specific commands to check for the bug condition
- `{REPRO_TIMING}` — `during-install`, `post-install`, or `both`

## Instructions

### 0. Setup Remote Access

Find the kubeconfig on EC2:

```bash
KUBECONFIG_PATH=$(ssh ec2-user@{EC2_IP} "ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null" 2>/dev/null)
```

If the kubeconfig doesn't exist yet, wait 2 minutes and retry (max 5 retries).

All `oc` commands run via SSH using the discovered path:
```bash
ssh ec2-user@{EC2_IP} "KUBECONFIG=$KUBECONFIG_PATH oc <command>"
```

### 1. Apply Day-1 Manifests (if applicable)

If `{MANIFEST_PHASE}` is `day-1` and manifests exist in `{WORKDIR}/manifests/`:

```bash
ssh ec2-user@{EC2_IP} "mkdir -p ~/manifests"
scp {WORKDIR}/manifests/*.yaml ec2-user@{EC2_IP}:~/manifests/
ssh ec2-user@{EC2_IP} "KUBECONFIG=$KUBECONFIG_PATH oc apply -f ~/manifests/"
```

Wait 2-3 minutes after applying for operators to reconcile.

If `{MANIFEST_PHASE}` is `null` or `day-0`, skip this step.

### 2. Wait for Cluster to Settle

Poll every 3 minutes until the cluster reaches a stable state OR a during-install bug is detected. A cluster is "settled" when ALL of:

- All nodes are `Ready`
- All MCPs show `UPDATED=True`, `UPDATING=False`, `DEGRADED=False`
- No ClusterOperators are `Degraded=True` or `Progressing=True` (allow a few minutes for settling)

```bash
ssh ec2-user@{EC2_IP} "KUBECONFIG=$KUBECONFIG_PATH bash -c '
  echo \"=== NODES ===\"
  oc get nodes -o wide
  echo \"=== MCP ===\"
  oc get mcp
  echo \"=== CO ===\"
  oc get co --no-headers | grep -v \"True.*False.*False\" || echo \"All COs healthy\"
'"
```

**Maximum wait: 30 minutes after deployment completes.** Report status to the user at each check.

### 3. During-Install Bug Detection

If `{REPRO_TIMING}` is `during-install` or `both`, check for the bug condition at each poll iteration:

- Run `{DETECTION_COMMANDS}` if provided — these are read-only inspection commands (e.g., `oc get`, `pcs status`, log checks). Review each command before execution and skip any that are destructive or modify cluster state.
- Check for category-specific during-install indicators:
  - **mco/nto**: MCP DEGRADED with "bootstrap generated MC ... do not match"
  - **installer**: Bootstrap stuck, install log errors
  - **networking**: OVN CrashLoopBackOff during cluster bring-up

If the bug is detected during install, mark `bug_reproduced: true` and note `repro_timing: "during-install"`.

### 4. Detect Infrastructure Problems

Flag these as infrastructure issues (NOT the target bug unless the bug IS about these):

- **OVN CrashLoopBackOff on arbiter** — known OVN arbiter bug (`upgrade hack: unable to find LRSR for node arbiter-0`)
- **Cluster API unreachable after 15 min** — deployment likely failed
- **Nodes stuck NotReady with no MCP activity** — possible networking/bootstrap failure

If an infrastructure problem is detected, warn the user — this may prevent bug reproduction.

### 5. Write Output

Write `{WORKDIR}/monitor-result.json`:

```json
{
  "status": "cluster_ready|during_install_bug|stuck|failed",
  "cluster_ready": true|false,
  "bug_reproduced_during_install": true|false,
  "bug_evidence": "specific output if bug found during install, or null",
  "nodes": [{"name": "...", "status": "Ready|NotReady", "roles": "..."}],
  "mcp_status": [{"name": "master", "updated": 2, "degraded": 0}],
  "degraded_operators": ["list of any still-degraded COs"],
  "infrastructure_issues": ["list of any infra problems detected"],
  "total_checks": 10,
  "duration_minutes": 30,
  "final_check_time": "ISO8601 timestamp"
}
```

### 6. Early Exit

- **cluster_ready**: All healthy — proceed to bug reproduction steps
- **during_install_bug**: Bug found during install — skip reproduction steps, go to log collection
- **stuck**: Cluster not settling — warn user, ask whether to proceed anyway
- **failed**: Cluster API unreachable — deployment failed, stop

**IMPORTANT: Do NOT destroy or clean the cluster.** This agent only monitors — it never runs cleanup, `make clean`, or any destructive operations.
