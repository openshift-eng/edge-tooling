# Workspace Plugin — Post-Migration Cleanup

Findings from reviews on PR #249 that are worth addressing.

## Medium

- **`scripts/resume-project.py` — restore bounds check**
  The old `if not names: return "no_projects"` guard was removed. Numeric
  arg on empty workspace now shows confusing "out of range (1-0)" instead
  of "No recent projects found."

- **`scripts/consolidate-project.py:314` — non-atomic write**
  `write_text()` can truncate the project CLAUDE.md on crash. Use
  `tempfile` + `os.replace()` for atomic writes.

- **Consolidate `parse_frontmatter()` into `workspace_lib.py`**
  6 copies across `domain-info.py`, `resume-project.py`, `skills.py`
  with subtle behavioral differences (e.g. `skills.py` catches
  `UnicodeDecodeError` but doesn't coerce datetime; others do the
  opposite).

- **Add tests for `consolidate-project.py` and `resume-project.py`**
  3 of 5 scripts are covered; these two are not.

## Low

- **`scripts/setup.sh:1`, `tests/test_setup.sh:1` — shebang alignment**
  Use `#!/usr/bin/env bash` to match repo standard (CONTRIBUTING.md).

- **`scripts/resume-project.py:308,345` — rename `l` variable (ruff E741)**
  Rename to `ln` in both list comprehensions.

- **Skill frontmatter normalization (all 8 skills)** — add `allowed-tools`,
  use `workspace:` namespaced names, make descriptions action-triggered
  ("Use when…") per SKILL-GUIDELINES.md.
