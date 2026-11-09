---
name: update-project
description: Update project documentation from what was accomplished in this session
argument-hint: [name-or-number]
---

# Update Project Documentation

Record what this session did in `projects/<name>/`. Use the project in
`$ARGUMENTS`, else the one loaded in this conversation. If there is none,
run `resume-project.py` with no argument and follow its `error_message`.

## Scope

Edit only files under `projects/<name>/`. Never change the `status:`
field (that is `/workspace:close-project`), memory files, session tasks,
or repo source.

## Rules for CLAUDE.md

`CLAUDE.md` is an index of 100 lines or less. Detail goes into the detail
files listed in its `## Reference Files` table.

- Check off completed `- [ ]` items and add newly found work as `- [ ]`.
- `## Progress` has one line per milestone, in its final state. When a
  milestone completes, replace the in-progress line; do not append.
- Findings, test output, and review discussion go into a detail file. For
  a new detail file, add a `| File | Content |` row.

## Steps

1. Compare the conversation with the project files. Read `CLAUDE.md` first
   unless its current content is already in context. If nothing is new,
   say so and stop.
2. Make the edits directly in this turn.
3. Run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project.py" touch <name>
   ```

   It stamps `last-active`, and archives completed items when
   `CLAUDE.md` is over the cap. If `over_cap` is still true, do what
   `error_message` says.
4. Tell the user in one or two lines what changed. If the session produced
   durable domain knowledge, suggest `/workspace:update-domain`.
