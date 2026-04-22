---
name: two-node:create-rhel-stories
argument-hint: [--dry-run] <RHEL ticket keys or JQL>
description: Create OCPEDGE stories for TNF RHEL verification tickets, link them, and set components
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep, Agent, AskUserQuestion, mcp__mcp-atlassian__jira_search, mcp__mcp-atlassian__jira_get_issue, mcp__mcp-atlassian__jira_create_issue, mcp__mcp-atlassian__jira_create_issue_link, mcp__mcp-atlassian__jira_search_fields
---

# two-node:create-rhel-stories

## Synopsis

```bash
/two-node:create-rhel-stories [--dry-run] [RHEL-XXXXX ...] [jql:<JQL query>]
```

## Description

Create OCPEDGE stories for groups of related TNF RHEL verification tickets that don't already have a corresponding story on the OCPEDGE backlog. Link the RHEL tickets to the new story and add the required components.

## Arguments

- `$ARGUMENTS` (optional): One of the following input formats, optionally preceded by `--dry-run`
  - **Specific ticket keys** (e.g., `RHEL-12345 RHEL-12346 RHEL-12347`): Process these tickets directly
  - **JQL query** (e.g., `jql:project = RHEL AND component = "resource-agents"`): Search for tickets matching the query
  - **No arguments** (or only `--dry-run`): Auto-discover untested TNF resource-agents tickets using the default JQL
  - `--dry-run` (optional): Preview the plan without creating or modifying anything in Jira

## Prerequisites

