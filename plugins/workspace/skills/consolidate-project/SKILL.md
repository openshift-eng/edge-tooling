---
name: consolidate-project
description: Consolidate a bloated project CLAUDE.md by archiving completed checklist items
argument-hint: [name-or-number]
---

# Consolidate Project CLAUDE.md

`/workspace:update-project` runs this automatically when `CLAUDE.md` is
over 100 lines. Use this skill to run it on demand.

Resolve the project as in `/workspace:update-project`, then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/consolidate-project.py" <name>
```

Sections with 10 or more completed items keep their last 3 and move the
rest to `progress-archive.md`. Unchecked items are never touched. Report
the result in one line. On `over_threshold_no_sections`, move narrative
into a detail file by hand, with a Reference Files row.
