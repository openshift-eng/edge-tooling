# git-commits

Structured commit workflow — teaches Claude to create small, logical git
commits that tell a coherent implementation story instead of committing
entire files at once.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install git-commits
```

## Usage

Always active — whenever you ask to commit changes, the skill plans
small, logical commits automatically. You can also invoke explicitly:

```text
/small-commits
```

After presenting the commit plan, you choose:

- **Create commits** — stages and commits each piece
- **Propose only** — shows commit messages and file groupings without committing

## What It Does

- Analyzes pending changes and groups them into logical commits
- Stages specific hunks (not whole files) when a file contains changes
  for multiple logical commits, using the patch-file technique
- Uses `git commit --fixup=<sha>` when a change belongs to an earlier commit
- Offers to run `git rebase --autosquash` to fold fixups at the end
- Produces a git log that reads like a coherent implementation story

## Notes

- **Side effects**: Creates git commits on the current branch and
  optionally rewrites local history via rebase. Confirms the commit
  plan before executing and confirms before any rebase.
- **No push**: Never pushes to a remote.
- **PR branches are personal**: Rewriting history on non-main branches
  is expected. Do not warn about force-push implications.

## Requirements

- Git repository with uncommitted changes
- Category: `util`

## Author

pmatuszak
