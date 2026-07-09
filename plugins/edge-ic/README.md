# Edge IC Plugin

Individual contributor workflow automation for TODO management, status reporting, and Jira updates.

## Commands

### `/edge-ic:daily-report`

Generate a daily standup report from today's TODO file in Slack-ready format.

**Usage:**

```text
/edge-ic:daily-report
```

**Output:**

- Plain text report with completed and in-progress items
- Slack emoji format (`:done-circle-check:`, `:in-progress:`, `:jira-blocker:`)
- Jira ticket links
- Ready for direct copying into Slack

---

### `/edge-ic:weekly-wrapup`

Generate a weekly summary from the past week's TODO files.

**Usage:**

```text
/edge-ic:weekly-wrapup
```

**Output:**

- Aggregated accomplishments from Monday-Friday
- Grouped by theme (bugs, features, process improvements, etc.)
- Jira ticket links
- Markdown format

---

### `/edge-ic:daily-show`

Display the TODO list for today, or for a specific past day. Supports filtering to specific sections and limiting the number of items shown.

**Usage:**

```text
/edge-ic:daily-show
/edge-ic:daily-show 2026-07-01
/edge-ic:daily-show --section=priority,progress
/edge-ic:daily-show --limit=5
```

**Arguments:**

- date (`YYYY-MM-DD`): show a past day's TODO instead of today's
- `--section=<name>[,<name>...]`: show only the named section(s) (`Priority`, `In Progress`, `Waiting on Review`, `Review Requests`, `Completed`, `Backlog`, `Key Issues Tracked`, or common aliases)
- `--limit=<n>`: show only the first `<n>` items

**Output:**

- Full TODO file contents as written, including tracked links and notes
- Read-only — never modifies the file

---

### `/edge-ic:daily-done`

Mark a specific task in today's TODO file as done.

**Usage:**

```text
/edge-ic:daily-done OCPEDGE-2510
/edge-ic:daily-done the TNF deploy item
```

**Output:**

- Matched item checked off (`- [x]`) and moved to Completed
- All tracked links and notes preserved

---

### `/edge-ic:daily-link`

Add a PR, doc, Jira, or note link to an existing TODO task.

**Usage:**

```text
/edge-ic:daily-link OCPEDGE-2510 PR https://github.com/openshift/installer/pull/1234
/edge-ic:daily-link item 2 Note: needs metal cluster to reproduce
```

**Output:**

- Indented link sub-line added beneath the matched task
- Link type auto-classified from the URL when not specified

---

### `/edge-ic:task-info`

Show detailed info about a specific TODO task, including live Jira and PR status.

**Usage:**

```text
/edge-ic:task-info OCPEDGE-2510
```

**Output:**

- Task description, section, and all tracked links/notes
- Live status pulled from Jira (via MCP) and GitHub (via `gh`)
- Offers to backfill a missing Jira link when the task has a bare ticket key

---

### `/edge-ic:sprint-status`

Query all tickets in a sprint and display them grouped by status.

**Usage:**

```text
/edge-ic:sprint-status [sprint-number] [--assignee=<user>] [--format=<type>]
```

**Arguments:**

- `sprint-number`: Sprint number (e.g., `287`)
- `--assignee=<user>`: Filter by assignee (email, `currentUser()`, or `Unassigned`)
- `--format=<type>`: Output format (`table`, `simple`, `keys-only`)

**Examples:**

```text
/edge-ic:sprint-status 287
/edge-ic:sprint-status 287 --assignee=currentUser()
/edge-ic:sprint-status 287 --format=simple
```

---

### `/edge-ic:update-jira`

Read today's TODO file and update related Jira issues with accomplishments.

**Usage:**

```text
/edge-ic:update-jira
```

**Features:**

- Parses completed items from today's TODO
- Prompts for which issues to update
- Adds comments with accomplishment details
- Updates issue status if appropriate

## Installation

Add the edge-tooling marketplace to Claude Code:

```text
/plugin marketplace add openshift-eng/edge-tooling
```

Then install the edge-ic plugin:

```text
/plugin install edge-ic
```

## Requirements

- Jira MCP server configured for Jira commands
- TODO files in `.daily/YYYY/MM/YYYY-MM-DD.md` format (relative to repository root)

## Use Cases

- Daily standup reporting to Slack
- Weekly accomplishment summaries
- Sprint progress tracking
- Jira issue updates from TODO items
