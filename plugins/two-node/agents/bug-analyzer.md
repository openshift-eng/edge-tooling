# Bug Analyzer Agent

Fetch a Jira bug (description + comments), detect the target topology, OCP version, reproduction configuration, and any manifests needed.

## Inputs

- `{BUG_ID}` — Jira issue key (e.g., `OCPBUGS-66217`)
- `{WORKDIR}` — working directory for output files

## Instructions

### 1. Fetch Bug from Jira

Use `jira_get_issue` MCP tool to fetch `{BUG_ID}`. Extract:

- `summary` — issue title
- `description` — full description text
- `components` — list of component names
- `labels` — list of labels
- `fixVersions` — list of fix version names
- `versions` (affects versions) — list of version names
- `status` — current status
- `priority` — priority level

Then use `jira_get_issue_comments` to fetch ALL comments on the bug. Comments often contain the most valuable reproduction data — successful reproduction configs, root cause analysis, log snippets, and workarounds.

If the fetch fails, write an error to `{WORKDIR}/bug-analysis.json` and stop:

```json
{"error": "Failed to fetch {BUG_ID}: <reason>"}
```

### 2. Detect Topology

Only two topologies are supported: `arbiter` and `fencing`. Evaluate in order:

**From components (high confidence):**
- Component is `Two Node with Arbiter` → `arbiter`
- Component is `Two Node Fencing` → `fencing`

**From labels (high confidence):**
- Contains "arbiter", "tna", "two-node-arbiter", "etcd-arbiter" → `arbiter`
- Contains "fencing", "tnf", "stonith", "pacemaker", "two-node-fencing" → `fencing`

**From description and comments (medium confidence):**
- `NUM_ARBITERS`, `etcd arbiter`, `arbiter node`, `arbiter-0`, `arbiter MCP`, `ENABLE_ARBITER` → `arbiter`
- `stonith`, `fencing agent`, `pacemaker`, `STONITH`, `BMC_DRIVER`, `redfish`, `fence_redfish`, `pcs status` → `fencing`

**Confidence levels:**
- `high` — matched from components or labels
- `medium` — matched from description or comment keywords
- `low` — no clear match; could not determine topology

If confidence is `low`, set `topology: null` — the orchestrator will ask the user.

### 3. Detect OCP Version

Scan `versions` (Affects Versions) and `fixVersions` for version patterns:
- Extract versions like `4.20`, `4.19`, `4.18`, `4.21`
- **Prefer `versions` (Affects Versions) over `fixVersions`** — Affects Versions is the version the bug was observed on, which is the version we want to deploy. Fix Versions is where the fix will land — deploying that version would mean the bug is already fixed.
- If a z-stream is specified (e.g., `4.20.14`), use it directly
- If only minor version (e.g., `4.20`), set `version_exact: false` — the orchestrator will ask the user for the exact version

Also check comments — engineers often specify the exact version they reproduced on (e.g., "I reproduced this on 4.20.14").

Format: `quay.io/openshift-release-dev/ocp-release:<version>-x86_64`

If no version found, set `ocp_version: null` — the orchestrator will ask the user.

### 4. Detect Bug Category

Classify the bug based on components, description, and comments. Categories:

