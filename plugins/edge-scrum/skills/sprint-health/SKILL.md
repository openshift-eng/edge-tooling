---
name: sprint-health
description: Use when analyzing sprint health — capacity at the start, risks mid-sprint, or retrospective input at the end
allowed-tools: Agent, AskUserQuestion, Write, Read, Glob, Bash
user-invocable: true
---

# Sprint Health Analysis

You are orchestrating a sprint health analysis for the OCPEDGE team. Delegate all Jira data-fetching and analysis to sub-agents — the main context is for coordination and report writing only.

> **Before proceeding**: Read `plugins/edge-scrum/references/Edge-Scrum-Laws.md` to identify which law files apply to Sprint Health, then read those files. Law files are the canonical reference for story pointing rules, workflow states, churn rules, and hygiene expectations. When in doubt, the Laws win.

## Configuration

```yaml
# Scrum Board
board_id: "11479"
board_name: "OpenShift Edge Scrum"

# Custom Field IDs
fields:
  story_points:  customfield_10028
  epic_link:     customfield_10014
  qa_contact:    customfield_10470
  flagged:       customfield_10021
```

## Execution Model

All Jira data-fetching and analysis runs in sub-agents defined in `plugins/edge-scrum/agents/`. The main context:

1. Reads Laws and Roster (Step 0)
2. Parses args and asks mode (Step 1)
3. For each phase: reads the agent definition file, substitutes `{VARIABLE}` placeholders, spawns the agent
4. Reads compact file outputs between phases for guard checks
5. Writes the final report (Step 4)

**Rules:**

- Agents write output to `$WORKDIR` via the `Write` tool; main context reads those files with `Read`
- Substitute all `{VARIABLE}` placeholders in agent definition content before spawning
- Never embed raw Jira response data in the main context

## User Arguments

The user may provide arguments: $ARGUMENTS

- Sprint number (e.g., `285`) → target that specific sprint
- No arguments → target the active sprint

---

## Workflow

### Step 0: Load Laws and Roster

Read these files and hold in working memory:

1. `plugins/edge-scrum/references/Edge-Scrum-Laws.md` — read the Sprint Health row in the Agent Task Index to identify required law files
2. Read each law file listed for Sprint Health:
   - `plugins/edge-scrum/references/laws/00-team-roster.md` — SP targets and roster rules
   - `plugins/edge-scrum/references/laws/06-jira-fields.md` — custom field IDs
   - `plugins/edge-scrum/references/laws/07-workflow-states.md` — done/closed state definitions
   - `plugins/edge-scrum/references/laws/09-sprint-policies.md` — capacity and churn rules
3. `plugins/edge-scrum/.roster.json` — extract `username`, `display_name`, `sp_target` per member

If `.roster.json` does not exist, stop: "Roster file not found. Copy `.roster.json.example` to `.roster.json` and populate it before running this skill."

---

### Step 1: Parse Arguments and Select Mode

Parse `$ARGUMENTS`:

- If a sprint number is present: `TARGET_SPRINT="<number>"`
- Otherwise: `TARGET_SPRINT="active"`

Use `AskUserQuestion` to ask:

> "Which sprint health mode would you like to run?
> - **capacity** — start-of-sprint load and commitment health
> - **mid-sprint** — burndown, blockers, and sprint goal risk
> - **retro** — delivery summary, churn, and goal analysis"

Set `MODE` to one of: `capacity`, `mid-sprint`, `retro`.

Set `TODAY` = today's date as `YYYY-MM-DD`.

Create the work directory:

```bash
WORKDIR=/tmp/sprint-health-$(echo "$TARGET_SPRINT")-$(date +%Y%m%d) && mkdir -p "$WORKDIR" && echo "$WORKDIR"
```

Record `WORKDIR`.

---

> **Phase 2 is sequential**: Run Phase 2a to completion before starting Phase 2b — Phase 2b requires `SPRINT_ID` from Phase 2a's output.

### Phase 2a: Sprint Metadata

Read `plugins/edge-scrum/agents/sprint-mapper.md`. Substitute all placeholders:

- `{WORKDIR}` → work directory path
- `{TARGET_SPRINT}` → the target sprint value (`"active"` or a sprint number)
- `{FIRST_SPRINT}` → (leave empty — release-health field)
- `{LAST_SPRINT}` → (leave empty — release-health field)
- `{TOTAL_DEV_SPRINTS}` → (leave empty — release-health field)

Spawn the agent with substituted content.

