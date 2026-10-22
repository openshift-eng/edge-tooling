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

## Step 2.5: Worktree & Skill Cleanup

Substeps 2.5a-2.5d apply if `P.worktree_status` (from Step 1's
resume-project.py output) is non-empty (multi-repo worktrees).
Substep 2.5s applies if `P.frontmatter.worktree_path` is present
(self-repo worktree). Substep 2.5e applies if
`P.frontmatter.skills` is non-empty. Substep 2.5f always runs.

**2.5a. Display worktree status**

Show a status summary from `P.worktree_status`:

```
| Repo | Branch | Status |
|------|--------|--------|
```

Where status is derived from each entry in `P.worktree_status`:
- `exists=false` → `MISSING` (already gone, skip cleanup)
- `error` is non-null → `ERROR: <message>`
- `dirty=true` and `ahead > 0` → `dirty (N files), ahead by N`
- `dirty=true` → `dirty (N files)`
- `no_upstream=true` → `no upstream (local-only commits)`
- `ahead > 0` → `ahead by N`
- otherwise → `clean`

**2.5b. Handle worktrees needing attention**

If any worktree is dirty, has unpushed commits (`ahead > 0`), or has
no upstream (`no_upstream=true`), warn the user:

> "The following worktrees need attention before removal:
>   - `<repo>` (`<branch>`): <status detail>
>
> What would you like to do?"

Use AskUserQuestion with options:
- "Commit and push changes before closing"
- "Discard changes and remove worktrees"
- "Keep worktrees (close project but leave them in place)"