| Category | Jira Components (exact names) | Keywords |
|----------|-------------------------------|----------|
| `etcd` | `Etcd` | etcd, quorum, member list, learner, cluster ID, etcdctl |
| `fencing` | `Two Node Fencing` | stonith, pacemaker, fence_redfish, pcs status, BMC, fencing loop, corosync |
| `mco` | `Machine Config Operator`, `Machine Config Operator / platform-baremetal` | MCP degraded, rendered MC, bootstrap MC, MachineConfig, machine-config-daemon |
| `nto` | `Node Tuning Operator`, `Performance Addon Operator` | PerformanceProfile, TuneD, kernel args, 50-nto, node-tuning |
| `networking` | `Networking / ovn-kubernetes`, `Networking / cluster-network-operator`, `Networking / DNS`, `Networking / On-Prem DNS`, `Networking / On-Prem Load Balancer`, `Networking / On-Prem Host Networking`, `Networking / router` | OVN, CoreDNS, pod connectivity, SDN, haproxy, keepalived |
| `storage` | `Storage`, `Storage / Kubernetes`, `Storage / Local Storage Operator` | CSI, PV, PVC, snapshot controller, storage class |
| `operator` | `Cluster Version Operator`, `config-operator` | ClusterOperator degraded, operator crash, controller-manager |
| `upgrade` | `Cluster Version Operator`, `Cluster Update Console Plugin` | upgrade, ClusterVersion, CVO, stuck updating, oc adm upgrade |
| `kubelet` | `Node / Kubelet` | NotReady, kubelet crash, pod eviction, kubelet.conf |
| `installer` | `Installer / OpenShift on Bare Metal IPI`, `Installer / Agent based installation`, `Installer / Assisted installer`, `Installer / openshift-installer` | bootstrap, install failure, openshift-install, rendezvous |
| `baremetal` | `Bare Metal Hardware Provisioning / baremetal-operator`, `Bare Metal Hardware Provisioning / cluster-baremetal-operator`, `Bare Metal Hardware Provisioning / ironic`, `Cloud Compute / BareMetal Provider` | BareMetalHost, BMH, ironic, metal3, detached |
| `apiserver` | `kube-apiserver`, `openshift-apiserver` | apiserver, API unavailable, kube-apiserver |
| `other` | none of the above | |

A bug can have multiple categories (e.g., `["mco", "nto"]` for a PP day-0 MC mismatch).

### 5. Extract Manifests (if any)

Most bugs will NOT have manifests. Only extract if the description or comments contain YAML code blocks with Kubernetes resources.

Look for:
- `kind: PerformanceProfile` — PP manifest
- `kind: KubeletConfig` — KC manifest
- `kind: MachineConfig` — MC manifest
- `kind: Tuned` — Tuned profile
- `kind: SriovNetworkNodePolicy` — SR-IOV config
- Any other `kind:` with `apiVersion:` and `metadata:`

For each found manifest:
- Validate it has `apiVersion`, `kind`, and `metadata.name`
- Save to `{WORKDIR}/manifests/<nn>-<name>.yaml`
- Use prefix `98-` for PP, `99-` for KC, `50-` for MC/Tuned

**Determine apply phase** — whether manifests should be applied at day-0 or day-1:
- **day-0**: Keywords like "day-0", "install time", "ASSETS_EXTRA_FOLDER", "extra_dir", "bootstrap", "during install"
- **day-1**: Keywords like "day-1", "day-2", "post-install", "oc apply", "oc create", "after install"
- **unknown**: If unclear, default to `unknown` — the orchestrator will ask

If no manifests found, set `manifests: []` and `manifest_phase: null`.

### 6. Extract Config Hints

Scan description AND comments for dev-scripts configuration hints. Look for:

**Cluster topology settings:**
- `NUM_MASTERS`, `NUM_WORKERS`, `NUM_ARBITERS`
- `MASTER_MEMORY`, `MASTER_VCPU`, `MASTER_DISK`
- `ARBITER_MEMORY`, `ARBITER_VCPU`

**Feature flags:**
- `cpuPartitioningMode: AllNodes` / `ENABLE_WORKLOAD_PARTITIONING=true`
- `FEATURE_SET` (TechPreviewNoUpgrade, DevPreviewNoUpgrade)
- `FIPS_MODE`

**Network settings:**
- `IP_STACK` (v4, v6, v4v6)
- `NETWORK_TYPE`
- `PROVISIONING_NETWORK_PROFILE`

**BMC/fencing settings (TNF):**
- `BMC_DRIVER` (redfish, ipmi)

**Image/registry settings:**
- `ENABLE_LOCAL_REGISTRY`
- `MIRROR_IMAGES`

**Storage settings:**
- `VM_EXTRADISKS`, `VM_EXTRADISKS_SIZE`

**Install method** (important — changes the make target and deployment flow):
- IPI: keywords `IPI`, `make all`, `dev-scripts`, `baremetal IPI`
- Agent-based: keywords `agent-based`, `agent installer`, `openshift-install agent`, `make agent`, `agent-config.yaml`
- kcli: keywords `kcli`, `kcli create cluster` (NOTE: kcli uses a different playbook `kcli-install.yml`, not `setup.yml`)
- If unclear, default to `ipi` — it's the most common method

### 7. Mine Comments for Reproduction Context

Search comments for high-value reproduction data. Prioritize comments that contain:

