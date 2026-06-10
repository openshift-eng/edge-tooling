# Session Summary: From Eval Framework to Diagnostic Skill

## Key Findings

### 1. The eval framework is not for feature testing

**The openshift/agentic-skills eval framework is not for feature testing.** It does not validate that software works correctly — that TNF shutdown recovers, that etcd rejoins after fencing, that standby persists across reboots. For that, you need automated E2E tests running against real clusters in CI (e.g., `origin/test/extended/two_node/`).

### 2. The eval framework IS the foundation for trustworthy AI skills

What it does is verify that an **AI agent reasons correctly** — given this cluster state, does the agent classify the problem correctly, recommend the right procedure, and avoid dangerous actions? Without evals, a skill is "trust the AI." With evals, it's "verify the AI." This is the primary distinction.

Everything we built in this session — the SKILL.md format, the diagnostic reasoning pattern, the severity classification, the knowledge base structure — comes from the agentic-skills repo. We could not have done this work without it.

### 3. Two fundamentally different skill types exist

| Type | What it does | Example | Evals? |
|------|-------------|---------|--------|
| **Doc-lookup** | Retrieval — finds the right answer in documentation | kubernetes-docs, openshift-docs | Yes (12 YAML test cases each, enum-constrained) |
| **Diagnostic reasoning** | Analysis — applies decision rules, classifies severity, weighs multiple factors, produces structured recommendations | cluster-update-advisor, cluster-diagnostic | None yet |

The eval framework was built for doc-lookup and proven there. Extending it to diagnostic reasoning — verifying that an AI can correctly diagnose complex operational scenarios, not just retrieve facts — is the hard unsolved problem. That's exactly what our cluster-diagnostic skill needs to become production-grade.

## What We Explored

### openshift/agentic-skills eval framework

Explored the full eval stack: pytest runner, YAML test cases with enum-constrained JSON schemas, containerized agent testing via `/v1/agent/run`, multi-provider support (Claude, Gemini, OpenAI).

The existing doc-lookup test cases are simple ("which command lists pods?"). No test cases exist yet for diagnostic reasoning skills. The `cluster-update-advisor` — the most sophisticated skill in the repo — has zero evals.

### TNF graceful shutdown test document

Reviewed `TNF_GRACEFUL_SHUTDOWN_TEST.md` — 1,417 lines documenting 7 test runs on HPE ProLiant e920t bare metal (OCP 4.22.0-rc.3). These manual test runs are real feature testing:

| Run | Scenario | Key Finding |
|-----|----------|-------------|
| 1 | Pacemaker standby | Kills API immediately; persists across reboots |
| 2 | Documented procedure | Sequential shutdown triggers fencing race |
| 3 | Simultaneous Redfish GracefulShutdown | Correct procedure — clean recovery ~12 min |
| 4 | Single-node GracefulShutdown | Corosync clean departure, no fencing |
| 5 | Network partition (iptables) | Double fence, force-new-cluster, ~17 min |
| 6 | Single-node ForceOff | HPE iLO ForceOff is non-deterministic |
| 7 | VIP holder ForceOff | VIP migration works; BMC can silently fail |

**Critical correction found:** `pcmk_delay_random` does not exist as a Pacemaker property (the random component is implicit between `pcmk_delay_base` and `pcmk_delay_max`).

### openshift-eng/edge-tooling plugin marketplace

Discovered the existing `two-node` plugin with 3 agentic skills built by the team (Luca, Hamza):

- **verify-rhel-bugfix** — SSHs to cluster, patches RPMs, runs tests, posts Jira reports
- **bug-reproducer** — deploys clusters, reproduces bugs via 5 sub-agents, collects logs
- **create-rhel-stories** — creates and links Jira tickets automatically

These are operational automation with real side effects — not passive knowledge bases.

## What We Built

### Reusable diagnostic skill template

`plugins/.templates/diagnostic-skill.md.template` (107 lines)

A generic pattern any topology (TNF, TNA, SNO) can instantiate:

1. Run a diagnostic script to gather cluster state via SSH
2. Read a reference file containing domain knowledge
3. Analyze output against the knowledge base
4. Report findings with severity classification (BLOCKER / WARNING / INFO)

### TNF cluster-diagnostic skill

Ported the test document findings into the `two-node` plugin as an agentic diagnostic skill:

| File | Lines | Purpose |
|------|-------|---------|
| `skills/cluster-diagnostic/SKILL.md` | 122 | 3-mode skill: diagnose, validate, recovery-guide |
| `references/cluster-knowledge-base.md` | 430+ | All 7 test run findings, procedures, failure modes, timelines, etcd recovery |
| `scripts/diagnose-cluster.sh` | 133 | Gathers pcs/etcd/corosync/BMC state via SSH |
| `README.md` | +25 | Skill documentation |

**Supports both access patterns:**

- Dev-scripts: SSH through EC2 hypervisor to VMs (auto-detected)
- Bare metal: Direct SSH to nodes via `NODE_0`/`NODE_1` env vars

**Three modes:**

- `diagnose` — SSH to cluster, gather state, analyze, report findings
- `validate` — Check a proposed shutdown procedure against known failure modes
- `recovery-guide` — Step-by-step recovery for specific scenarios (standby, full-shutdown, network-partition, etc.)

**Diagnose only** — gathers state and recommends actions but never executes recovery or shutdown commands.

### What the skill is and is not

| What it is | What it is not |
|------------|----------------|
| Operational triage tool | Feature test suite |
| Knowledge transfer for the team | CI/CD validation |
| Guardrails against known bad procedures | Automated regression testing |
| Structured reasoning from 7 test runs | Replacement for E2E tests |

## Gaps That Remain

### 1. No evals for the diagnostic skill

Our cluster-diagnostic skill has no test cases. There's no automated way to verify that Claude correctly classifies `pcs status` showing standby as BLOCKER, or flags sequential shutdown as dangerous, or recommends `pcs node unstandby --all`. Writing YAML eval test cases for diagnostic reasoning skills — extending the framework from doc-lookup to analysis — is the next step to make this trustworthy.

### 2. No automated E2E tests for shutdown/recovery

The 7 manual test runs on bare metal are feature testing. Turning those scenarios into automated E2E tests that run in CI (`origin/test/extended/two_node/`) is the work that none of this session addressed. The diagnostic skill helps Claude reason about shutdown/recovery issues after they happen — it does not verify that the software handles them correctly.

## Verification

All checks pass on the new plugin files:

- `./marketplace validate two-node` — passed
- `scripts/lint-skills.py` — no issues found
- `markdownlint` — 0 errors
- `shellcheck` — info-level only (matching existing script conventions)