If "commit and push": help the user commit and push in each worktree.
For `no_upstream` branches, push with `-u` to set the upstream:
`git -C <worktree-path> push -u fork <branch>`
(each worktree's `path` in `P.worktree_status` is absolute).

**2.5c. Remove worktrees**

Unless the user chose to keep worktrees, remove each existing worktree.
The worktree `path` and the repo checkout are derivable from
`P.worktree_status` (paths are absolute). For a repo at
`<workspace>/repos/<repo>`:

```bash
# If user chose "Discard changes" (worktree may be dirty):
git -C <workspace>/repos/<repo> worktree remove --force .worktrees/<branch>
# If worktree is clean (user chose "Commit and push" or was already clean):
git -C <workspace>/repos/<repo> worktree remove .worktrees/<branch>
```

For non-PR branches (those NOT starting with `pr/`), also offer to
delete the local branch:
```bash
git -C <workspace>/repos/<repo> branch -d <branch>
```

For PR checkout branches (`pr/<number>`), just delete the local branch
— these are local refs created from the remote PR, not remote branches:
```bash
git -C <workspace>/repos/<repo> branch -D pr/<number>
```

**2.5d. Update frontmatter**

If worktrees were removed, the `worktrees:` field will be cleared
in Step 3b (set to `worktrees: []`).

If worktrees were kept, leave the field as-is and add a note to the
closing summary: "Worktrees preserved — branches still active in repos."

**2.5s. Self-repo worktree cleanup**

This substep applies only when `P.frontmatter.worktree_path` is present
and `P.worktree_status` is empty (self-repo project, not multi-repo).

1. Check dirty state inside the worktree:

   ```bash
   git -C <P.frontmatter.worktree_path> status --porcelain
   git -C <P.frontmatter.worktree_path> rev-parse --abbrev-ref @{upstream} 2>/dev/null
   # If the above fails → no_upstream. Otherwise:
   git -C <P.frontmatter.worktree_path> rev-list --count @{upstream}..HEAD
   ```

   Derive status using the same logic as 2.5a (dirty/ahead/no-upstream).

2. If the worktree needs attention (dirty, ahead, or no upstream),
   present the same AskUserQuestion as 2.5b:
   - "Commit and push changes before closing"
   - "Discard changes and remove worktree"
   - "Keep worktree (close project but leave it in place)"

   If "commit and push": help the user commit and push. For
   `no_upstream` branches: `git -C <worktree_path> push -u fork <branch>`.
   If either the commit or push fails, keep the worktree and report the
   error — do not proceed to removal.

   If the worktree is clean (or commit and push succeeded), proceed
   to removal. If the user chose to keep the worktree, skip to step 5.

3. Remove the worktree using git:

   ```bash
   # Clean worktree:
   git worktree remove <P.frontmatter.worktree_path>
   # Dirty worktree (user chose "Discard"):
   git worktree remove --force <P.frontmatter.worktree_path>
   ```

   Then delete the local branch:
   ```bash
   git branch -d <P.frontmatter.branch>
   ```

4. If the worktree path does not exist on disk (already removed
   externally), skip removal silently but treat it as removed for
   frontmatter purposes (clear `worktree_path` in Step 3b).

5. If the user chose to keep the worktree, leave it in place and add a
   note to the closing summary: "Worktree preserved at
   `<worktree_path>` — branch `<branch>` still active."

**2.5e. Remove skill symlinks**

If `P.frontmatter.skills` is non-empty, check which linked skills are
still needed by other active projects:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/skills.py" unlink-check <P.name>
```

If the output has `status: "error"`, mention it and continue to Step 3.
Otherwise, for each entry in `skills`:

- `removable: true` → remove the symlink (plain `rm` — the target is a
  symlink, never use `rm -r`):

  ```bash
  rm "<workspace>/.claude/skills/<name>"
  ```

- `removable: false` with non-empty `used_by` → keep it; report:
  "Skill `<name>` kept — still used by `<used_by>`."
- `missing: true` → nothing to remove; skip silently.
- `removable: false` with `is_symlink: false` → not ours to delete;
  report: "`.claude/skills/<name>` is not a symlink — left in place."

Skills are unlinked even when the user chose to keep worktrees in 2.5b —
symlinks surface autocomplete entries and have nothing to do with
branches. The `skills:` frontmatter is cleared in Step 3b regardless of
what was removable.

**2.5f. Clean up handoff marker**

If a checkpoint was armed for this project, remove it so a subsequent
`/clear` doesn't attempt to resume a closed project:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" clear \
  --project "<P.name>"
```

No output handling needed — the marker is silently removed only if it
references this project.

If `mcp__workspace__detach` is available, call it.

## Step 3: Update Project CLAUDE.md

**3a. Read the current CLAUDE.md**

Read the full `project.context_file` (absolute path from Step 1).

**3b. Update frontmatter fields**

Using the Edit tool, update the YAML frontmatter:

1. Change `status: active` (or whatever the current status is) to
   `status: done`
2. Add a `closed: <YYYY-MM-DD>` field (today's date) after the
   `status` line. If a `closed:` field already exists, update it.
3. Update `last-active: <YYYY-MM-DDTHH:MM>` to the current time (or add it after
   `closed:` if it doesn't exist).
4. If worktrees were removed in Step 2.5, change the `worktrees:`
   list to `worktrees: []`. Leave `branch:` as-is for historical
   reference.
   For self-repo projects: if the worktree was removed (or already
   absent) in Step 2.5s, remove the `worktree_path:` line from
   frontmatter (or set to empty string). Leave `branch:` as-is for
   historical reference.
   If the worktree was kept, leave `worktree_path:` as-is.
5. If the project had a `skills:` list, change it to `skills: []`
   (the symlinks were handled in Step 2.5e; the cleared list records
   that this project no longer holds any skill references).

**3c. Add closing notes section**

If the user provided closing notes (non-empty, not "no"):

Closing Notes always go in CLAUDE.md (the index), not in detail files.

1. Check if a `## Closing Notes` section already exists in the file.
2. If it exists, replace its content with the new notes.
3. If it doesn't exist, add a `## Closing Notes` section at the end
   of the file with the notes and today's date:

```markdown
## Closing Notes

_Closed YYYY-MM-DD_

<user's closing notes>
```

## Step 4: Confirm Closure

Display a brief confirmation:

```
Project `<name>` marked as done.
```

If closing notes were added, include them in the confirmation.
Remind the user that closed projects won't appear in the SessionStart
summary, but can still be resumed with `/workspace:resume-project <name>`.

If the project produced domain-worthy lessons, suggest
`/workspace:update-domain <name>` before closing out.

---

## Important Notes

- Always use the Read tool before editing, and Edit tool for changes
- Never delete the project directory — closing just updates metadata
- Use today's date for the `closed` field
- The project will be filtered from the SessionStart "Recent projects"
  table but remains fully accessible via `/workspace:resume-project`
