---
name: two-node:bug-reproducer
description: Reproduce an OpenShift bug on a TNA (arbiter) or TNF (fencing) cluster — fetches Jira bug, deploys the right topology, monitors for the bug condition, collects logs, and generates a findings report
allowed-tools: Agent, AskUserQuestion, Write, Read, Glob, Grep, Bash
user-invocable: true
argument-hint: "OCPBUGS-XXXXX"
---

# Bug Reproducer TNA/TNF

Automate OpenShift bug reproduction on Two-Node with Arbiter (TNA) or Two-Node with Fencing (TNF) clusters. Given a Jira bug ID, this skill fetches the bug, detects the topology, extracts manifests, deploys the cluster via dev-scripts, monitors for the bug condition, collects logs, and generates a findings report.

## Synopsis

```text
/two-node:bug-reproducer OCPBUGS-66217
```

## Arguments

One required argument: a Jira issue key (e.g., `OCPBUGS-66217`). No flags.

## Prerequisites

This skill must be run inside a Claude Code session opened at the **Two-Node Toolbox (TNT) repo**, specifically at `two-node-toolbox/deploy/` or `two-node-toolbox/deploy/openshift-clusters/`.

Before running:
1. EC2 instance must be running (`make create && make init` or equivalent)
2. `make inventory` must have been run (populates `inventory.ini` with EC2 IP)
3. EC2 must have been configured (`./configure` or equivalent setup)
4. Pull secret must exist at `roles/dev-scripts/install-dev/files/pull-secret.json`
5. Jira credentials must be set in `~/.bashrc`: `export JIRA_USERNAME="you@redhat.com"` and `export JIRA_API_TOKEN="your-token"`

## Execution Model

The orchestrator (this file) coordinates 5 phases, each handled by a sub-agent. Agents write output to `$WORKDIR` via the `Write` tool. The main context reads those files between phases for guard checks.

Agent definitions are in `plugins/two-node/agents/`:
- `bug-analyzer.md` — Phase 1: Jira fetch, topology detection, repro steps extraction
- `cluster-deployer.md` — Phase 2: Config update + deployment (IPI, agent, or kcli)
- `cluster-monitor.md` — Phase 3: Wait for cluster to settle, detect during-install bugs
- `bug-reproducer.md` — Phase 4: Execute reproduction steps on the healthy cluster (most bugs)
- `log-collector.md` — Phase 5: Log collection + findings report (category-targeted logs)

---

## Workflow

### Step 0: Validate Environment (main context)

Parse `$ARGUMENTS` to extract the bug ID. If no argument provided, ask the user with `AskUserQuestion`.

```
BUG_ID = first argument (e.g., OCPBUGS-66217)
```

**Validate BUG_ID format:** Must match the pattern `OCPBUGS-[0-9]+`. If it doesn't, stop with:
> Invalid bug ID format: "$BUG_ID". Expected format: `OCPBUGS-XXXXX` (e.g., OCPBUGS-66217).

**Check 1: Working directory**

Verify the current working directory is within the TNT deploy tree:
```bash
pwd
```
Must contain `two-node-toolbox/deploy`. If not, stop with:
> This skill must be run from the Two-Node Toolbox deploy directory (`two-node-toolbox/deploy/` or `two-node-toolbox/deploy/openshift-clusters/`).

Determine the key paths:
```
TNT_DEPLOY_DIR = path to two-node-toolbox/deploy/openshift-clusters/
TNT_REPO_DIR = path to two-node-toolbox/
```

**Check 2: Inventory**

Read `inventory.ini` (at `$TNT_DEPLOY_DIR/inventory.ini`). Extract the EC2 IP from the `[metal_machine]` group. If no valid IP found, stop with:
> No EC2 IP found in inventory.ini. Run `make inventory` first.

```
EC2_IP = extracted IP address
```

**Check 3: SSH connectivity**

```bash
ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no ec2-user@$EC2_IP "echo OK" 2>&1
```
If this fails, stop with:
> Cannot SSH to EC2 at $EC2_IP. Ensure the instance is running and accessible.

**Check 4: Pull secret**

