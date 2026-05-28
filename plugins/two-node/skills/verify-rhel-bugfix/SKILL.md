---
name: two-node:verify-rhel-bugfix
description: Verify a RHEL resource-agents bug fix on a TNF cluster — fetch Jira context, check cluster, patch nodes, run test, generate JIRA report
argument-hint: "<RHEL-XXXXX or URL>"
allowed-tools: Agent, AskUserQuestion, Write, Read, Glob, Grep, Bash, mcp__mcp-atlassian__jira_get_issue, mcp__mcp-atlassian__jira_search, mcp__mcp-atlassian__jira_add_comment
user-invocable: true
---

# two-node:verify-rhel-bugfix

## Synopsis

```text
/two-node:verify-rhel-bugfix RHEL-157145
/two-node:verify-rhel-bugfix https://issues.redhat.com/browse/RHEL-157145
```

## Description

Verify a RHEL resource-agents bug fix on a Two-Node with Fencing (TNF)
cluster. Fetches the full bug context from Jira (title, z-stream, upstream
PR, linked OCPEDGE tracking ticket, test instructions), checks cluster
state, patches nodes with the fixed RPM, runs verification tests, and
generates a JIRA comment report.

## Arguments

- `$ARGUMENTS` (required): A JIRA issue key or URL
  - **Issue key** (e.g., `RHEL-157145`): Use as JIRA ID directly
  - **URL** (e.g., `https://issues.redhat.com/browse/RHEL-157145`): Extract JIRA ID from the URL
  - **No argument**: Ask the user for the JIRA ID

## Prerequisites

This skill requires the `mcp-atlassian` MCP server for Jira access (configured
via the plugin's `.mcp.json`). If the `mcp__mcp-atlassian__jira_get_issue` tool
is not available, stop and show setup instructions (see `create-rhel-stories`
skill for reference).

Additional requirements:

1. SSH access to a hypervisor running a TNF cluster (via two-node-toolbox)
2. The RPM with the fix downloaded locally (typically in `~/Downloads/`)

Determine the hypervisor IP:

```bash
cd two-node-toolbox/deploy && make info 2>/dev/null | grep "Host:" | awk '{print $2}'
```

If `two-node-toolbox/` is not at the repo root, ask the user for the
hypervisor IP.

Store as `HYPERVISOR` for all subsequent steps.

## Scripts Directory

All scripts are run relative to the plugin directory:

```bash
SCRIPTS_DIR=${PLUGIN_DIR}/scripts
```

Available scripts:

- `verify-cluster.sh` — Cluster health check (OCP, nodes, pcs, etcd, RPM versions)
- `patch-nodes.sh <path-to-rpm> [grep-pattern]` — RPM patching with persistent override + reboot + verification
- `collect-logs.sh [minutes-ago] [output-dir]` — Collect pacemaker/etcd logs from both nodes

All scripts auto-detect the hypervisor IP from `two-node-toolbox/deploy`
if present, or fall back to the `HYPERVISOR` environment variable.

---

## Workflow

### Step 1: Gather Bug Information from Jira

Fetch the RHEL ticket and all connected tickets to build the full context
automatically. Only ask the user for what can't be derived from Jira.

### Step 1a: Fetch the RHEL Ticket

```text
mcp__mcp-atlassian__jira_get_issue(
  issue_key="<RHEL-XXXXX>",
  fields="summary,status,description,fixVersions,issuelinks,components,priority,customfield_10879,customfield_10638",
  comment_limit=5
)
```

Extract:

- **Bug title** — from `summary`
- **Target z-stream** — from `fixVersions` (e.g., `rhel-9.8.z`)
- **RPM NVR** — from `Fixed in Build` field or `fixVersions` context
- **Issue links** — all linked tickets (clones, relates, blocks)
- **Test instructions** — scan `description` and comments for "how to test",
  "test steps", "verification steps", or similar patterns
- **Status** — current ticket status

### Step 1b: Follow Links to Connected Tickets

From the RHEL ticket's `issuelinks`, identify and fetch:

**Parent/upstream bug** — look for links of type "is cloned by" or "clones"
pointing to OCPBUGS or another RHEL ticket. Fetch it:

```text
mcp__mcp-atlassian__jira_get_issue(
  issue_key="<OCPBUGS-XXXXX>",
  fields="summary,description,issuelinks,fixVersions,components",
  comment_limit=5
)
```

From the upstream bug, extract:

- **Upstream PR** — scan description and comments for GitHub PR links
  (e.g., `github.com/ClusterLabs/resource-agents/pull/XXXX` or
  `ClusterLabs/resource-agents#XXXX`)
- **Fix commit** — scan for commit hashes or "merged as" references
- **Author** — from the PR or commit reference
- Additional test instructions from developer comments

**OCPEDGE tracking ticket** — look for links of type "Relates" or
"is related to" pointing to OCPEDGE tickets. If found, fetch it:

```text
mcp__mcp-atlassian__jira_get_issue(
  issue_key="<OCPEDGE-XXXXX>",
  fields="summary,status,customfield_10020",
  comment_limit=0
)
```

From the OCPEDGE ticket, extract:

- **Sprint** — from `customfield_10020` (sprint field) or the ticket summary
- **Tracking ticket key** — the OCPEDGE ticket itself

**Sibling clones** — if the RHEL ticket has clone links to other RHEL
tickets (same fix, different z-streams), note them for context. These are
other z-stream verifications of the same fix.

### Step 1c: Present Collected Information

Present all gathered information to the user in a summary:

```markdown
## Bug Context (from Jira)

- **RHEL ticket:** RHEL-XXXXX — <title>
- **Status:** <status>
- **Target z-stream:** <fixVersion>
- **Parent/upstream bug:** OCPBUGS-XXXXX (or "none found")
- **Upstream PR:** ClusterLabs/resource-agents#XXXX (or "none found")
- **Fix commit:** <hash> (or "not found — will verify from RPM")
- **Author:** <author> (or "unknown")
- **OCPEDGE tracking:** OCPEDGE-XXXX / Sprint XXX (or "none found")
- **Sibling z-stream tickets:** RHEL-AAAA (9.6.z), RHEL-BBBB (9.7.z)
- **Test instructions from Jira:** <extracted instructions or "none found">
```

### Step 1d: Ask User for Remaining Information

Only ask for what wasn't found in Jira:

1. **RPM to test** — ask: "What's the RPM filename? (e.g.,
   resource-agents-4.10.0-108.el9_8.2.x86_64.rpm)"
   Then check if the RPM exists in `~/Downloads/` automatically:

   ```bash
   ls ~/Downloads/*resource-agents* 2>/dev/null
   ```

   If multiple matches, ask the user to pick one.

