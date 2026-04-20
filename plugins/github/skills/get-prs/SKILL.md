---
name: get-prs
description: "Fetch open PRs for one or more GitHub repos and return structured JSON — use when asked about PR status, open PRs, or repo activity"
argument-hint: <org/repo [org/repo ...]>
user-invocable: true
---

# get-prs

Fetch open, non-draft pull requests for one or more GitHub repositories and return structured JSON.

## Usage

Run the get-prs script with the repos passed via `$ARGUMENTS`:

```bash
!`bash "${PLUGIN_DIR}/scripts/get-prs.sh" "${ARGUMENTS}"`
```

Present the raw JSON output to the user without reformatting.

## Output Schema

Returns a JSON object keyed by `org/repo`. Each value is an array of PR objects with these fields:

- `number` — PR number
- `title` — PR title
- `url` — direct link to the PR
- `author` — GitHub login of the author
- `assignees` — list of assignee logins
- `reviewers` — list of requested reviewer logins
- `labels` — list of label names
- `createdAt` — ISO 8601 timestamp
- `lastCommentAt` — timestamp of the most recent comment or review
- `lastCommentBy` — login of the most recent commenter/reviewer
- `openLongerThan3Days` — boolean
- `ageCategory` — one of `< 1 Day`, `< 2 Days`, `< 3 Days`, `> 3 Days`

## Prerequisites

- `gh` CLI authenticated with access to target repositories
- `jq` available on PATH