```bash
test -f "$TNT_DEPLOY_DIR/roles/dev-scripts/install-dev/files/pull-secret.json" && echo "EXISTS" || echo "MISSING"
```
If missing, stop with:
> Pull secret not found. Place your pull-secret.json at `roles/dev-scripts/install-dev/files/pull-secret.json`.

**Check 5: Jira credentials**

Check that both `JIRA_USERNAME` and `JIRA_API_TOKEN` are set as environment variables. Do NOT print or log the token value — only check that it is non-empty.

```bash
[ -n "$JIRA_USERNAME" ] && echo "JIRA_USERNAME=SET" || echo "JIRA_USERNAME=MISSING"
[ -n "$JIRA_API_TOKEN" ] && echo "JIRA_API_TOKEN=SET" || echo "JIRA_API_TOKEN=MISSING"
```

If either is missing, stop with:
> Jira credentials not found. The skill needs `JIRA_USERNAME` and `JIRA_API_TOKEN` to fetch bug details.
>
> **Step 1:** Save your Jira API token to a file (replace with your actual token):
>
> ```bash
> echo "your-jira-api-token" > ~/.jira-token && chmod 600 ~/.jira-token
> ```
>
> **Step 2:** Add the exports to `~/.bashrc` (replace the email with yours):
>
> ```bash
> echo 'export JIRA_USERNAME="your-email@redhat.com"' >> ~/.bashrc && echo 'export JIRA_API_TOKEN=$(cat ~/.jira-token 2>/dev/null)' >> ~/.bashrc && source ~/.bashrc
> ```
>
> To generate a Jira API token, go to: https://id.atlassian.com/manage-profile/security/api-tokens

**Create work directory:**

```bash
WORKDIR="/tmp/two-node-bug-reproduce-${BUG_ID}" && mkdir -p "$WORKDIR/manifests" && echo "$WORKDIR"
```

Record `WORKDIR` for all subsequent phases.

---

### Phase 1: Bug Analysis (spawn bug-analyzer agent)

Read `plugins/two-node/agents/bug-analyzer.md`. Substitute `{BUG_ID}` and `{WORKDIR}` placeholders, then spawn the agent.

**After agent completes**, read `$WORKDIR/bug-analysis.json` and apply guard checks:

1. If `"error"` key exists → show error to user and stop

2. **Test bug check (STOP GATE):** If `bug_type` is `"test"`:
   - Show to user:
   > **This appears to be a test bug, not a product bug.** Deploying a cluster will not help reproduce it.
   >
   > **Reason:** $TEST_BUG_REASON
   >
   > The fix for this bug is in test code, not in the cluster. Reproduction requires modifying test assertions or test preconditions, which this skill cannot do.
   - **Stop.** Do not proceed with deployment.

3. **Environment feasibility check:** If `environment_feasible` is `false`:
   - Show to user:
   > **Warning: Dev-scripts environment may not be able to reproduce this bug.**
   >
   > **Blockers:** $ENVIRONMENT_BLOCKERS
   >
   > This bug requires conditions that dev-scripts VMs cannot provide. Proceed anyway?
   - Wait for user confirmation via `AskUserQuestion`. If denied, stop.

4. If `topology` is `null` or `topology_confidence` is `low`:
   - Ask the user: "Could not determine topology from the bug. Is this an **arbiter** or **fencing** bug?"
   - Set `TOPOLOGY` from user response. **Must be exactly `arbiter` or `fencing`** — reject any other value and ask again.
5. If `ocp_version` is `null`:
   - Ask the user: "Could not determine OCP version from the bug. What version should be deployed? (e.g., 4.20.14)"
   - Set `OCP_VERSION` and compute `RELEASE_IMAGE`
6. Otherwise, extract `TOPOLOGY`, `RELEASE_IMAGE`, `CONFIG_HINTS`, `MANIFEST_PHASE`, `BUG_CATEGORIES`, `DETECTION_COMMANDS`, `INSTALL_METHOD`, and `REPRO_TIMING` from the analysis
7. If `manifest_phase` is `unknown` and manifests exist, ask the user: "Should these manifests be applied at day-0 (during install) or day-1 (after cluster is up)?"
8. If `install_method` is `kcli`, note that a different playbook (`kcli-install.yml`) will be used