1. **Successful reproduction configs**: "I reproduced with...", "Tested on...", "Configuration:"
2. **Test results**: "PASS", "FAIL", "REPRODUCED", "Result:"
3. **Root cause analysis**: "The issue is...", "Looking at the code...", "Root cause:"
4. **Log evidence**: "Looking at bootkube-journal...", "From the logs..."
5. **Workarounds**: "Workaround:", "Instead of...", "This fixes it:"
6. **Detection commands**: Specific `oc` commands that reveal the bug condition

**Filter out noise:**
- Bot comments (status transitions, automated linking)
- Generic acknowledgments ("Got it", "Will look")
- Reassignment notices

Extract the most useful reproduction context into `repro_context`.

### 8. Determine Reproduction Timing

Classify WHEN the bug manifests:

- **during-install**: Bug appears during or immediately after deployment, before COs settle. Examples: bootstrap MC mismatch, OVN CrashLoopBackOff, install bootstrap failure. Keywords: "during install", "bootstrap", "day-0", "install fails"
- **post-install**: Bug requires a healthy cluster first, then specific steps to trigger. **This is the majority of bugs.** Examples: etcd rejoin deadlock, fencing loop, operator degradation after config change. Keywords: reproduction steps that reference `pcs resource`, `oc apply`, `oc adm upgrade`, node reboot, backup/restore, etc.
- **both**: Bug can appear during install OR be triggered post-install

If unclear, default to `post-install` — most bugs need active reproduction steps.

### 9. Detect Test-Only Bugs

Determine whether this bug is a **product bug** (cluster behavior issue) or a **test bug** (e2e test code issue). Test bugs cannot be reproduced by deploying a cluster — they require fixing test code.

**Signals that a bug is a test-only issue:**

- Summary or description references a specific test name (e.g., `[sig-etcd]`, `[sig-installer]`, `[Feature:baremetal]`, `[Serial]`)
- The fix is in test code files (e.g., `test/extended/`, `helpers.go`, `_test.go`, `tnf_recovery.go`)
- The root cause is a wrong test assertion, not wrong cluster behavior (e.g., "test expects X but cluster correctly does Y")
- The bug is about a test environment mismatch (e.g., "test assumes workers exist but two-node has none", "test expects secret that doesn't exist on this topology")
- Keywords: "test flake", "test assertion", "test precondition", "test design issue", "test assumes", "test should be skipped"
- The "Expected results" section says the test should be fixed/skipped, not that the cluster should behave differently

**Signals that confirm it IS a product bug (not a test bug):**

- The fix is in product code (operators, resource agents, shell scripts, Go controllers)
- The cluster exhibits wrong behavior regardless of how you detect it
- The bug has concrete reproduction steps involving cluster operations (not running a test binary)

If classified as a test bug, set `"bug_type": "test"` and `"test_bug_reason": "explanation"`.
If classified as a product bug, set `"bug_type": "product"`.
If unclear, set `"bug_type": "unclear"`.

### 9a. Assess Dev-Scripts Environment Feasibility (skip if test bug)

**If `bug_type` was set to `"test"` in step 9, skip this section entirely.**

Check whether the bug's reproduction conditions can be met in a dev-scripts VM environment. Dev-scripts deploys libvirt VMs on EC2 — it has inherent limitations compared to real bare metal.

**Conditions that dev-scripts CANNOT provide:**

| Condition | Why it fails | Signal keywords |
|-----------|-------------|-----------------|
| FQDN hostnames | Dev-scripts VMs use short names like `ostest-master-0` — no dots | "FQDN", "hostname contains dots", "fully qualified domain name" |
| Real BMC hardware | Dev-scripts uses virtual redfish (sushy-tools), not real IPMI/Redfish BMCs | "physical BMC", "real redfish", "iDRAC", "iLO", "bare metal hardware" |
| Real network partition | VMs share the same hypervisor network — can't do true link-layer isolation | "network partition", "physical link failure", "cable pull" |
| More than 3 VMs (resource-constrained) | EC2 has limited RAM/CPU for VMs | "5-node cluster", "large cluster", "scale" |
| Real storage hardware | No physical disks, only virtual | "physical disk failure", "SSD", "NVMe" |
| Specific NIC hardware | VMs use virtio NICs | "SR-IOV physical function", "specific NIC model", "DPDK on hardware" |

**Conditions that dev-scripts CAN provide:**

