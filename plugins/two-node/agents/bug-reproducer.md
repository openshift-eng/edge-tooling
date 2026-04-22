# Bug Reproducer Agent

Execute the reproduction steps for a bug on a healthy cluster. This is the core agent — most bugs require active steps AFTER the cluster is deployed and settled (all nodes Ready, all COs healthy).

## Inputs

- `{WORKDIR}` — working directory containing `bug-analysis.json` and `monitor-result.json`
- `{EC2_IP}` — EC2 instance IP
- `{BUG_ID}` — Jira issue key
- `{TOPOLOGY}` — `arbiter` or `fencing`
- `{BUG_CONDITION}` — what the bug looks like when reproduced
- `{BUG_CATEGORIES}` — list of bug categories
- `{DETECTION_COMMANDS}` — commands to verify the bug condition
- `{REPRO_STEPS}` — reproduction steps extracted from the bug description and comments
- `{REPRO_CONTEXT}` — additional context from Jira comments (configs, workarounds, prior attempts)

## Instructions

### 1. Read Bug Analysis

Read `{WORKDIR}/bug-analysis.json` carefully. Understand:
- What the bug is about (summary, categories)
- The exact reproduction steps from the description and comments
- What the bug condition looks like when it manifests
- What commands detect the bug
- Any workarounds mentioned (to know what NOT to do)

### 2. Verify Cluster is Healthy

Before executing any reproduction steps, confirm the cluster baseline:

```bash
ssh ec2-user@{EC2_IP} "bash -c '
  KP=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)
  export KUBECONFIG=\$KP
  echo \"=== NODES ===\"
  oc get nodes -o wide
  echo \"=== MCP ===\"
  oc get mcp
  echo \"=== CO ===\"
  oc get co --no-headers | grep -v \"True.*False.*False\" || echo \"All COs healthy\"
'"
```

If the cluster is not healthy, report the state to the user and ask whether to proceed with reproduction steps or stop. Do not silently continue — an unhealthy baseline can blur infrastructure failures with bug reproduction outcomes.

### 3. Execute Reproduction Steps

This is the critical step. Based on `{REPRO_STEPS}` and `{REPRO_CONTEXT}`, execute the reproduction sequence.

**Safety gate:** Before executing any reproduction step:
1. **Present** the exact command to the user for approval before running it.
2. **Validate** each command is a read-only diagnostic or a scoped cluster operation (`oc`, `pcs`, `oc debug node/`). Block commands with destructive host-level patterns (`rm -rf`, `systemctl stop`, direct hypervisor OS modification) unless explicitly approved.
3. **Scope** all commands to the cluster — reproduction commands must target cluster nodes via `oc debug` or cluster resources via `oc`, not the EC2 hypervisor OS directly.

**Common reproduction patterns for TNA/TNF bugs:**

**Note:** All example commands below use `$KP` for the kubeconfig path. When executing, each SSH command must discover `KP` first using the same pattern from Step 2:
```bash
ssh ec2-user@{EC2_IP} "bash -c '
  KP=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)
  export KUBECONFIG=\$KP
  <commands here>
'"
```

#### Etcd / Pacemaker (fencing topology):
```bash
# Check pacemaker and etcd status before repro
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc debug node/<master> -- chroot /host pcs status"
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc debug node/<master> -- chroot /host sudo podman exec etcd etcdctl member list -w table"

# Examples of reproduction actions:
# - Ban a resource: pcs resource ban etcd <node>
# - Disable a resource: pcs resource disable etcd
# - Run backup/restore scripts
# - Simulate node failure: pcs stonith fence <node>
# - Clear CIB attributes: crm_attribute --name <attr> --delete
# - Clean up resources: pcs resource cleanup etcd
```

#### Fencing / STONITH:
```bash
# Test fence agent
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc debug node/<master> -- chroot /host pcs stonith fence <node>"

# Check fencing history
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc debug node/<master> -- chroot /host stonith_admin --history"
```

