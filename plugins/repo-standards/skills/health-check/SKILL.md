---
name: repo-standards:health-check
description: Use when auditing a repository against the agentic development checklist — checks required artifacts, configuration quality, AGENTS.md size, Coderabbit config, and reports compliance status
user-invocable: true
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "[target-directory]"
---

# Health Check

You are auditing a repository against the agentic development standards. All pass/fail criteria come from the Repo Standards Laws.

## User Arguments

The user may provide a target directory: `$ARGUMENTS`

If no directory is provided, use the current working directory.

---

## Workflow

### Step 0: Load Laws

Read the laws index at `${CLAUDE_PLUGIN_ROOT}/references/repo-standards-laws.md`. Load files listed under the "Health Check" task in the Agent Task Index:

- `laws/01-required-artifacts.md`
- `laws/03-agents-md-convention.md`
- `laws/04-coderabbit-config.md`

The Laws are authoritative. When this skill and the Laws conflict, the Laws win.

---

### Step 1: Run Artifact Check

```bash
echo '{"cwd":"<target-directory>"}' | bash "${CLAUDE_PLUGIN_ROOT}/scripts/check-repo-artifacts.sh"
```

Parse the output. If JSON is returned, artifacts are missing. If no output, all required files exist.

---

### Step 2: Run AGENTS.md Size Check

```bash
echo '{"cwd":"<target-directory>"}' | bash "${CLAUDE_PLUGIN_ROOT}/scripts/check-agents-md-size.sh"
```

Parse the output. If JSON is returned, the file exceeds the 200-line limit.

---

### Step 3: Run CodeRabbit Config Check

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/check-coderabbit-config.sh" "<target-directory>"
```

Parse the JSON output for `exists`, `auto_review`, `path_filters`, `instructions`, and `pass`.

---

### Step 4: Additional Read-Based Checks

Perform these checks by reading files directly:

1. **CLAUDE.md symlink** --- use Bash to verify CLAUDE.md is a symlink pointing to AGENTS.md: `test -L CLAUDE.md && readlink CLAUDE.md` should return `AGENTS.md`. A regular file or a symlink to a different target is non-compliant.
2. **CONTRIBUTING.md sections** --- verify required section headers exist (case-insensitive match): Getting Started, Development Workflow, Branch Naming, Commit Messages, Testing, Code Review, Code Style
3. **docs/architecture.md** --- check if it exists (recommended, not required)
4. **docs/upstream.md** --- check if it exists (recommended, not required)

---

### Step 5: Produce Report

Output a compliance table:

```text
| Check | Status | Detail |
|-------|--------|--------|
| README.md | PASS | Present |
| CONTRIBUTING.md | FAIL | Missing |
| AGENTS.md | PASS | Present, 142 lines |
| AGENTS.md size | PASS | 142 lines (limit: 200) |
| CLAUDE.md symlink | PASS | Symlink to AGENTS.md |
| .coderabbit.yaml | PASS | auto_review enabled |
| CodeRabbit path_filters | WARN | Not configured |
| CodeRabbit instructions | WARN | Not configured |
| docs/architecture.md | WARN | Not present (recommended) |
| docs/upstream.md | WARN | Not present (recommended) |
| CONTRIBUTING.md sections | FAIL | Missing: Branch Naming, Code Style |
```

Use these status levels:
- **PASS** --- meets the requirement
- **FAIL** --- required item missing or non-compliant
- **WARN** --- recommended item missing or non-optimal

---

### Step 6: Offer Remediation

If any FAIL items exist, offer to run `/repo-standards:scaffold-repo` to generate missing artifacts.

If only WARN items exist, note them as recommendations but confirm the repository meets minimum compliance.
