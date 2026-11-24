---
name: close-project
description: Close a project workspace, mark it done, and clean up its worktrees
argument-hint: "[name-or-number] [closing notes]"
---

# Close Project

`$ARGUMENTS`: an optional project name or number, then optional closing
notes. Resolve the project first: run `resume-project.py <first-token>`
(a number only resolves there; `project.py` takes names), or with no token
use the project loaded in this conversation, else run it with no argument
and follow its `error_message`. Use the resolved `project.name` below.

After every script call, if `status` is not `ok`, show `error_message` and stop, unless a step below says otherwise. Pass `<name>` and the notes
single-quoted, writing each embedded `'` as `'\''`; notes often contain
pasted `$(...)`, backticks or `"`, which double quotes would expand.

1. Dry run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" close <name> --dry-run
   ```

   If `already_done`, ask whether to continue (to update the notes).
2. If `needs_attention` is not empty (uncommitted or unpushed work), show
   each worktree's `status_text` and ask the user: commit and push first,
   discard (`--force`), or keep the worktrees (`--keep-worktrees`). Help
   them commit and push if they choose that, then repeat step 1. Never pass
   `--force` without their explicit choice.
3. If there are no notes in the arguments, ask for optional closing notes
   (outcome, PR links).
4. Apply:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" close <name> \
     [--force | --keep-worktrees] [--notes '<notes>']
   ```

   If it returns `needs_attention`, go back to step 2.

   It removes the worktrees and their local branches, sets
   `status: done` and `closed:`, and writes `## Closing Notes`. Report
   `branches_kept` (unmerged branches, and branches whose worktree was
   already missing, are never deleted) and any `remove_errors`.

The project directory stays. `/workspace:resume-project <name>` can still
open it. If the project produced durable domain knowledge, suggest
`/workspace:update-domain <name>`.
