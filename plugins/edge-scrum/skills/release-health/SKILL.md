---
name: release-health
description: Use when analyzing the health of an OCP release cycle — evaluates Features, Initiatives, Epics, and Tasks from Jira to assess progress, identify risks, surface refinement gaps, and recommend actions to keep the team on track toward branch cut
allowed-tools: Agent, AskUserQuestion, Write, Read, Glob, Bash
user-invocable: true
---

# Release Health Analysis

You are orchestrating a release health analysis for the OCPEDGE team. Delegate all Jira data-fetching and analysis to sub-agents — the main context is for coordination and report writing only.

> **Before proceeding**: Read `plugins/edge-scrum/references/Edge-Scrum-Laws.md` to find which law files apply to release health orchestration. For this skill, load: `laws/00-team-roster.md`, `laws/01-jira-projects.md`, `laws/05-jira-features.md`, `laws/06-jira-fields.md`, `laws/09-sprint-policies.md`, `laws/14-agent-conventions.md`. The configuration below is derived from the Laws — when in doubt, defer to the law files.

## Configuration

```yaml
# Scrum Board (skill-specific; not in Laws)
board_id: "11479"
board_name: "OpenShift Edge Scrum"
sprint_prefix: ["OCPEDGE Sprint", "OpenShift Edge Sprint"]

# Custom Field IDs (Red Hat Jira instance-specific)
fields:
  story_points:  customfield_10028   # Numeric; Stories/Tasks/Spikes
  epic_link:     customfield_10014   # Story → Epic relationship
  parent_link:   customfield_10018   # Epic → Feature/Initiative relationship
  qa_contact:    customfield_10470   # User picker; QA owner
  flagged:       customfield_10021   # Array; non-empty = impediment
  docs_approver: customfield_10473   # User picker; Doc Contact
  sme:           customfield_10475   # User picker; Subject Matter Expert
```

## Execution Model

All Jira data-fetching and analysis runs in sub-agents defined in `plugins/edge-scrum/agents/`. The main context:

1. Reads Edge Scrum Laws (Step 0)
2. Collects user inputs (Step 1)
3. For each phase: reads the agent definition file, substitutes `{VARIABLE}` placeholders, spawns the agent
4. Reads compact file outputs between phases for guard checks
5. Writes the final report (Step 9)

**Rules:**

- Spawn all agents in a phase in **one message** with multiple `Agent` calls (concurrent execution)
- Agents write output to `$WORKDIR` via the `Write` tool; main context reads those files with `Read`
- Substitute all `{VARIABLE}` placeholders in agent definition content before spawning
- Never embed raw Jira response data in the main context

## User Arguments

The user may provide arguments: `$ARGUMENTS`

- Version number (e.g., `4.19`, `5.0`) → release version
- Sprint range (e.g., `281-285`) → first through last sprint
- Branch cut (e.g., `bc:285` or `branch-cut 285`) → last sprint before feature freeze
- No arguments → ask for all inputs

---

## Workflow

### Step 0: Load Edge Scrum Laws and Roster (main context)

Read both files and hold in working memory:

1. Load these law files from `plugins/edge-scrum/references/laws/`:
   - `00-team-roster.md` — team capacity and `.roster.json` structure
   - `01-jira-projects.md` — **Jira projects and OCPBUGS components** — authoritative project keys and component names
   - `05-jira-features.md` — Feature/Initiative conventions and sizing
   - `06-jira-fields.md` — custom field IDs
   - `09-sprint-policies.md` — sprint capacity rules
   - `14-agent-conventions.md` — agent orchestration conventions

2. `plugins/edge-scrum/.roster.json` — extract:
   - **Team roster** — `username`, `display_name`, and `sp_target` per member
   - **Roster size** — count of members (used for capacity calculations)
   - If the file does not exist, stop and instruct the user to copy `.roster.json.example` to `.roster.json` and populate it.

The Laws are authoritative. Where this skill and the Laws conflict, the Laws win.

---

### Step 1: Gather Release Parameters (main context)

Parse arguments. Use `AskUserQuestion` for any missing values:

1. **Release version** — e.g., `4.19`, `5.0`
2. **Sprint range** — first sprint number through last
3. **Branch cut sprint** — which sprint is the last dev sprint

Compute and confirm:

- `FIRST` = first sprint number
- `LAST` = last sprint number (branch cut)
- `TOTAL_SPRINTS` = LAST − FIRST + 1
- `REFINEMENT_SPRINT_NUM` = FIRST (dedicated to spike creation, no delivery expected)
- `TOTAL_DEV_SPRINTS` = TOTAL_SPRINTS − 1
- `TODAY` = today's date (`YYYY-MM-DD`)

Create the work directory:

```bash
WORKDIR=/tmp/release-health-$(date +%Y%m%d) && mkdir -p $WORKDIR && echo $WORKDIR
```

Record `WORKDIR` — substitute it into all agent prompts.

---

### Phase 2: Sprint + Feature Collection

> **Spawn both agents in a single message (concurrent).**

