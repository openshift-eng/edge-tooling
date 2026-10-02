---
name: release-planning-analysis
description: Write the decisions and narrative for a release planning risk assessment — reads pre-computed check results and produces a short, decision-oriented recommendations file
allowed-tools: Read, Write
user-invocable: false
---

# release-planning: Analysis

## Purpose

Read the pre-computed planning risk checks from `checks.json` and write the narrative parts of the report: a one-line headline, the decisions table, scope and process-gap commentary, and an appendix of detailed recommendations. All arithmetic (capacity, timeline, cut line, hidden scope, composite scoring, process-gap counts) has already been computed by `run-checks.py`. This agent interprets and advises; it never computes.

## When to Spawn

The parent release-planning skill spawns this agent during Phase 5b, after `run-checks.py` has produced `{WORKDIR}/checks.json`.

## Parameters

Substituted by the parent before spawning:

| Placeholder | Description |
|---|---|
| `{WORKDIR}` | Work directory path |
| `{VERSION}` | OCP release version (e.g., `5.1`) |

## Instructions

### Step 1: Read Data

Read `{WORKDIR}/checks.json`. Relevant keys:

- `meta` — headline figures: `total_remaining_sp`, `total_capacity_sp`, `gap_sp`, hidden-scope range, active/dormant counts, risk counts, `overall_risk`
- `method` — the formula, inputs and result behind every headline figure. Every number you write must be traceable to this block or to a table below
- `cut_line` — active features in PM rank order with cumulative SP and `fits` (`fits` / `partial` / `over`)
- `capacity` — per person: `assigned_sp`, `unpointed_assigned`, `remaining_capacity`, `status` (`OK` / `OVER` / `NOT_IN_ROSTER`)
- `timeline` — per feature: `remaining_sp`, `unpointed_open`, `velocity_per_sprint`, `sprints_needed`, `risk`
- `assignment` — `spof` (single-owner features) and `unassigned`
- `bug_load` — unassigned Blocker/Critical bugs
- `sizing` — informational T-shirt mismatches (not part of composite risk)
- `composite` — per-feature composite risk and its signals
- `scope_confirmation` — dormant features (status New, no evidence of work) with the reason each was classified dormant
- `hidden_scope` — unpointed story counts and the SP estimate range with its basis
- `process_gaps` — computed systemic findings: `roster_drift`, `features_without_sme`, unpointed counts, `excluded_epics`
- `feature_names` — key → summary, for naming features

### Step 2: Write Recommendations

Write `{WORKDIR}/recommendations.json`:

```json
{
  "headline": "<one sentence, ≤ 20 words: what the reader should take away — usually whether the lever is scope, people, or ownership>",
  "decisions": [
    {
      "decision": "<what to decide, naming the feature as Name (KEY) and the concrete action>",
      "frees": "<what it frees: 'up to 179 SP', 'ownership', 'visibility'>",
      "owner": "<role, not a person: PM, Eng manager, Team lead, SME, Assignees>",
      "by": "<a sprint event: 'S295 planning', 'before pencils down', 'this week'>",
      "why": "<optional one-clause derivation using figures from checks.json; omit if the assembler can derive it from the timeline>"
    }
  ],
  "scope_decisions": [
    "<1–3 bullets on the dormant list: which ones look like real 5.x candidates, which are clearly next-release, what confirming them requires>"
  ],
  "process_gaps": [
    "<up to 4 bullets that add interpretation to the computed gaps — do not restate roster drift, unpointed counts or SME counts; the assembler renders those from process_gaps>"
  ],
  "per_feature": [
    "<appendix: one line per HIGH composite feature — what specifically to do>"
  ],
  "per_person": [
    "<appendix: one line per person over target or sole owner — what to move or pair>"
  ]
}
```

### Writing Rules

- **Decisions, not observations.** Each `decisions` row must be something a named role can decide by a named sprint event. "Alice is overloaded" is an observation; "Move OCPSTRAT-3105 (TNA quorum tuning) from Alice to Bob before S295 planning" is a decision. At most **five** rows; the table is the report's core, not a list of everything.
- **Lead with the biggest lever.** Read `cut_line` first. If features fall below the line, the first decision is usually a scope decision, not a rebalancing one — 158 SP cannot be closed by moving work between people.
- **Name the feature.** Write `Name (KEY)` using `feature_names`, e.g. `Edge Tech Debt Backlog (OCPSTRAT-2788)`. Never a bare key without its name in a decision.
- **Bare keys only.** Write `OCPSTRAT-2788`, never `[OCPSTRAT-2788](url)`. The assembler links keys; pre-linking creates broken nested links. The assembler warns on pre-built links, and under `--strict` (how the skill runs it) rejects the file.
- **Every number must be traceable.** Only use figures that appear in `method`, `meta` or a table in `checks.json`. Do not derive new totals, percentages or averages. If you want to say "roughly 160 SP", the report already shows `gap_sp`; reference it, do not recompute it.
- **Pointed vs unpointed stay separate.** Unpointed stories are hidden scope. Refer to them as a count or as the estimate range from `hidden_scope`; never add them to SP totals or invent points for them.
- **Dormant is a scope decision, not a failure.** Features in `scope_confirmation` are not "data-quality failures"; they are uncommitted scope. Recommend confirming or deferring them, not "creating stories" for them.
- **Roles in `owner`, names in `decision`.** The owner column is a role. People appear inside the decision text, by display name.
- **Never assume gender.** Use the person's name or they/them. Never he/she/his/her/him about a teammate. The assembler warns on a gendered pronoun, and under `--strict` (how the skill runs it) rejects the file.
- **Non-roster people.** If `process_gaps.roster_drift` is non-empty, one decision or process-gap line should ask for the roster to be reconciled, because every capacity figure depends on it.
- **Do not repeat the tables.** The report shows the cut line, the people table, the dormant list and the method table. Your text interprets; it does not restate.
- **Phrase pairing by feature, not by person.** Write "pair X with Y on Feature (KEY)", never "add a contributor to X". A person is not a bucket work is added to; the pairing is on a piece of work.

## Important Notes

- This agent does NOT compute any numbers — all arithmetic is in `checks.json`
- This agent does NOT build hierarchies or read Jira data files
- This agent does NOT write markdown sections — only the JSON file above
- Output is a single JSON file with six keys: `headline` (a one-sentence string) and `decisions` (a list) are required; `scope_decisions`, `process_gaps`, `per_feature` and `per_person` are lists that may be empty
- `assemble-report.py` validates the file (pre-built links, gendered pronouns, unknown keys, more than five decisions): by default it warns, and under `--strict` (how the skill runs it) it fails. Rewrite the file if it warns.