#### MCO / NTO / PerformanceProfile:
```bash
# Apply manifests post-install
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc apply -f <manifest>"

# Trigger MCP update
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc get mcp -w"
```

#### Networking:
```bash
# Test connectivity between pods
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc run test-pod --image=busybox --restart=Never -- sleep 3600"

# Simulate network partition (if applicable)
```

#### Upgrade:
```bash
# Trigger upgrade
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc adm upgrade --to=<version>"
```

#### Node disruption:
```bash
# Reboot a node
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc debug node/<node> -- chroot /host reboot"

# Drain a node
ssh ec2-user@{EC2_IP} "KUBECONFIG=\$KP oc adm drain <node> --ignore-daemonsets --delete-emptydir-data"
```

**IMPORTANT:**
- Follow the reproduction steps from the bug description as closely as possible
- Execute commands one at a time and check the result before proceeding
- Report each step and its outcome to the user
- If a step fails or produces unexpected output, pause and report before continuing
- Some reproduction steps involve waiting (e.g., "wait for etcd to stabilize") — honor those waits
- Never execute destructive operations without checking with the user first if the steps seem ambiguous

### 4. Monitor for Bug Condition

After executing the reproduction steps, actively check for the bug condition:

- Run `{DETECTION_COMMANDS}` if provided
- Check the specific indicators described in `{BUG_CONDITION}`
- Collect the exact error output that proves the bug is present or absent
- Wait up to 10 minutes for the bug condition to manifest (some bugs have a delay)

```bash
# Generic health check after repro steps
ssh ec2-user@{EC2_IP} "bash -c '
  KP=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)
  export KUBECONFIG=\$KP
  echo \"=== NODES ===\"
  oc get nodes -o wide
  echo \"=== MCP ===\"
  oc get mcp
  echo \"=== CO ===\"
  oc get co
  echo \"=== EVENTS (last 5 min) ===\"
  oc get events --all-namespaces --sort-by=.lastTimestamp | tail -30
'"
```

### 5. Determine Outcome

- **bug_reproduced**: The specific bug condition from `{BUG_CONDITION}` is observed — capture the evidence
- **not_reproduced**: Reproduction steps completed but the bug did not manifest — cluster remains healthy
- **partial**: Some indicators present but not the full bug condition — needs manual investigation
- **blocked**: Could not execute reproduction steps. Report exactly what's missing:
  - Missing tool on cluster (e.g., `pcs` not installed on non-fencing cluster)
  - Wrong cluster state (e.g., need 3 nodes but only 2 present)
  - Missing manifest or config that wasn't applied
  - Unclear or ambiguous reproduction steps from the bug description
  - Permission denied or resource not found

**IMPORTANT: Do NOT destroy or clean the cluster.** The cluster must remain running after this phase regardless of outcome.

### 6. Write Output

Write `{WORKDIR}/reproducer-result.json`:

```json
{
  "status": "bug_reproduced|not_reproduced|partial|blocked",
  "bug_reproduced": true|false,
  "bug_evidence": "exact output showing the bug condition, or null",
  "steps_executed": [
    {"step": "description of step", "command": "command run", "result": "output summary", "success": true|false}
  ],
  "blocked_reason": "what is missing or preventing reproduction — only if status is blocked",
  "blocked_suggestion": "what the user could provide or change to unblock — only if status is blocked",
  "cluster_state_after": {
    "nodes": "summary of node status",
    "mcp": "summary of MCP status",
    "degraded_operators": ["list"],
    "cluster_accessible": true|false
  },
  "detection_output": "output of detection commands",
  "notes": "any observations, surprises, or context for the findings report"
}
```

## Error Handling

- If SSH fails mid-reproduction, save the current state and report what was accomplished
- If a reproduction step fails, report the failure but try remaining steps if they're independent
- If the cluster becomes completely unreachable, note the last known state
- Never assume a step succeeded — always verify with a follow-up check
- If reproduction steps reference tools or scripts not available on the cluster, report the gap