**Show summary to user and confirm before proceeding:**

> **Bug:** $BUG_ID — $SUMMARY
> **Type:** Product bug
> **Topology:** $TOPOLOGY (confidence: $CONFIDENCE)
> **Install Method:** $INSTALL_METHOD
> **OCP Version:** $OCP_VERSION
> **Release Image:** $RELEASE_IMAGE
> **Bug Categories:** $BUG_CATEGORIES
> **Manifests:** $MANIFEST_LIST (phase: $MANIFEST_PHASE) — or "None"
> **Bug Condition:** $BUG_CONDITION
> **Environment:** Feasible / Feasible with caveats ($BLOCKERS)
>
> Proceed with deployment?

Wait for user confirmation via `AskUserQuestion`. If denied, stop.

---

### Phase 2: Cluster Deployment (spawn cluster-deployer agent)

Read `plugins/two-node/agents/cluster-deployer.md`. Substitute all `{VARIABLE}` placeholders:
- `{WORKDIR}`, `{TOPOLOGY}`, `{RELEASE_IMAGE}`, `{EC2_IP}`, `{TNT_DEPLOY_DIR}`, `{CONFIG_HINTS}`, `{MANIFEST_PHASE}`, `{INSTALL_METHOD}`

Spawn the agent. This is a long-running phase (45-90 minutes).

The deployer agent runs the `ansible-playbook` command and **monitors the deployment periodically** (see deployer agent for details). It checks every 10 minutes for signs of failure and can detect stalled installations early rather than waiting for the full 120-minute timeout.

**After agent completes**, read `$WORKDIR/deploy-result.json`:
- If `status` is `success`, proceed to Phase 3.
- If `status` is `failed`:
  1. Show the error to the user, including `failure_category` if available.
  2. If `auto_fixable` is `true` in the result: inform the user what fix will be attempted, then run Phase 2a (clean) and re-deploy with the fix applied. Example: if `CI_TOKEN` expired, the deployer can't fix that, but if `make requirements` failed due to a transient network error, a retry may work.
  3. Otherwise, ask: "Deployment failed. Would you like to **clean and retry**, **skip to log collection**, or **stop**?"
  4. If **clean and retry**: run `Phase 2a: Clean and Retry` (below), then re-spawn the cluster-deployer agent.
  5. If **skip to log collection**: jump to Phase 5.
  6. If **stop**: end the skill.
  - Maximum **1 automatic retry** allowed. If the retry also fails, ask the user before trying again.

#### Phase 2a: Clean and Retry (main context — only if deployment failed)

Clean the previous failed deployment before retrying:

```bash
cd "$TNT_DEPLOY_DIR" && ansible-playbook clean.yml -i inventory.ini -e "interactive_mode=false"
```

If `clean.yml` fails or doesn't exist for the install method, fall back to SSH cleanup:
```bash
ssh "ec2-user@$EC2_IP" "cd ~/dev-scripts && make clean 2>/dev/null; cd ~/openshift-metal3/dev-scripts && make clean 2>/dev/null; true"
```

After cleanup completes, re-spawn the cluster-deployer agent with the same parameters.

---

### Phase 3: Wait for Cluster Ready (spawn cluster-monitor agent)

Read `plugins/two-node/agents/cluster-monitor.md`. Substitute:
- `{WORKDIR}`, `{EC2_IP}`, `{TOPOLOGY}`, `{MANIFEST_PHASE}`, `{BUG_CONDITION}`, `{BUG_CATEGORIES}`, `{DETECTION_COMMANDS}`, `{REPRO_TIMING}`

Spawn the agent.