2. **Target z-stream** — only if not found in `fixVersions`. Use
   AskUserQuestion:
   > "Which RHEL z-stream is this fix targeting?"
   > Options:
   > - rhel-9.6.z (OCP 4.19.x / 4.22.x-ec, RHCOS 9.6)
   > - rhel-9.7.z (OCP 4.20.x, RHCOS 9.7)
   > - rhel-9.8.z (OCP 4.21.x / 4.22.x-ec, RHCOS 9.8)
   > - rhel-10.1.z (OCP 4.22.x + osImageStream rebase to RHEL 10)

3. **Upstream PR** — only if not found in linked tickets. Ask: "Link to the
   upstream PR? (e.g., ClusterLabs/resource-agents#2136, or 'none')"

4. **Test instructions** — only if nothing found in Jira. Ask: "Are there
   specific test instructions from the developer? (paste them, a URL, or
   'no' to figure it out from the bug description)"

5. **Verification type** — always ask, since this is a judgment call.
   Use AskUserQuestion:
   > "What type of verification is needed?"
   > Options:
   > - Functional test (the bug can be reproduced or fix behavior observed)
   > - Code-only verification (bug can't be reproduced locally, verify fix
   >   code is present in the RPM)

### Step 2: Check Cluster State

Run the cluster health check script:

```bash
export HYPERVISOR="<ip>" && bash "${PLUGIN_DIR}/scripts/verify-cluster.sh"
```

From the output, compare the running cluster's RHEL version with the target
z-stream. Present the status to the user:

- If matching: "Cluster is running the right RHEL version. Ready to patch."
- If not matching: "Cluster is running RHEL X.Y but the fix targets RHEL A.B.
  You'll need to redeploy or rebase."
- If no cluster: "No cluster running. You'll need to deploy one."

If a cluster change is needed, present options via AskUserQuestion:

- Redeploy cluster (update config_fencing.sh and `make redeploy-cluster`)
- Deploy from scratch (`make clean && make fencing-ipi`)
- Apply osImageStream rebase (for RHEL 10 targets)
- Skip (user will handle it manually)

**Do NOT deploy or redeploy the cluster yourself.** Only inform the user
what's needed. Cluster lifecycle is the user's responsibility.

### Step 3: Patch Nodes

Only proceed if the cluster is ready and the RPM is available locally.

Run the patching script:

```bash
export HYPERVISOR="<ip>" && bash "${PLUGIN_DIR}/scripts/patch-nodes.sh" ~/Downloads/<rpm-file> [grep-pattern]
```

The script will:

1. Copy RPM to hypervisor, then to both nodes
2. Apply `rpm-ostree override replace -C` on both nodes
3. Reboot both nodes
4. Wait for them to come back (up to 10 minutes)
5. Verify RPM version on both nodes
6. Optionally verify fix code presence (if grep pattern provided)

After the script completes, verify the fix is present by examining the
relevant source file on the nodes. For resource-agents bugs, this is
typically `/usr/lib/ocf/resource.d/heartbeat/podman-etcd`.

If the local resource-agents source repo is available, check it for fix
details before asking the user.

### Step 4: Run the Test

This step varies per bug. Based on the verification type:

#### Code-Only Verification

- Show RPM version on both nodes
- grep for fix code indicators in the target file (usually
  `/usr/lib/ocf/resource.d/heartbeat/podman-etcd`)
- Use `sed -n` to show the relevant code sections
- Document which lines contain each fix component
- Present findings to the user for approval

#### Functional Test

Execute the test steps provided by the developer (from Jira) or designed
based on the bug description. Common patterns:

| Pattern | Key Commands |
|---------|-------------|
| Shutdown + restart | `sudo virsh shutdown/start ostest_master_X` |
| STONITH fencing | `pcs stonith fence <node>` |
| Standby/unstandby | `pcs node standby/unstandby <node>` |
| Resource disable/enable | `pcs resource disable/enable etcd-clone` |
| Attribute manipulation | `crm_attribute --update/--delete --name <attr>` |
| Log counting | `grep -c "<pattern>" /var/log/pacemaker/pacemaker.log` |

All commands must be run via SSH through the hypervisor:

```bash
ssh ec2-user@${HYPERVISOR} "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null core@192.168.111.20 '<command>'"
```

After each test:

- Collect relevant logs from `/var/log/pacemaker/pacemaker.log`
- Check etcd configuration logs (ETCD_NAME, ETCD_INITIAL_CLUSTER, etc.)
- Verify cluster health (`pcs status`, etcd member list, etcd endpoint health)

**Always ask the user before destructive operations** like shutting down VMs
or fencing nodes.

#### Log Collection

Use the log collection script when you need a full log dump:

```bash
export HYPERVISOR="<ip>" && bash "${PLUGIN_DIR}/scripts/collect-logs.sh" [minutes-ago]
```

Default is 30 minutes. Logs are saved to `/tmp/bugfix-verify-logs/<timestamp>/`.

### Step 5: Generate Report

1. Compile all results into a JIRA comment using Markdown format.
   Use this structure:

   ```markdown
   ### Verification of RHEL-XXXXX - Bug Title

   #### Environment
   - **OCP Version:** <version>
   - **Topology:** Two-Node with Fencing (TNF)
   - **Platform:** Baremetal IPI (libvirt dev-scripts)
   - **RHCOS:** <version>
   - **resource-agents RPM:** <NVR>
   - **Nodes:** master-0 (192.168.111.20), master-1 (192.168.111.21)

   #### Fix Details
   - **Upstream PR:** [ClusterLabs/resource-agents#<PR>](<URL>)
   - **Commit:** `<hash>` - "<message>"
   - **Author:** <author>
   - **RHEL tracker:** <parent JIRA ID>

   #### Code Verification
   <RPM version confirmation + grep/sed output showing fix code>

   #### Functional Test
   <Test steps and command output — only if functional test was performed>

   #### Summary
   | Check | Result |
   |-------|--------|
   | Fix present in podman-etcd script | **PASS** |
   | <check 2> | **PASS** |

   #### Conclusion
   <One paragraph verdict>
   ```

2. Save the report to `/tmp/verify-rhel-bugfix-RHEL-XXXXX/report.txt`

3. Present the report to the user for review.

4. Ask if they want to post the comment to JIRA automatically:
   > "Post this report as a comment on RHEL-XXXXX? (yes/no)"

   If yes:

   ```text
   mcp__mcp-atlassian__jira_add_comment(
     issue_key="<RHEL-XXXXX>",
     comment="<report content>"
   )
   ```

   If the RHEL ticket has sibling clones (same fix, different z-streams),
   ask if the user wants to post the same report to those tickets too.

---

## Critical Rules

- **Always use `rpm-ostree override replace -C`** for patching, never
  `rpm-ostree usroverlay` (changes lost on reboot)
- **Always verify fix code on nodes** after patching — don't assume the
  RPM contains the fix
- **Check the resource-agents source repo** if available locally for fix
  details before asking the user
- **The report format is Markdown** for JIRA comments
- **Ask before destructive operations** like shutting down VMs or fencing nodes
- **Do NOT deploy or destroy clusters** — cluster lifecycle is the user's job
- **All SSH to nodes goes through the hypervisor** — nodes are not directly
  accessible from the local machine
- **Prefer Jira data over asking the user** — only ask for information that
  wasn't found in the tickets

## Examples

### Example 1: Standard RHEL Bug Fix Verification

```text
/two-node:verify-rhel-bugfix RHEL-157145
```

Fetches RHEL-157145 from Jira, discovers linked OCPBUGS upstream bug and
OCPEDGE tracking ticket, checks cluster, patches nodes, runs verification,
and generates report.

### Example 2: URL Input

```text
/two-node:verify-rhel-bugfix https://issues.redhat.com/browse/RHEL-150700
```

Extracts `RHEL-150700` from the URL and runs the same workflow.

## Notes

- This skill is **read-write**: it runs scripts that SSH to cluster nodes,
  reboot VMs, and replace RPMs. It can also post comments to Jira via MCP.
- The skill does NOT provision or manage cluster lifecycle — the user must
  have a running TNF cluster before invoking.
- Only resource-agents bugs on TNF clusters are supported. For TNA (arbiter)
  topology or other components, use `/two-node:bug-reproducer` instead.
- Reports from past verifications can be used as format reference if available.