- Virtual redfish BMC (sushy-tools) — sufficient for most fencing bugs
- Arbiter and fencing topologies with correct node counts
- Day-0 and day-1 manifest injection
- Node reboot, shutdown, pcs commands, etcd operations
- Any `oc` command, backup/restore scripts, operator interactions

If the bug requires conditions dev-scripts cannot provide, set `"environment_feasible": false` and `"environment_blockers": ["list of what's missing and why"]`.
Otherwise set `"environment_feasible": true`.

### 10. Extract Reproduction Steps

Parse the description and comments for concrete reproduction steps. Look for:

- Numbered steps ("1. Deploy...", "2. Run...", "3. Observe...")
- Sections titled "Reproduction", "Steps to Reproduce", "How to Reproduce", "Reproduction Scenarios"
- Specific commands (bash code blocks, `oc` commands, `pcs` commands, scripts)
- Pre-conditions ("Start with a healthy TNF cluster", "Deploy with FQDN hostnames")
- Wait/observe instructions ("Wait for etcd to stabilize", "Observe the logs")

Structure the steps as an ordered list in `repro_steps`. Include:
- Pre-conditions needed before starting
- Each concrete action (command or manual step)
- What to observe/check after each action
- Expected outcome (what the bug looks like)

### 11. Determine Bug Condition and Detection (skip if test bug)

**If `bug_type` was set to `"test"` in step 9, skip this section entirely.**

Based on all gathered data, define:
- **bug_condition**: What the bug looks like when reproduced (e.g., "start logs 'is not in the member list yet' in a loop", "MCP DEGRADED with bootstrap MC mismatch", "pcs status shows node UNCLEAN")
- **detection_commands**: Specific commands to check if the bug is present
- **success_criteria**: What a healthy cluster looks like (for comparison)

### 12. Write Output

Write `{WORKDIR}/bug-analysis.json`:

```json
{
  "bug_id": "OCPBUGS-XXXXX",
  "summary": "...",
  "status": "...",
  "priority": "...",
  "components": ["machine-config-operator", "node-tuning-operator"],
  "labels": ["day-0", "arbiter"],
  "topology": "arbiter|fencing|null",
  "topology_confidence": "high|medium|low",
  "topology_signals": ["list of matching keywords/components"],
  "ocp_version": "4.20.14|4.20|null",
  "ocp_version_exact": true|false,
  "release_image": "quay.io/openshift-release-dev/ocp-release:4.20.14-x86_64|null",
  "bug_categories": ["mco", "nto"],
  "manifests": [
    {"kind": "PerformanceProfile", "name": "masters-performanceprofile", "file": "98-masters-performanceprofile.yaml"}
  ],
  "manifest_phase": "day-0|day-1|unknown|null",
  "config_hints": {
    "ENABLE_WORKLOAD_PARTITIONING": "true",
    "MASTER_VCPU": "8",
    "IP_STACK": "v4",
    "BMC_DRIVER": "redfish"
  },
  "bug_condition": "Brief description of what the bug looks like when reproduced",
  "detection_commands": [
    "oc get mcp master -o jsonpath='{.status.conditions[?(@.type==\"Degraded\")].message}'"
  ],
  "success_criteria": "All nodes Ready, all MCPs updated, no degraded operators",
  "repro_timing": "during-install|post-install|both",
  "repro_steps": [
    {"step": 1, "action": "description of what to do", "command": "exact command if applicable", "check": "what to verify after"},
    {"step": 2, "action": "...", "command": "...", "check": "..."}
  ],
  "repro_preconditions": ["healthy TNF cluster", "FQDN hostnames", "etc."],
  "repro_context": "Key reproduction details extracted from comments — configs, results, timelines, prior attempts",
  "logs_to_collect": ["bootkube-journal.log", "nto-controller.log", "bootstrapconfigdiff"],
  "install_method": "ipi|agent|kcli",
  "bug_type": "product|test|unclear",
  "test_bug_reason": "only present when bug_type is 'test' — why this is a test issue, not a product issue",
  "environment_feasible": true|false,  // only present for product/unclear bugs (test bugs skip this)
  "environment_blockers": ["only present when environment_feasible is false — conditions dev-scripts cannot provide"]
}
```

Also write manifests (if any) to `{WORKDIR}/manifests/` directory.