**After agent completes**, read `$WORKDIR/monitor-result.json`:
- If `status` is `during_install_bug`: bug was detected during install — skip Phase 4, go to Phase 5 (log collection).
- If `status` is `cluster_ready`: cluster is healthy — proceed to Phase 4 (reproduction steps).
- If `status` is `stuck`: inform user, ask whether to attempt reproduction steps anyway, skip to log collection, or **clean and redeploy** (go back to Phase 2a then Phase 2).
- If `status` is `failed`: the cluster API is unreachable — deployment likely failed silently. Ask user: "Cluster appears failed. **Clean and redeploy**, **skip to log collection**, or **stop**?" If clean and redeploy, run Phase 2a then Phase 2.

---

### Phase 4: Execute Reproduction Steps (spawn bug-reproducer agent)

**Skip this phase if:** the bug was already detected during install, or `REPRO_TIMING` is `during-install` only.

This is the core phase for most bugs. The cluster is healthy and we now execute the specific steps to trigger the bug.

Read `plugins/two-node/agents/bug-reproducer.md`. Substitute:
- `{WORKDIR}`, `{EC2_IP}`, `{BUG_ID}`, `{TOPOLOGY}`, `{BUG_CONDITION}`, `{BUG_CATEGORIES}`, `{DETECTION_COMMANDS}`, `{REPRO_STEPS}`, `{REPRO_CONTEXT}`

Spawn the agent.

**After agent completes**, read `$WORKDIR/reproducer-result.json` and check the `status` field:
- If `status` is `"bug_reproduced"`: inform user, proceed to Phase 5 (log collection).
- If `status` is `"not_reproduced"`: inform user the bug did not manifest. Still proceed to log collection for evidence.
- If `status` is `"partial"`: some indicators present — proceed to log collection for analysis.
- If `status` is `"blocked"`: could not execute steps — report why and what's missing. Ask user: "Reproduction blocked because: $REASON. Would you like to **provide the missing piece** (e.g., a command, a manifest, a config change), **retry with adjustments**, or **skip to log collection**?" If the user provides additional info, re-spawn the bug-reproducer agent with the updated context.

---

### Phase 5: Log Collection + Report (spawn log-collector agent)

Set `LOCAL_LOG_DIR=/tmp/two-node-bug-reproduce-$BUG_ID`.

Read `plugins/two-node/agents/log-collector.md`. Substitute:
- `{WORKDIR}`, `{EC2_IP}`, `{BUG_ID}`, `{LOCAL_LOG_DIR}`, `{TNT_REPO_DIR}`, `{BUG_CATEGORIES}`

Spawn the agent.

**After agent completes**, read `$WORKDIR/collection-result.json`:
- Show summary to user: logs location, findings report path, reproduction result
- If `bug_reproduced` is `true`, suggest next steps (e.g., paste findings into Jira comment)

---

### Step 6: Final Summary (main context)

Present the complete outcome to the user:

```
## Bug Reproduction Complete

**Bug:** $BUG_ID — $SUMMARY
**Result:** Reproduced / Not Reproduced / Inconclusive
**Topology:** $TOPOLOGY | **OCP Version:** $OCP_VERSION
**Cluster:** STILL RUNNING — available for manual inspection

**Logs:** $LOCAL_LOG_DIR/
**Findings Report:** $TNT_REPO_DIR/docs/$(echo $BUG_ID | tr '[:upper:]' '[:lower:]')-findings.md

### Next Steps
- Review the findings report
- SSH to EC2 and inspect the cluster: `ssh ec2-user@$EC2_IP`
- Paste key findings into the Jira bug as a comment
- If not reproduced, consider different OCP version or topology
- When done, clean up: `cd $TNT_DEPLOY_DIR && ansible-playbook clean.yml -i inventory.ini`
```

**IMPORTANT: The cluster is intentionally left running** so the user can SSH in, run `oc` commands, and inspect the state firsthand. The skill NEVER destroys or cleans the cluster on its own.

---

## Edge Cases

