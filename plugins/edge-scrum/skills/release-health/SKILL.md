---
name: release-health
description: Use when analyzing the health of an OCP release cycle — evaluates Features, Initiatives, Epics, and Tasks from Jira to assess progress, identify risks, surface refinement gaps, and recommend actions to keep the team on track toward branch cut
allowed-tools: Agent, AskUserQuestion, Write, Read, Glob, Bash, mcp__plugin_mcp-atlassian_mcp-atlassian__jira_get_sprints_from_board, mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
user-invocable: true
---

# Release Health Analysis

You are orchestrating a release health analysis for the OCPEDGE team. Data fetching runs inline using MCP tools and transform scripts. Analysis is delegated to a sub-agent.

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

1. **Steps 0–1**: Load laws/roster, gather release parameters (main context)
2. **Phase 2**: Fetch sprints + features inline using MCP tools → transform scripts (main context)
3. **Phase 3**: Fetch epics + spikes inline using MCP tools → transform scripts (main context)
4. **Phase 4**: Delegate full analysis to sub-agent (LLM-driven risk assessment)
5. **Step 9**: Assemble and write the final report (main context)

**Rules:**

- Data fetching uses MCP tools directly in the main context
- MCP responses are large and get persisted to files automatically — note those file paths
- Transform scripts (`plugins/edge-scrum/bin/`) convert raw MCP data to structured JSON
- Use `check-page.py` to extract pagination info from persisted files
- The analysis sub-agent only needs `Read`, `Write`, and `jira_search` — it consumes the structured JSON
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

### Phase 2: Sprint + Feature Collection (inline)

Fetch sprints and features in parallel using MCP tools, then transform with scripts.

#### 2a — Fetch Sprints

Call `jira_get_sprints_from_board` for board_id `"11479"` three times:

- `state="active"`
- `state="closed"` — paginate using `page_token` (see pagination protocol below); use `limit=50`
- `state="future"`

After all pages are fetched, note all persisted file paths and run:

```bash
python3 plugins/edge-scrum/bin/transform-sprints.py \
  --input <all_persisted_file_paths> \
  --output {WORKDIR}/sprints.json \
  --today {TODAY} \
  --first-sprint {FIRST} \
  --last-sprint {LAST} \
  --total-dev-sprints {TOTAL_DEV_SPRINTS}
```

#### 2b — Fetch Features

Call `jira_search` with:

- **JQL:** `project = OCPSTRAT AND labels = "ocpedge-plan" AND labels = "{VERSION}-candidate" ORDER BY priority ASC`
- **Fields:** `key, summary, status, issuetype, priority, assignee, fixVersions, labels, description, issuelinks, customfield_10795, customfield_10470, customfield_10473, customfield_10475`
- **limit:** `50`

Paginate using `page_token`. If zero results, use fallback JQL (set `fallback_used`):

```
project = OCPSTRAT AND labels = "ocpedge-plan" AND status not in (Done, Closed) ORDER BY priority ASC
```

After all pages fetched, run:

```bash
python3 plugins/edge-scrum/bin/transform-features.py \
  --input <all_persisted_file_paths> \
  --output {WORKDIR}/features.json
```

Append `--fallback-used` if fallback JQL was used.

#### 2c — Verify

Read and check:

- `{WORKDIR}/sprints.json` — if `"error"` key is present or `sprint_map` is empty, warn the user and stop
- `{WORKDIR}/features.json` — if `feature_keys` is empty, warn the user about scope and stop

---

### Phase 3: Epic + Spike Collection (inline)

#### 3a — Fetch Epics

Read `{WORKDIR}/features.json`. Extract `feature_keys_csv`.

If `feature_keys` has more than 50 entries, split into batches of 50. For each batch, call `jira_search`:

- **JQL:** `project in (OCPEDGE, USHIFT) AND "Parent Link" in ({feature_keys_batch_csv}) ORDER BY priority ASC`
- **Fields:** `key, summary, status, assignee, labels, description, customfield_10028, customfield_10018, customfield_10470, customfield_10473, customfield_10475`
- **limit:** `50`

