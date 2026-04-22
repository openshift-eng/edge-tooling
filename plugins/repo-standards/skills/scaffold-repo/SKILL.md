---
name: repo-standards:scaffold-repo
description: Use when setting up a new repository or bringing an existing repo into compliance with agentic development standards — scaffolds CONTRIBUTING.md, AGENTS.md, .coderabbit.yaml, and docs/architecture.md
user-invocable: true
allowed-tools: Read, Write, Bash, AskUserQuestion, Glob
argument-hint: "[target-directory]"
---

# Scaffold Repository

You are scaffolding a repository to meet agentic development standards. All conventions come from the Repo Standards Laws.

## User Arguments

The user may provide a target directory: `$ARGUMENTS`

If no directory is provided, use the current working directory.

---

## Workflow

### Step 0: Load Laws

Read the laws index at `${CLAUDE_PLUGIN_ROOT}/references/repo-standards-laws.md`. Load all files listed under the "Scaffold Repo" task in the Agent Task Index.

The Laws are authoritative. When this skill and the Laws conflict, the Laws win.

---

### Step 1: Detect Current State

Run the artifact check script to detect what exists and what is missing:

```bash
echo '{"cwd":"<target-directory>"}' | bash "${CLAUDE_PLUGIN_ROOT}/scripts/check-repo-artifacts.sh"
```

If all artifacts pass, report compliance status to the user and exit. No further action needed.

---

### Step 2: Gather Project Details

Use `AskUserQuestion` to collect the following. Do not assume defaults for any of these:

1. **Project name** --- human-readable name for the project
2. **Primary language** --- Go, Python, Shell, etc.
3. **Team name** --- team that owns this repository
4. **Upstream relationship** --- does this repo interact with an upstream project? (y/n)
5. **Brief project description** --- 2--3 sentences describing what the project does

---

### Step 3: Generate Missing Artifacts

For each missing artifact, generate the file using templates from the Laws and customize with the gathered project details.

#### CONTRIBUTING.md (if missing)
- Use the template structure from Law 02
- Fill in language-specific tooling (formatters, test commands) based on the primary language
- Include all required sections

#### AGENTS.md (if missing)
- Follow Law 03 conventions
- Include: project overview, build/test/lint commands, code style, PR/commit format, security considerations
- Keep under 200 lines

#### .coderabbit.yaml (if missing)
- Follow Law 04 requirements
- Enable auto_review
- Add path_filters appropriate for the project language
- Include basic review instructions

#### docs/architecture.md (if missing and repo has multiple components)
- Follow Law 05 recommendations
- Create a skeleton with section headers for the team to fill in

#### docs/upstream.md (if missing and upstream relationship confirmed)
- Follow Law 06 recommendations
- Create a skeleton with section headers for the team to fill in

---

### Step 4: Create CLAUDE.md Symlink

If CLAUDE.md does not exist or is not a symlink to AGENTS.md:

```bash
ln -sf AGENTS.md CLAUDE.md
```

---

### Step 5: Summary

Report to the user:
- Files created (list each with path)
- Files that already existed (skipped)
- Remaining manual steps:
  - Fill in skeleton sections in docs/architecture.md and docs/upstream.md (if created)
  - Register with Cyborg program (if applicable)
  - Configure OpenShift Test Extensions (if applicable)
  - Review generated AGENTS.md and customize for project specifics
