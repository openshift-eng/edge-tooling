---
name: get-edge-tooling-prs
description: "Fetch and display open PRs for openshift-eng/edge-tooling in a formatted list grouped by age"
user-invocable: true
---

# get-edge-tooling-prs

Fetch open PRs for `openshift-eng/edge-tooling` and present them as a formatted, human-readable list grouped by age.

## Step 1: Fetch PR data

```bash
!`bash "${PLUGIN_DIR}/scripts/get-prs.sh" openshift-eng/edge-tooling`
```

## Step 2: Format output

The JSON output is keyed by repo. Access the PR array via `["openshift-eng/edge-tooling"]`, then group PRs by `ageCategory` in this order (oldest group first):

1. **> 3 Days**
2. **< 3 Days**
3. **< 2 Days**
4. **< 1 Day**

Omit any group that has no PRs. Within each group, list PRs oldest to newest.

Format each PR as:

```text
**#N — Title** https://github.com/openshift-eng/edge-tooling/pull/N
- Author: username | Assignees: a, b | Reviewers: c, d
- Last comment: YYYY-MM-DD by username
```

If assignees or reviewers are empty, show "none".
If lastCommentAt is null, show "no comments".

## Prerequisites

- `gh` CLI authenticated with access to target repositories
- `jq` available on PATH
