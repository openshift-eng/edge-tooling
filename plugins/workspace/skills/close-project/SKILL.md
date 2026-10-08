---
name: close-project
description: Close a project workspace, mark it done, and clean up its worktrees
argument-hint: "[name-or-number] [closing notes]"
---

# Close Project Workspace

You are helping a developer close a project workspace by marking it as
done. This updates the project's CLAUDE.md frontmatter and optionally
records closing notes.

Everything after the skill name in `$ARGUMENTS` is parsed as follows:
- The **first token** is an optional project name or numeric shorthand.
- Everything after the first token is treated as **closing notes**.

## Step 1: Select Project

**1a. Resolve project name**

Extract the first token from `$ARGUMENTS`. Run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resume-project.py" <first-token>`
via Bash (omit the token if none was provided). Parse the JSON and handle
by `status`:

- **`ok`** — use `project.name` as the target. All paths in `project` are
  **absolute** — use them directly in Read/Bash (no joining). Proceed to 1b.
- **`no_argument`** — check if a project was loaded earlier in this
  conversation (e.g., via `/workspace:resume-project`). If so, use that project
  name as the default: re-run the script with that name and proceed.
  Otherwise, present the first 3 `alternatives` as AskUserQuestion
  options plus "See all projects". Re-run with the chosen name.
- **`not_found`** / **`out_of_range`** — show `error_message`, present
  `alternatives` as a picker, re-run with chosen name.
- **`no_projects`** — show `error_message` and stop.
- **`error`** — if the message mentions **PyYAML**, relay the install
  command (`pip3 install pyyaml`) rather than retrying. Otherwise show the
  message and stop.

**1b. Check current status**

If `project.frontmatter.status` is `done`:
- Inform the user: "Project `<name>` is already marked as done."
- Ask if they'd like to update the closing notes anyway. If no, stop.

## Step 2: Gather Closing Notes

**2a. Extract notes from arguments**

If there is text after the project identifier in `$ARGUMENTS`, use it
as the closing notes.

**2b. Ask for notes**

If no notes were provided in the arguments, ask the user:

> "Any closing notes for this project? (outcome, resolution, links to
> PRs, etc.) Say 'no' to skip."

## Step 3: Inspect Worktrees and Skills

Run via Bash:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/close-project.py" check "<P.name>"
```

If `status` is not `ok`, show `error_message` and stop. Otherwise
`worktrees` lists every worktree of the project (multi-repo `worktrees:`
and the self-repo `worktree_path:` alike; self-repo entries have
`repo: "(self)"`), each with absolute `path`, `branch`, `exists`, `dirty`,
`dirty_files`, `ahead` and `no_upstream`. `skills` lists linked repo skills
with `shared` (another active project still uses it).

If `worktrees` is non-empty, show a summary table:

```
| Repo | Branch | Status |
|------|--------|--------|
```

Status per entry:
- `exists=false` → `MISSING` (already gone, nothing to clean up)
- `dirty` and `ahead > 0` → `dirty (N files), ahead by N`
- `dirty` → `dirty (N files)`
- `no_upstream` → `no upstream (local-only commits)`
- `ahead > 0` → `ahead by N`
- otherwise → `clean`

(A `pr/<number>` branch with no upstream and nothing else wrong is
reported as `no upstream` but is treated as clean — those are throwaway
local refs.)

## Step 4: Decide What to Do With Worktrees

Pick the `--worktrees` mode for Step 5:

- No worktrees, or all are `MISSING` or clean → `remove`.
- Otherwise (any dirty, ahead, or no upstream) warn the user:

  > "The following worktrees need attention before removal:
  >   - `<repo>` (`<branch>`): <status detail>
  >
  > What would you like to do?"

  Use AskUserQuestion with options:
  - "Commit and push changes before closing"
  - "Discard changes and remove worktrees" → mode `discard`
  - "Keep worktrees (close project but leave them in place)" → mode `keep`

  If "commit and push": help the user commit and push in each worktree
  (`path` is absolute). For `no_upstream` branches push with `-u`:
  `git -C <path> push -u fork <branch>`. If a commit or push fails, report
  it and do not remove that worktree. Re-run the `check` command, then
  continue with mode `remove` — the script itself refuses to remove
  anything still dirty or unpushed, so a failed push is safe.

## Step 5: Close the Project

Run via Bash (omit `--notes` when there are none):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/close-project.py" apply "<P.name>" \
  --worktrees <remove|keep|discard> --notes "<closing notes>"
```

The script removes worktrees and their local branches according to the
mode, unlinks repo skills no other active project uses, marks the project
`status: done` with `closed:` and `last-active:`, clears `worktrees:` /
`worktree_path:` / `skills:` for what was removed, writes (or replaces) the
`## Closing Notes` section, and clears a pending handoff marker. Skills are
unlinked even when worktrees are kept.

Parse the JSON: `removed` and `kept` list what was done per item (`kind`
is `worktree`, `branch` or `skill`; `kept` entries carry a `reason`), and
`errors` lists anything that failed. Failures never abort the close —
report them. If `status` is not `ok`, show `error_message` and stop.

## Step 6: Confirm Closure

Display a brief confirmation:

```
Project `<name>` marked as done.
```

- If closing notes were added, include them.
- Report `kept` entries: skills still used by other projects ("Skill
  `<name>` kept — still used by `<users>`."), and preserved worktrees
  ("Worktrees preserved — branches still active in repos.").
- Report any `errors`.

Remind the user that closed projects won't appear in the SessionStart
summary, but can still be resumed with `/workspace:resume-project <name>`.

If the project produced domain-worthy lessons, suggest
`/workspace:update-domain <name>` before closing out.

---

## Important Notes

- All deterministic work lives in `scripts/close-project.py` (also used by
  the Claude Code mod UI) — do not hand-edit the frontmatter or run
  `git worktree remove` yourself
- The script never deletes the project directory — closing just updates
  metadata, and re-running it is safe
- The project will be filtered from the SessionStart "Recent projects"
  table but remains fully accessible via `/workspace:resume-project`