- **Bug has no YAML manifests**: Most bugs won't have manifests. Deploy cluster without `ASSETS_EXTRA_FOLDER` — the bug is about cluster behavior, not day-0 injection
- **Day-1 manifests**: If manifests need to be applied post-install, the monitor agent applies them via `oc apply` after the cluster is healthy, then watches for the bug condition
- **OVN arbiter bug**: If monitoring detects OVN CrashLoopBackOff on arbiter topology, warn user this is a known OVN issue, not the target bug
- **EC2 TTL**: EC2 instances auto-delete after 12 hours. If deployment takes too long, warn the user about remaining time
- **Bootstrap VM destroyed**: Bootkube logs may not be available if bootstrap VM was already cleaned up. Log collector handles this gracefully.
- **Config file already modified**: Config files (`config_arbiter.sh`, `config_fencing.sh`) are gitignored local copies — the deployer edits them directly, no backup/restore needed
- **Previous cluster still running**: If deploying and a previous cluster exists on the EC2, the deployer agent detects this and runs cleanup before proceeding (see Phase 2 in deployer agent)
- **Deployment stalled**: The deployer agent sets a 120-minute timeout on `ansible-playbook`. If it hangs beyond that, the agent reports `status: failed` with `error: timeout` — the orchestrator then offers clean-and-retry
- **Reproduction blocked by missing tool/state**: If the reproducer agent cannot execute steps (e.g., `pcs` not installed, wrong cluster state), it reports `status: blocked` with the reason — the orchestrator asks the user what to do rather than silently failing

## Critical Rules

1. **NEVER destroy the cluster after reproduction.** The cluster must remain running so the user can SSH in and inspect. Only the user decides when to clean up.
2. **NEVER run `clean.yml` or `make clean` without user confirmation** — except in Phase 2a (clean-and-retry after a failed deployment, which the user already approved) and in the deployer's pre-clean step when an existing cluster is detected before a new deployment (this is necessary to deploy cleanly).
3. **At most 1 automatic retry** for deployment failure. After that, ask the user.
4. **Always report the cluster's final state** in the summary — nodes, MCPs, COs, and whether the cluster is accessible.
5. **Test bugs are NOT reproducible.** If the bug analyzer classifies a bug as `bug_type: "test"`, stop immediately with a warning. Do not deploy a cluster.
6. **Warn on infeasible environments.** If `environment_feasible` is `false`, warn the user and get explicit confirmation before deploying.

## TNT Repository File Safety

The skill operates within the user's TNT repository. The user has already set up the EC2 instance, inventory, and configuration. **The skill must not modify TNT repo files except for the config files it needs to update for deployment.**

**Files the skill MAY modify:**
- `roles/dev-scripts/install-dev/files/config_arbiter.sh` — gitignored, local-only config
- `roles/dev-scripts/install-dev/files/config_fencing.sh` — gitignored, local-only config
- `vars/kcli.yml` — kcli config (only if install method is kcli)

**Files the skill MUST NOT modify:**
- `inventory.ini` — user manages this via `make inventory`
- `setup.yml`, `clean.yml`, `redfish.yml`, `kcli-install.yml` — playbooks
- Any file under `roles/` except the config files listed above
- Any file under `collections/`, `group_vars/`, `host_vars/`
- `Makefile`, `configure`, or any script in the repo
- Any file in the parent `deploy/` or repo root directories

**Commands the skill MAY run:**
- `ansible-playbook setup.yml ...` — deployment
- `ansible-playbook clean.yml ...` — cleanup (only with user approval)
- `ansible-playbook redfish.yml ...` — fencing config
- `ansible-playbook kcli-install.yml ...` — kcli deployment
- `ssh`/`scp` to EC2 — remote operations
- `rsync` from EC2 — log collection

**Commands the skill MUST NOT run:**
- `make create`, `make init`, `make inventory`, `make start`, `make stop`, `make destroy` — instance lifecycle is the user's job
- `./configure` — EC2 configuration is the user's job
- Any `git` commands that modify the repo (commit, push, checkout, reset)
- Any command that modifies TNT repo files beyond the allowed config files

## Notes

- This skill is **read-write**: it modifies gitignored config files, may upload manifests to EC2, and runs Ansible playbooks
- Deployment is the longest phase — expect 45-90 minutes
- The skill does NOT provision EC2 instances — the user must do that before invoking
- Only `arbiter` and `fencing` topologies are supported (no 3node, SNO, or MicroShift)
- The cluster is **always left running** after the skill completes — the user cleans up manually
