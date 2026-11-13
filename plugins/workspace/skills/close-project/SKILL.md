---
name: close-project
description: Close a project workspace, mark it done, and clean up its worktrees
argument-hint: "[name-or-number] [closing notes]"
---

# Close Project

`$ARGUMENTS`: an optional project name or number, then optional closing
notes. With no project, use the one loaded in this conversation, else run
`resume-project.py` with no argument and follow its `error_message`.

1. Dry run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" close <name> --dry-run
   ```

   If `already_done`, ask whether to continue (to update the notes).
2. If `needs_attention` is not empty (uncommitted or unpushed work), show
   each worktree's `status_text` and ask the user: commit and push first,
   discard (`--force`), or keep the worktrees (`--keep-worktrees`). Help
   them commit and push if they choose that. Never pass `--force` without
   their explicit choice.
3. If there are no notes in the arguments, ask for optional closing notes
   (outcome, PR links).
4. Apply:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" close <name> \
     [--force | --keep-worktrees] [--notes "<notes>"]
   ```

   It removes the worktrees and their local branches, sets
   `status: done` and `closed:`, and writes `## Closing Notes`. Report
   `branches_kept` (unmerged branches that `git branch -d` kept) and any
   `remove_errors`.

The project directory stays. `/workspace:resume-project <name>` can still
open it. If the project produced durable domain knowledge, suggest
`/workspace:update-domain <name>`.
