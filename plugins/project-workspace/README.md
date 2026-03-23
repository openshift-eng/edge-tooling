# Project Workspace Plugin

Create and manage structured project workspaces for development tasks.

## Overview

The `project-workspace` plugin provides two commands for managing development task workspaces:

- **/project:new** - Create a new project workspace with type-specific scaffolding
- **/project:resume** - Resume work on an existing project with full context reload

## Project Types

Each project type gets custom scaffolding and templates:

| Type | Use Case | Directories Created | Progress Checklist |
|------|----------|--------------------|--------------------|
| **bug** | Bug investigation | `logs/`, `docs/` | Details captured → Logs analyzed → Root cause → Fix → PR |
| **feature** | Feature development | `docs/`, `patches/` | Design → Implementation → Tests → PRs → Merged |
| **ci-testing** | CI/test work | `results/`, `scripts/` | Jobs identified → Failures analyzed → Fixes → CI passing |
| **docs** | Documentation | `drafts/` | Draft → Technical review → Editorial → PR |
| **analysis** | Code review/analysis | `docs/` | Analysis → Findings → Recommendations → Actions |

## Commands

### /project:new

Create a new project workspace with interactive scaffolding.

**Usage:**
```bash
/project:new                                  # Interactive mode
/project:new Fix kubelet timeout after fencing # Quick create with description
```

**What it does:**
1. Gathers task information (description, type, JIRA, related repos)
2. Generates folder name (from JIRA ID or description)
3. Creates directory structure based on type
4. Generates CLAUDE.md with YAML frontmatter and type-specific sections
5. Suggests relevant skills and next steps

**Example workflow:**
```bash
/project:new
# Describe: "Investigate OCPBUGS-74679 - kubelet fails to start"
# Type: bug (auto-detected)
# JIRA: https://issues.redhat.com/browse/OCPBUGS-74679
# Repos: cluster-etcd-operator, installer (selected from dev-env)
# Creates: projects/OCPBUGS-74679/ with logs/, docs/, CLAUDE.md
```

### /project:resume

Resume an existing project with full context loading.

**Usage:**
```bash
/project:resume                    # Interactive picker (shows recent 3)
/project:resume OCPBUGS-74679      # Resume by name
/project:resume 1                  # Resume project #1 from recent list
```

**What it does:**
1. Selects project (numeric shorthand, name, or interactive)
2. Loads CLAUDE.md context with YAML frontmatter parsing
3. Auto-loads repo context files for related repos
4. Lists all project files
5. Shows progress checklist summary
6. Suggests next steps based on project state

**Numeric shorthand:**
If your SessionStart hook shows recent projects:
```
📂 Recent projects:
1. OCPBUGS-74679 (bug, updated 2 hours ago)
2. add-lvm-metrics (feature, updated 1 day ago)
3. ci-failure-analysis (ci-testing, updated 3 days ago)
```

You can resume with `/project:resume 1` instead of typing the full name.

## CLAUDE.md Structure

Every project gets a CLAUDE.md file with:

**YAML Frontmatter:**
```yaml
---
project: OCPBUGS-74679
type: bug
created: 2026-03-23
status: active
jira: https://issues.redhat.com/browse/OCPBUGS-74679
repos:
  - cluster-etcd-operator
  - installer
related_links:
  - https://github.com/openshift/installer/pull/12345
---
```

**Markdown Sections:**
- Summary (type-specific: Bug Summary, Feature Summary, etc.)
- Type-specific sections (Timeline, Design Notes, CI Jobs, etc.)
- Progress checklist
- Related Source Code table (auto-populated from repo context)
- Suggested Skills

## Integration with dev-env-setup

When used in a workspace with `dev-env-setup`:
- Repo selection pulls from `dev-env.yaml`
- Resume auto-loads context from `repos/<name>/CLAUDE.md` or `presets/*/context/<name>.md`
- Related Source Code table populated from repo context files

## Requirements

- Projects directory at workspace root (created automatically)
- Optional: dev-env.yaml for repo integration
- Optional: SessionStart hook for recent projects list

## Example Directory Structure

```
projects/
├── OCPBUGS-74679/
│   ├── CLAUDE.md          # Project context with YAML frontmatter
│   ├── .gitignore         # Excludes logs, large files
│   ├── logs/              # Log files, must-gather data
│   └── docs/              # Investigation notes
├── add-lvm-metrics/
│   ├── CLAUDE.md
│   ├── docs/              # Design docs
│   └── patches/           # WIP patches
└── ci-failure-analysis/
    ├── CLAUDE.md
    ├── results/           # Test results
    └── scripts/           # Analysis scripts
```

## License

Apache-2.0