Read `plugins/edge-scrum/agents/release-health-sprint-mapper.md` and `plugins/edge-scrum/agents/release-health-feature-fetcher.md`. Substitute all `{VARIABLE}` placeholders with the computed values, then spawn:

- **Agent 2a** — prompt: sprint-mapper content with `{WORKDIR}`, `{FIRST_SPRINT}`, `{LAST_SPRINT}`, `{TOTAL_DEV_SPRINTS}` substituted
- **Agent 2b** — prompt: feature-fetcher content with `{WORKDIR}`, `{VERSION}` substituted

**After both complete**, read and check:

- `{WORKDIR}/sprints.json` — if `"error"` key is present or `sprint_map` is empty, warn the user and stop
- `{WORKDIR}/features.json` — if `feature_keys` is empty, warn the user about scope and stop

---

### Phase 3: Epic + Spike Collection

> **Spawn both agents in a single message (concurrent).**

Read `plugins/edge-scrum/agents/release-health-epic-fetcher.md` and `plugins/edge-scrum/agents/release-health-spike-finder.md`. Substitute `{WORKDIR}`, then spawn:

- **Agent 3a** — prompt: epic-fetcher content with `{WORKDIR}` substituted
- **Agent 3b** — prompt: spike-finder content with `{WORKDIR}` substituted

**After both complete**, read `{WORKDIR}/epics.json` and verify: `epic_keys` is a non-empty array, `feature_to_epics` is an object, and `epics` is an array. If any check fails, warn the user with a descriptive error and stop.

---

### Phase 4: Full Analysis

> **Spawn one agent.**

Read `plugins/edge-scrum/agents/release-health-analysis.md`. Substitute `{WORKDIR}`, `{VERSION}`, `{TODAY}`, `{REFINEMENT_SPRINT_NUM}`, then spawn:

- **Agent 4** — prompt: analysis content with all placeholders substituted

This agent reads all four data files, detects misplaced spikes, fetches child issues, builds the hierarchy, assesses all risks, and writes `{WORKDIR}/analysis.md`.

---

### Step 9: Generate Report (main context)

1. Read `{WORKDIR}/analysis.md`
2. Parse the `===ANALYSIS_META===` block for report header values
3. Assemble the complete report:

```markdown
# Release Health: OCP {VERSION}

**Analysis Date**: {TODAY}
**Release Window**: Sprint {FIRST} – Sprint {LAST} | Branch Cut: Sprint {LAST}
**Refinement Sprint**: Sprint {REFINEMENT_SPRINT_NUM} ({state}) — {features_with_spike}/{total_features} features have refinement spikes
**Current Sprint**: Sprint {current} ({completed_count}/{total_count} sprints complete, {remaining} remaining)
**Expected Dev Progress**: {expected_dev_completion_pct}% | **Actual Progress**: {actual_completion_pct}%
**Overall Health**: {health_emoji} {overall_health}

## Executive Summary

{Write 3–5 sentences using top_risks, overall_health, feature counts, and sprint_recommendation from ANALYSIS_META.
Cover: overall verdict, count on track vs. at risk, top 2–3 risks, one recommended priority this sprint.}

---

{Paste each ===SECTION:*=== block in order, removing the sentinel lines}
```

1. Write to `.reports/release_health_{VERSION}_{TODAY}.md` using the `Write` tool.
1. Clean up the work directory safely: `test -n "{WORKDIR}" && [[ "{WORKDIR}" == /tmp/release-health-* ]] && rm -rf -- "{WORKDIR}"`

---

## Edge Cases

- **No Features found**: Try fallback JQL (handled inside feature-fetcher agent); warn user to confirm scope; stop if still empty.
- **Feature with no Epics**: Flag as "Unplanned"; epic-fetcher returns empty list for that feature.
- **Epic with no Stories**: Flag as "Empty" in analysis.
- **Version format varies** (`4.19` vs `4.19.0`): Analysis agent tries both in fixVersion queries.
- **SME / Docs field errors**: Agents skip those fields and note "field not configured."
- **Sprint resolution failure**: sprint-mapper agent returns `"error"` in JSON; main context stops before Phase 3.
- **Multiple spikes per Feature**: spike-finder records all in `spike_keys[]`; analysis agent uses worst-case status.
- **OCPBUGS without Epics**: Grouped under "(Unlinked Bugs)" section.
- **Analysis during refinement sprint**: Analysis agent skips 7a entirely — no delivery progress expected.
- **Spike linked to child Epic**: Detected by analysis agent via `all_ref_sprint_spikes` cross-reference with `epics.json`.

---

## Important Notes

- **Read-only**: This skill does not modify any Jira data.
- **Agent definitions**: `plugins/edge-scrum/agents/release-health-*.md` — edit these to update agent behavior without touching orchestration logic.
- **Work directory**: `{WORKDIR}` persists across agents within a run. Rerunning on the same day overwrites prior files.
- **Laws files**: Agents read specific files from `plugins/edge-scrum/references/laws/` per the index at `plugins/edge-scrum/references/Edge-Scrum-Laws.md` — never hardcode roster, rules, or sizing in agent definitions.