This command requires the `mcp-atlassian` MCP server for Jira access. This plugin includes an `.mcp.json` that configures it automatically using a pinned container image. The user needs:
- **`podman`** installed
- **`JIRA_USERNAME`** environment variable set with their Red Hat email
- **`JIRA_API_TOKEN`** environment variable set with a [Jira API token](https://id.atlassian.com/manage-profile/security/api-tokens)

If the `mcp__mcp-atlassian__jira_search` tool is not available when the command runs, stop and show these setup instructions instead of proceeding.

## Scripts Directory

All scripts are run relative to the plugin directory:

```bash
SCRIPTS_DIR=${PLUGIN_DIR}/scripts
```

## Helper Script

This command uses a helper script for deterministic operations. The script is at:
`${SCRIPTS_DIR}/ocpedge_rhel_helper.py`

Available subcommands:
- `parse-args <arguments>` — Parse input into `{mode, jql?, tickets?}`
- `group-tickets <tickets_json>` — Group tickets by base summary, clone links, OCPBUGS refs
- `generate-description <ticket_keys_json>` — Generate story description markdown
- `generate-summary <base_summary>` — Generate story summary with prefix
- `check-links <tickets_json>` — Check which tickets already have OCPEDGE links
- `find-missing-clones <tickets_json>` — Find clone-linked tickets not in the current set (sibling clones to fetch)

All subcommands accept/return JSON. Use Bash to call them.
Pass `-` as the argument to read JSON from stdin (avoids ARG_MAX limits with large payloads):
```bash
echo '<large_json>' | python3 "${SCRIPTS_DIR}/ocpedge_rhel_helper.py" group-tickets -
```

## Auto-Discover Default JQL

When no arguments are provided (or only `--dry-run`), automatically search for TNF resource-agents tickets that need testing using this default JQL:
```
project = RHEL AND summary ~ "\\[TNF\\]" AND component = "resource-agents" AND ("Preliminary Testing" = Requested AND "Test Coverage" is EMPTY) AND (fixVersion in unreleasedVersions() OR fixVersion is EMPTY) ORDER BY created DESC
```
This aligns with the OCPEDGE RHEL Verification board (board 11551) filter, which shows all `[TNF]` resource-agents RHEL tickets, but further narrows to only untested ones.

## Dry-Run Mode

If `$ARGUMENTS` contains `--dry-run`, the command runs Steps 0–5 (read-only operations and plan presentation) but **does NOT execute Steps 6–7** (no issues created, no links added, no subtasks created). Instead, after presenting the plan, it shows what *would* be created and exits. This is useful for testing the grouping logic and verifying the plan against real Jira data without modifying anything.

## Implementation Steps

### Step 0: Parse Arguments

Run the helper to parse the input:

```bash
python3 "${SCRIPTS_DIR}/ocpedge_rhel_helper.py" parse-args $ARGUMENTS
```

This returns JSON with `mode` ("jql", "tickets", or "interactive"), `dry_run` (boolean), and the parsed values.
- If `mode` is "interactive", use auto-discover mode: search with the default JQL for untested resource-agents tickets (see Auto-Discover Default JQL above).
- If `mode` is "jql", use the `jql` field in Step 1.
- If `mode` is "tickets", use the `tickets` list in Step 2.
- If `dry_run` is true, stop after Step 5 (the plan) — do not create or modify anything in Jira.

### Step 1: Collect RHEL Tickets

**If specific ticket keys given**: Use those directly.

**If JQL query given** (user-provided or auto-discover default): Search for tickets:
```
mcp__mcp-atlassian__jira_search(
  jql='<JQL query>',
  fields="summary,status,fixVersions,issuelinks,components,priority,description,customfield_10879,customfield_10638",
  limit=50
)
```

For auto-discover mode (no arguments), use:
```
project = RHEL AND summary ~ "\\[TNF\\]" AND component = "resource-agents" AND ("Preliminary Testing" = Requested AND "Test Coverage" is EMPTY) AND (fixVersion in unreleasedVersions() OR fixVersion is EMPTY) ORDER BY created DESC
```

### Step 1b: Expand Clone Siblings

The JQL filter may only return a subset of clones for a given issue (e.g. only the untested ones). The OCPEDGE story should link **all** clones, not just the ones matching the filter.

Run the helper to find missing clones:
```bash
python3 "${SCRIPTS_DIR}/ocpedge_rhel_helper.py" find-missing-clones '<tickets_json>'
```

This returns a JSON array of ticket keys that are referenced via clone links but weren't in the search results. For each missing key, fetch it with `jira_get_issue` and add it to the ticket set. Then run `find-missing-clones` again on the expanded set — repeat until no new clones are found (this walks the full clone tree via the parent).

Typically one round is enough (the search results link to their parent, and fetching the parent reveals all siblings). **Limit to 5 iterations maximum** to prevent infinite loops in case of circular clone references.

### Step 2: Fetch RHEL Ticket Details

**If tickets were already returned with full details from Step 1** (JQL search includes the required fields), skip re-fetching and use those results directly.

**Otherwise**, for each RHEL ticket, fetch full details:
```
mcp__mcp-atlassian__jira_get_issue(
  issue_key="RHEL-XXXXX",
  fields="summary,status,fixVersions,issuelinks,components,priority,description"
)
```

Fetch tickets in parallel where possible to minimize wait time.

Extract from each ticket:
- **Summary**: The ticket title
- **Fix Version**: e.g. `rhel-9.6.z`, `rhel-9.8`, `rhel-9.9`
- **Issue Links**: Any existing links to OCPEDGE tickets
- **Status**: Current ticket status
- **Components**: Current components
- **Priority**: Ticket priority

### Step 3: Group Related RHEL Tickets

Run the helper script to group tickets deterministically:

```bash
python3 "${SCRIPTS_DIR}/ocpedge_rhel_helper.py" group-tickets '<tickets_json>'
```

Pass a JSON array of ticket objects (each with `key`, `summary`, `issuelinks`, and optionally `description` fields from Step 2).

The script groups tickets using a union-find algorithm based on:
1. **Same base summary** after stripping version suffixes like `[rhel-9.6.z]`
2. **Clone relationships** via "clones" / "is cloned by" links
3. **Shared OCPBUGS references** in summary or description

Returns a JSON array of groups, each with `base_summary`, `tickets`, `ocpbugs_refs`, and `existing_ocpedge_links`.

### Step 4: Check for Existing OCPEDGE Stories

For each group of related RHEL tickets, check if an OCPEDGE story already exists.

**Method 1** — The `existing_ocpedge_links` field from the group-tickets output already contains OCPEDGE links found in the tickets' issuelinks. If non-empty, those are existing stories.

**Method 2** — Search OCPEDGE project directly:
For each group, search using the base summary:
```
mcp__mcp-atlassian__jira_search(
  jql='project = OCPEDGE AND summary ~ "<base summary keywords>" AND issuetype = Story ORDER BY created DESC',
  fields="summary,status,issuelinks,components",
  limit=5
)
```

Use key distinctive words from the base summary (not common words like "the", "a", "in"). If the summary references an OCPBUGS ticket, include that in the search:
```
mcp__mcp-atlassian__jira_search(
  jql='project = OCPEDGE AND text ~ "OCPBUGS-XXXXX" AND issuetype = Story ORDER BY created DESC',
  fields="summary,status,issuelinks,components",
  limit=5
)
```

If an existing OCPEDGE story is found for a group:
- **If the story is open** (not Closed): note it as already tracked. Check if all RHEL tickets in the group are linked to it — if some are missing links, those will be added in Step 7.
- **If the story is Closed**: check the Preliminary Testing status of the RHEL tickets in the group:
  - If **all** tickets have Preliminary Testing = Pass (or are Closed), the work is done — skip, no action needed.
  - If **some** tickets are still untested (Preliminary Testing = Requested or empty), present the user with options in Step 5:
    - **Create new** story linking only the untested tickets
    - **Skip** this group entirely
    
    Do NOT decide automatically — always ask the user which action to take for each Closed story with untested tickets.

### Step 5: Present the Plan

Before creating anything, present a summary table to the user and ask for confirmation:

```markdown
## Plan

### Groups to Create OCPEDGE Stories For

| # | Base Summary | RHEL Tickets | Existing OCPEDGE |
|---|-------------|-------------|-----------------|
| 1 | <summary> | RHEL-111, RHEL-222, RHEL-333 | None — will create |
| 2 | <summary> | RHEL-444, RHEL-555 | OCPEDGE-789 (open) — will add missing links |
| 3 | <summary> | RHEL-666, RHEL-777 | OCPEDGE-101 (Closed) — has untested tickets: **create new / skip?** |
| 4 | <summary> | RHEL-888 | OCPEDGE-202 (Closed) — all tickets passed, no action needed |
| 5 | <summary> | RHEL-999 | OCPEDGE-303 (open) — fully linked, no action needed |

### Stories to Create: X
### Links to Add: Y

Proceed? (yes/no)
```

Use AskUserQuestion to get confirmation. Do NOT proceed without explicit user approval.

The user may also:
- Remove groups from the plan
- Edit summaries
- Split or merge groups
- Skip certain tickets

Accommodate any adjustments before proceeding.

**If `dry_run` is true**: After presenting the plan, also show the exact summaries and descriptions that *would* be created (using `generate-summary` and `generate-description`), then stop. Print `🏁 Dry run complete — no changes were made in Jira.` and exit. Do NOT proceed to Step 6 or beyond.

### Step 6: Create OCPEDGE Stories

For each group that needs a new story, use the helper to generate the summary and description:

```bash
python3 "${SCRIPTS_DIR}/ocpedge_rhel_helper.py" generate-summary "<base_summary>"
python3 "${SCRIPTS_DIR}/ocpedge_rhel_helper.py" generate-description '["RHEL-111", "RHEL-222"]'
```

Then create the issue:

```
mcp__mcp-atlassian__jira_create_issue(
  project_key="OCPEDGE",
  summary="<output of generate-summary>",
  issue_type="Story",
  description="<output of generate-description>",
  components="Two Node Fencing,QE,RHEL-Verification"
)
```

### Step 6b: Create Subtasks

For each newly created OCPEDGE story, create two subtasks:

**Subtask 1 — Bug fix verification:**
```
mcp__mcp-atlassian__jira_create_issue(
  project_key="OCPEDGE",
  summary="Bug fix verification",
  issue_type="Sub-task",
  additional_fields="{\"parent\": \"OCPEDGE-XXXX\"}"
)
```

**Subtask 2 — Automation:**
```
mcp__mcp-atlassian__jira_create_issue(
  project_key="OCPEDGE",
  summary="Automation: adding to Polarion + Origin (two node) repo",
  issue_type="Sub-task",
  additional_fields="{\"parent\": \"OCPEDGE-XXXX\"}"
)
```

Create both subtasks in parallel for each story. Replace `OCPEDGE-XXXX` with the key of the story created in Step 6.

### Step 7: Link RHEL Tickets to OCPEDGE Stories

For each RHEL ticket that needs to be linked to its OCPEDGE story (both newly created and existing stories with missing links):

```
mcp__mcp-atlassian__jira_create_issue_link(
  link_type="Relates",
  inward_issue_key="<OCPEDGE-XXXX>",
  outward_issue_key="<RHEL-XXXXX>"
)
```

Create links in parallel where possible.

### Step 8: Report Results

After all operations complete, present a summary:

```markdown
## Results

### Created Stories

| OCPEDGE Story | Summary | RHEL Tickets Linked | Subtasks |
|--------------|---------|-------------------|----------|
| [OCPEDGE-XXX](https://issues.redhat.com/browse/OCPEDGE-XXX) | <summary> | RHEL-111, RHEL-222, RHEL-333 | OCPEDGE-AAA (verification), OCPEDGE-BBB (automation) |

### Added Links to Existing Stories

| OCPEDGE Story | New Links Added |
|--------------|----------------|
| [OCPEDGE-YYY](https://issues.redhat.com/browse/OCPEDGE-YYY) | RHEL-444, RHEL-555 |

### Skipped (Already Fully Linked)

| OCPEDGE Story | RHEL Tickets |
|--------------|-------------|
| [OCPEDGE-ZZZ](https://issues.redhat.com/browse/OCPEDGE-ZZZ) | RHEL-666 |

### Components Set on All New Stories
- Two Node Fencing
- QE
- RHEL-Verification
```

## Error Handling

- If a component name doesn't exist in the OCPEDGE project, warn the user and ask whether to proceed without it or to provide the correct component name
- If issue linking fails (e.g. "Relates" link type not found), try "Relates to" as an alternative link type name. If that also fails, list available link types using `mcp__mcp-atlassian__jira_search_fields` and ask the user
- If a create fails, report the error and continue with remaining groups
- Never silently skip errors — always report them in the results

## Examples

### Example 1: Auto-Discover Untested Tickets (Default)

```bash
/two-node:create-rhel-stories
```

Searches for untested TNF resource-agents RHEL tickets using the default JQL and creates OCPEDGE stories.

### Example 2: Dry Run

```bash
/two-node:create-rhel-stories --dry-run
```

Previews the plan without creating or modifying anything in Jira.

### Example 3: Specific Tickets

```bash
/two-node:create-rhel-stories RHEL-12345 RHEL-12346 RHEL-12347
```

Processes only the specified RHEL tickets.

### Example 4: JQL Query

```bash
/two-node:create-rhel-stories jql:project = RHEL AND component = "resource-agents" AND status != Closed ORDER BY created DESC
```

Searches for tickets matching the JQL query.

### Example 5: Dry Run with Specific Tickets

```bash
/two-node:create-rhel-stories --dry-run RHEL-12345 RHEL-12346
```

Previews what would be created for specific tickets without modifying Jira.

## Notes

- **Always confirm with the user before creating stories** — the plan step is mandatory
- **Use Markdown** in descriptions — the MCP tool converts Markdown to Jira wiki markup automatically
- **Parallelize** Jira API calls where possible (fetching tickets, creating links)
- **Link type**: Use "Relates" for the issue link type (this maps to "relates to" in Jira)
- The three required components are: `Two Node Fencing`, `QE`, `RHEL-Verification`
- If the user provides tickets from projects other than RHEL, still process them — the grouping and story creation logic applies the same way