After completion, read `{WORKDIR}/sprints.json`. Verify:

- `target_sprint` is non-null. If null, stop: "Could not find sprint '{TARGET_SPRINT}'. Check the sprint number and try again."

Extract:

- `SPRINT_ID` = `target_sprint.id`
- `SPRINT_START` = `target_sprint.start`
- `SPRINT_NAME` = `target_sprint.name`
- `SPRINT_NUM` = sprint number extracted from `target_sprint.name` using the pattern `/(\d+)$/` (last sequence of digits in the name, e.g., `"OCPEDGE Sprint 285"` → `285`). If no digits are found, use `TARGET_SPRINT` as fallback.

---

### Phase 2b: Sprint Issues

Read `plugins/edge-scrum/agents/sprint-health-issues.md`. Substitute all placeholders:

- `{WORKDIR}` → work directory path
- `{SPRINT_ID}` → sprint ID integer
- `{SPRINT_START}` → sprint start date

Spawn the agent with substituted content.

After completion, read `{WORKDIR}/sprint_issues.json`. Verify:

- `issues` array is non-empty. If empty, warn: "Sprint {SPRINT_NAME} has no issues — analysis sections will be sparse." Proceed anyway.

---

### Phase 3: Mode-Specific Analysis

Based on `MODE`, read the corresponding agent file, substitute placeholders, and spawn.

**capacity:**

Read `plugins/edge-scrum/agents/sprint-health-capacity-analyzer.md`. Substitute:

- `{WORKDIR}` → work directory path
- `{TODAY}` → today's date
- `{SPRINT_START}` → sprint start date

**mid-sprint:**

Read `plugins/edge-scrum/agents/sprint-health-midpoint-analyzer.md`. Substitute:

- `{WORKDIR}` → work directory path
- `{TODAY}` → today's date

**retro:**

Read `plugins/edge-scrum/agents/sprint-health-retro-analyzer.md`. Substitute:

- `{WORKDIR}` → work directory path
- `{TODAY}` → today's date
- `{SPRINT_START}` → sprint start date
- `{SPRINT_ID}` → sprint ID integer

After the mode-specific agent completes, read `{WORKDIR}/analysis.md` to verify it was written successfully.

---

### Step 4: Generate Report

1. Read `{WORKDIR}/sprints.json` to get `target_sprint` values. Read `{WORKDIR}/sprint_issues.json` to get `total_sp` and `total_issues`.
2. Compute `total_roster_sp` = sum of all `sp_target` values in `.roster.json`.
3. Assemble the final report:

   a. Write this header:

   ```markdown
   # Sprint Health: {SPRINT_NAME}

   **Date**: {TODAY}
   **Mode**: {MODE}
   **Sprint**: {target_sprint.start} – {target_sprint.end} | {target_sprint.days_elapsed} of {target_sprint.total_days} days elapsed
   **Goal**: {target_sprint.goal or "Not set"}
   **Team**: {roster_size} members | {total_roster_sp} SP capacity
   **Committed**: {total_sp} SP across {total_issues} issues

   ---
   ```

   b. Append the body from `{WORKDIR}/analysis.md`, replacing each line that matches the pattern `===SECTION:<name>===` (any section name) with a blank line.

4. Write the assembled report to:

   ```
   .reports/sprint_health_{SPRINT_NUM}_{MODE}_{TODAY}.md
   ```

5. Clean up:

   ```bash
   test -n "$WORKDIR" && [[ "$WORKDIR" == /tmp/sprint-health-* ]] && rm -rf -- "$WORKDIR"
   ```

---

## Edge Cases

- **No active sprint**: sprint-mapper selects the highest-numbered closed sprint; report proceeds normally.
- **Empty sprint**: warn user, proceed — analysis sections will indicate no issues.
- **No sprint goal**: all three modes handle null goal gracefully — retro and mid-sprint note "No sprint goal set."
- **Unrostered assignees**: capacity analyzer lists them under "Unrostered assignees."
- **OCPBUGS issues**: included in queries; always contribute 0 SP per Laws.

---

## Important Notes

- **Read-only**: This skill does not modify any Jira data.
- **Agent definitions**: `plugins/edge-scrum/agents/sprint-health-*.md` and `sprint-mapper.md`
- **Work directory**: cleaned up after each run; rerunning same day overwrites prior files.
- **Laws**: agents read their required law files from `plugins/edge-scrum/references/laws/` (per the index in `references/Edge-Scrum-Laws.md`) — never hardcode rules here.
