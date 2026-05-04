---
name: skills-review:lint
argument-hint: "[--all | <path/to/SKILL.md> ...] [--severity error]"
description: "Lint SKILL.md files for quality, correctness, and adherence to the skill guidelines. Use when reviewing, creating, or modifying skills."
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# Skill Linter

Runs the skills quality linter (`scripts/lint-skills.py`) and presents findings.

## Synopsis

```text
/skills-review:lint                                    # Lint changed SKILL.md files (vs main)
/skills-review:lint --all                              # Lint all SKILL.md files
/skills-review:lint plugins/foo/skills/bar/SKILL.md    # Lint specific file(s)
/skills-review:lint --severity error                   # Only show errors, suppress warnings
```

## Steps

### 1. Parse Arguments

Determine the linting scope from `$ARGUMENTS`:

| Argument | Command | Behavior |
|----------|---------|----------|
| (empty) | `bash scripts/lint-skills.py` | Lint SKILL.md files changed vs main branch |
| `--all` | `bash scripts/lint-skills.py --check-all-files` | Lint every SKILL.md under `plugins/` |
| `--severity error` | `bash scripts/lint-skills.py --severity error` | Only report errors (E001–E007), skip warnings |
| file paths | `bash scripts/lint-skills.py <paths>` | Lint the specified file(s) |

Arguments can be combined: `--all --severity error` or `path/SKILL.md --severity error`.

### 2. Run the Linter

Execute from the repository root.

### 3. Present Results

- If the linter finds no issues, report that all checked skills passed.
- If issues are found, present them grouped by file with check ID, severity, and message.
- For errors (E001–E007), recommend immediate fixes.
- For warnings (W001–W012), suggest improvements but note they are advisory.
- If the user wants to fix issues, propose concrete patches for the failing SKILL.md files.

## Examples

**User:** `/skills-review:lint`
**Claude:** Runs linter on changed files, reports findings, offers to fix.

**User:** `/skills-review:lint --all`
**Claude:** Runs linter on all SKILL.md files across the repo.

**User:** `/skills-review:lint plugins/lvms/skills/setup-prereq/SKILL.md --severity error`
**Claude:** Lints the specified file, showing only errors.

## Edge Cases

- **No SKILL.md files changed**: Report that no files matched and suggest `--all` to lint everything.
- **Linter script missing**: Warn that `scripts/lint-skills.py` was not found at the expected path.

## Notes

- **Analysis-only by default**: This skill runs the linter and presents findings. It only modifies files if the user explicitly requests fixes.
- See `plugins/docs/SKILL-GUIDELINES.md` for the full quality standards reference.
- The linter is a standalone Python script with no external dependencies.
