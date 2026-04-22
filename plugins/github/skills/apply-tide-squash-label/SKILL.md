---
name: apply-tide-squash-label
description: "Label a GitHub PR for squash merging by posting a /label tide/merge-method-squash comment"
argument-hint: <PR URL>
user-invocable: true
allowed-tools: Bash
---

# apply-tide-squash-label

Post a comment on a GitHub pull request to label it for squash merging.

## Prerequisites

- `gh` CLI authenticated with access to the target repository

## Steps

1. Validate that `$ARGUMENTS` contains a PR URL.
2. Run:

```bash
gh pr comment "$ARGUMENTS" --body "/label tide/merge-method-squash"
```

3. Report the result to the user.