Paginate using `page_token`. After all pages fetched, run:

```bash
python3 plugins/edge-scrum/bin/transform-epics.py \
  --input <all_persisted_file_paths> \
  --output {WORKDIR}/epics.json
```

#### 3b — Fetch Spikes

Read `{WORKDIR}/sprints.json`. Extract `refinement_sprint_id`.

Call `jira_search`:

- **JQL:** `project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = {refinement_sprint_id}`
- **Fields:** `key, summary, status, assignee, issuelinks`
- **limit:** `50`

Paginate using `page_token`. After all pages fetched, run:

```bash
python3 plugins/edge-scrum/bin/transform-spikes.py \
  --input <all_persisted_file_paths> \
  --features-file {WORKDIR}/features.json \
  --sprints-file {WORKDIR}/sprints.json \
  --output {WORKDIR}/spikes.json
```

#### 3c — Verify

Read `{WORKDIR}/epics.json` and verify: `epic_keys` is a non-empty array, `feature_to_epics` is an object, and `epics` is an array. If any check fails, warn the user with a descriptive error and stop.

---

### Pagination Protocol

This Jira instance uses `page_token` pagination, NOT `start_at`. Follow this protocol for all paginated MCP calls:

1. Make the first call without `page_token`
2. The response may be persisted to a file. Note the file path.
3. Run `check-page.py` to get pagination info:
   ```bash
   python3 plugins/edge-scrum/bin/check-page.py <persisted_file_path>
   ```
   Output: `{"issues_count": N, "has_more": bool, "next_page_token": "..."}`
4. If `has_more` is `true`: make the next call with `page_token` set to the `next_page_token` value. Repeat from step 2.
5. If `has_more` is `false`: pagination is complete.

For small responses that fit in context (not persisted), write them to `{WORKDIR}/raw_<type>_<page>.json` using `Write`, then run `check-page.py` on that file.

For `jira_get_sprints_from_board`: closed sprints may require multiple pages. Active and future typically fit in one page each.

---

### Phase 4: Full Analysis (sub-agent)

Read `plugins/edge-scrum/skills/release-health-analysis/SKILL.md`. Substitute `{WORKDIR}`, `{VERSION}`, `{TODAY}`, `{REFINEMENT_SPRINT_NUM}`, then spawn as a sub-agent:

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

- **No Features found**: Try fallback JQL (handled in Phase 2b); warn user to confirm scope; stop if still empty.
- **Feature with no Epics**: Flag as "Unplanned"; epic fetch returns empty list for that feature.
- **Epic with no Stories**: Flag as "Empty" in analysis.
- **Version format varies** (`4.19` vs `4.19.0`): Analysis agent tries both in fixVersion queries.
- **SME / Docs field errors**: Transform scripts handle gracefully with fallback values.
- **Sprint resolution failure**: transform-sprints.py sets `"error"` in JSON; main context stops before Phase 3.
- **Multiple spikes per Feature**: transform-spikes.py records all in `spike_keys[]`; analysis agent uses worst-case status.
- **OCPBUGS without Epics**: Grouped under "(Unlinked Bugs)" section by analysis agent.
- **Analysis during refinement sprint**: Analysis agent skips 7a entirely — no delivery progress expected.
- **Spike linked to child Epic**: Detected by analysis agent via `all_ref_sprint_spikes` cross-reference with `epics.json`.

---

## Important Notes

- **Read-only**: This skill does not modify any Jira data.
- **Transform scripts**: `plugins/edge-scrum/bin/` — reusable data transformation (no LLM needed)
- **Analysis sub-agent**: `plugins/edge-scrum/skills/release-health-analysis/SKILL.md` — LLM-driven risk assessment and report generation
- **Work directory**: `{WORKDIR}` persists across phases within a run. Rerunning on the same day overwrites prior files.
- **Laws files**: The analysis sub-agent reads specific files from `plugins/edge-scrum/references/laws/` per the index at `plugins/edge-scrum/references/Edge-Scrum-Laws.md` — never hardcode roster, rules, or sizing in skill definitions.
