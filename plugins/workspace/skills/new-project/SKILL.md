---
name: new-project
description: Create a new project workspace for a development task (bug, feature, CI, docs, analysis)
argument-hint: [description]
---

# New Project Workspace

You are helping a developer create a new project workspace. Projects live
under the `projects/` directory in the workspace and provide structured
working environments for specific tasks (bug investigations, feature
development, CI work, etc.).

Everything after the skill name in `$ARGUMENTS` is an optional initial
description of the task.

## Step 0: Resolve the Workspace Root

The **workspace root** `$WS` is the directory where `dev-env.yaml`,
`repos/`, and `projects/` live — the directory Claude Code was launched in.
Determine it once and reuse it:

1. If `$CLAUDE_PROJECT_DIR` is set, use it.
2. Otherwise, use the nearest ancestor of the current directory that
   contains `dev-env.yaml`.
3. If neither resolves (e.g., the workspace hasn't been set up), tell the
   user to run `/workspace:setup-environment` first, or ask them for the
   workspace path.

**Prefix every `projects/`, `repos/`, and `git -C` path below with `$WS`.**
Shell state does not persist between Bash tool calls, so always use the
absolute `$WS/...` form rather than relative paths.

## Step 1: Gather Task Information

Ask the user questions to understand what they're working on. Use the
AskUserQuestion tool for structured questions and encourage free-text
descriptions.

**1a. Task Description**

If the user provided a description in the arguments, use that. Otherwise,
ask:

> "What task are you working on? Please describe it in a sentence or two."

**1b. Task Type**

Based on the description, suggest a task type and confirm with the user.
Use AskUserQuestion with these options:

| Type | When to suggest |
|------|-----------------|
| Bug investigation | Description mentions a bug, issue, OCPBUGS, regression, failure, broken behavior |
| Feature development | Description mentions adding, implementing, creating new functionality |
| CI/testing | Description mentions CI, Prow, test failures, promotion, job configuration |
| Documentation | Description mentions docs, writing, documenting, guide |
| Analysis/review | Description mentions reviewing, analyzing, investigating (without a specific bug), understanding |

**1c. JIRA Ticket (optional)**

Ask: "Do you have a JIRA ticket for this task? If so, paste the URL
(e.g., https://issues.redhat.com/browse/OCPBUGS-12345). Otherwise, just
say 'no'."

**1d. Related Repositories**

**Single-repo self-workspace check:** if `$WS/dev-env.yaml` has a
top-level `self:` block, this workspace wraps the repo it lives in.
Note `self.name` and `self.summary` for the Step 4 summary, then **skip
steps 1d and 1g** — no repo selection (the repo is implicit), no skill
linking (the repo's `.claude/skills/` already is the workspace's). In
step 1f, do not create PR worktrees — record any PR URL in
`related_links:` only.

**Instead of step 1e**, create an isolated worktree automatically. If the project type is `ci-testing` or `analysis`, skip worktree creation (these types don't modify code by default). Omit `branch:` and `worktree_path:` from frontmatter.

1. Derive the branch name using the same logic as multi-repo step 1e-2:
   - If JIRA was provided, extract the ticket ID and ask for a slug
   - If no JIRA, use the project folder name
   - For `bug` type, prefix with `fix/`
   - Confirm the final branch name with the user

2. Create the worktree using git:

   ```bash
   # Ensure .claude/worktrees/ is excluded from git tracking
   grep -qF '.claude/worktrees' "$WS/.git/info/exclude" 2>/dev/null \
     || echo '.claude/worktrees/' >> "$WS/.git/info/exclude"

   # Determine the default branch
   default_branch=$(git -C "$WS" symbolic-ref refs/remotes/origin/HEAD \
     2>/dev/null | sed 's|refs/remotes/origin/||')
   if [ -z "$default_branch" ]; then
     for candidate in main master; do
       if git -C "$WS" rev-parse --verify "origin/$candidate" \
         >/dev/null 2>&1; then
         default_branch="$candidate"; break
       fi
     done
   fi

   if [ -z "$default_branch" ]; then
     echo "Cannot determine default branch from origin" >&2
     exit 1
   fi

   # Create the worktree
   git -C "$WS" worktree add \
     .claude/worktrees/<branch> -b <branch> origin/$default_branch
   ```

3. Record the worktree path for the frontmatter (Step 3b):
   `branch: <branch-name>` and
   `worktree_path: $WS/.claude/worktrees/<branch>`.

4. If `git worktree add` fails, warn the user and fall back to
   edit-in-place: omit `branch:` and `worktree_path:` from frontmatter,
   and note in the Step 4 summary that isolation was not possible.

The project frontmatter uses `repos: []` and omits `worktrees:` and
`skills:`. If the user explicitly requests no worktree (e.g., "no
worktree" in the task description), skip worktree creation and omit
`branch:` and `worktree_path:`.

Ask which repos from this workspace are relevant. **Dynamically load
the repo list** from `$WS/dev-env.yaml`:

1. Read `$WS/dev-env.yaml` and extract each repo's `name` and `summary`
   fields from the `repos:` array. Also extract the top-level
   `domain:` field if present (e.g., `domain: tnf`).
2. Build AskUserQuestion options with multiSelect=true, using
   `name` as the label and `summary` as the description.
3. If `$WS/dev-env.yaml` does not exist or has no repos, skip this step
   and note that no repos are configured (the user can add them
   later by editing the project's CLAUDE.md frontmatter).

**1e. Worktree Setup**

If the project type is `feature`, `bug`, or `docs`, and repos were
selected in Step 1d:

1. Ask which repos the user plans to **modify** (vs. reference-only).
   Use AskUserQuestion with multiSelect=true, listing the repos
   selected in Step 1d:

   > "Which of these repos will you be making changes to? (The others
   > will be available for reference but won't get a worktree.)"

   If only one repo was selected in 1d, skip this question and assume
   it will be modified.

2. Derive the branch name:
   - If JIRA was provided, extract the ticket ID (e.g., `OCPEDGE-2608`)
     and ask the user for a short slug to append:
     > "Branch name will start with `<jira-id>`. Add a short slug?
     > (e.g., `multi-hypervisor` → `ocpedge-2608-multi-hypervisor`)"
   - If no JIRA, use the project folder name as the branch name
   - For `bug` type, prefix with `fix/`
     (e.g., `fix/ocpbugs-84336-port-race`)
   - Confirm the final branch name with the user

3. For each repo the user plans to modify, create a worktree:

   ```bash
   # Ensure .worktrees/ is excluded from git tracking
   grep -qF '.worktrees' "$WS/repos/<repo>/.git/info/exclude" 2>/dev/null \
     || echo '.worktrees/' >> "$WS/repos/<repo>/.git/info/exclude"

   # Determine the default branch (main or master)
   default_branch=$(git -C "$WS/repos/<repo>" symbolic-ref refs/remotes/origin/HEAD \
     2>/dev/null | sed 's|refs/remotes/origin/||')
   if [ -z "$default_branch" ]; then
     for candidate in main master; do
       if git -C "$WS/repos/<repo>" rev-parse --verify "origin/$candidate" \
         >/dev/null 2>&1; then
         default_branch="$candidate"; break
       fi
     done
   fi

   # Create the worktree
   git -C "$WS/repos/<repo>" worktree add \
     .worktrees/<branch> -b <branch> origin/$default_branch
   ```

4. Store the branch name and worktree repos for Step 3b (frontmatter).

For `ci-testing` and `analysis`: do NOT create worktrees in this step.
Worktrees for these types are handled in Step 1f (analysis-PR) or
created manually later if needed.

**1f. Additional Context (optional)**

Ask: "Any additional context? (PR URLs, Prow job URLs, related projects,
etc.) Say 'no' to skip."

**If the project type is `analysis` and the user provided a PR URL:**

Extract the repo and PR number, then create a worktree:

1. Parse repo from URL (e.g., `cluster-etcd-operator` from
   `https://github.com/openshift/cluster-etcd-operator/pull/1620`)
2. Fetch and create a worktree for the PR:

   ```bash
   grep -qF '.worktrees' "$WS/repos/<repo>/.git/info/exclude" 2>/dev/null \
     || echo '.worktrees/' >> "$WS/repos/<repo>/.git/info/exclude"
   git -C "$WS/repos/<repo>" fetch origin pull/<number>/head:pr/<number>
   git -C "$WS/repos/<repo>" worktree add .worktrees/pr/<number> pr/<number>
   ```

3. Store `branch: pr/<number>` and the repo in worktrees list.
4. Add the PR URL to `related_links:` in frontmatter.

**1g. Repo Skill Linking (optional)**

Repos may ship their own Claude Code skills in `.claude/skills/`. Surface
them in workspace autocomplete by symlinking. Skip this step entirely
(silently, no question) if no repos were selected in Step 1d.

1. Run via Bash:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/skills.py" scan <repo1> <repo2> ...
   ```

   with the repos selected in Step 1d. If the output has
   `status: "error"` or an empty `skills` array, skip this step silently
   (mention any `errors` entries briefly, but never block project
   creation).

2. Partition the scanned skills:
   - `already_present.status == "same_source"` → already linked (by
     another project). Do NOT ask about these; record them in the
     `skills:` frontmatter (step 6 below) and mention the reuse in the
     Step 4 summary.
   - `already_present.status == "collision"` → not linkable (the name is
     taken by something else at the workspace root). Exclude, and note
     in the summary: "skill `<name>` skipped — name already in use".
   - `already_present.status == "dangling"` or `null` → offer to link
     (a dangling leftover symlink is replaced automatically).
   - `conflict: true` → same skill name from multiple selected repos;
     handle in step 4 below.

3. If any offerable non-conflicted skills remain, present ONE
   AskUserQuestion with multiSelect=true: label = skill `name`,
   description = "`<description>` (from `<repo>`)". Nothing selected →
   continue without linking.

4. For each conflicted name, ask a separate single-select
   AskUserQuestion: "Skill `<name>` is provided by multiple repos —
   which one should be linked?" with one option per source repo plus
   "Skip this skill". (Only one can own the name: symlinks can't rename
   a skill, so the others stay unlinked.)

5. For each chosen skill, run via Bash:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/skills.py" link <name> <repo>
   ```

   - On `status: "error"`: report it and continue with the remaining
     skills — never abort project creation.
   - If any success output has `created_dir: true`, note for the Step 4
     summary that a session restart is needed before these skills appear
     in autocomplete (the watcher only monitors dirs that existed at
     session start).

6. Record every linked or reused skill for the frontmatter (Step 3b):

   ```yaml
   skills:
     - name: <name>
       source: <repo>
   ```

## Step 2: Generate Folder Name

Based on the gathered information:

1. If a JIRA ticket was provided, extract the ticket ID (e.g.,
   `OCPBUGS-74679`) and use it as the suggested folder name.
2. Otherwise, generate a kebab-case slug from the task description
   (e.g., "Fix kubelet start timeout after fencing" becomes
   `fix-kubelet-start-timeout`). Keep it under 40 characters.
3. **Check if `$WS/projects/<suggestion>/` already exists** using ls. If it
   does, inform the user and ask:
   - Use a different name (suggest appending `-2`, `-3`, etc.)
   - Resume the existing project instead (point them to `/workspace:resume-project`)
4. Once you have a name that doesn't conflict, present the suggestion
   and ask the user to confirm or provide an alternative:

> "I suggest naming the project folder: `<suggestion>`. Is that OK, or
> would you prefer a different name?"

## Step 3: Create Project Scaffold

Create the project directory and generate files based on the task type.

**3a. Create directory structure**

Use the Bash tool to create directories. The base is always
`$WS/projects/<folder-name>/`.

Additional subdirectories by type:

| Type | Directories |
|------|-------------|
| Bug investigation | `logs/`, `docs/` |
| Feature development | `docs/`, `patches/` |
| CI/testing | `results/`, `scripts/` |
| Documentation | `drafts/` |
| Analysis/review | `docs/` |

**3b. Generate CLAUDE.md (lean index)**

Write a **lean index** CLAUDE.md (~50-80 lines) at
`$WS/projects/<folder-name>/CLAUDE.md` using the Write tool. This file is
an index, not a document — it orients Claude on what the project is and
where to look. All detailed content goes into separate files (Step 3d).

The content MUST follow the lean template for the detected type
(see [CLAUDE.md Templates](#claudemd-templates) below).

**3c. Generate .gitignore**

Write a `.gitignore` at `$WS/projects/<folder-name>/.gitignore` with:

```
# Large files that shouldn't be committed
*.log
*.txt.gz
*.tar.gz
```

**3d. Create starter detail files**

Create type-specific starter files alongside CLAUDE.md. Use the Write
tool for each file. Every file created MUST have a corresponding row in
the CLAUDE.md Reference Files table (generated in Step 3b).

| Type | Starter files |
|------|--------------|
| Bug investigation | `investigation.md`, `ci-runs.md`, `source-code-map.md` |
| Feature development | `design.md`, `source-code-map.md` |
| CI/testing | `ci-runs.md`, `test-failures.md` |
| Documentation | `drafts.md` |
| Analysis/review | `findings.md` |

Use the templates in the [Detail File Templates](#detail-file-templates)
section below for the starter content of each file.

## Step 4: Suggest Skills and Next Steps

After creating the project, provide a summary:

1. List the files and directories created
2. If worktrees were created, list them with their paths:
   > **Worktrees created:**
   > - `$WS/repos/<repo>/.worktrees/<branch>/` → branch `<branch>`
   >
   > When working on code changes, use the worktree paths above
   > instead of the main checkout (`$WS/repos/<repo>/`).

2b. In a single-repo self-workspace with a worktree: report the
    worktree path and branch:
    > **Worktree created:**
    > - `<worktree_path>` → branch `<branch>`
    >
    > Code changes happen in this worktree. The main checkout stays
    > on its current branch for reference.

    If no worktree was created (user opted out or creation failed):
    remind that code changes happen directly in this checkout — suggest
    creating a git branch named after the project folder before starting.

3. If skills were linked in Step 1g, list them:
   > **Skills linked:**
   > - `/<name>` (from `<repo>`)
   >
   > These are available in autocomplete now — no restart needed.

   If `link` reported `created_dir: true`, say instead: "Restart the
   session to pick up the new skills (the `.claude/skills/` directory
   was just created)."
4. Suggest relevant skills based on the task type:

| Type | Skills to suggest |
|------|-------------------|
| bug | `/prow-job:analyze-test-failure`, `/prow-job:analyze-install-failure`, `/prow-job:extract-must-gather`, `/feature-dev:feature-dev` |
| feature | `/feature-dev:feature-dev`, `/pr-review-toolkit:review-pr` |
| ci-testing | `/prow-job:analyze-test-failure`, `/prow-job:analyze-install-failure`, `/prow-job:analyze-resource`, `/prow-job:extract-must-gather` |
| docs | `/feature-dev:feature-dev` |
| analysis | `/pr-review-toolkit:review-pr`, `/prow-job:analyze-test-failure`, `/feature-dev:feature-dev` |

5. Suggest concrete next steps for starting the work
6. Remind the user they can resume this project later with
   `/workspace:resume-project`
7. If the `mcp__workspace__attach` tool is available, call it with `dir` = the absolute project directory. If it is not available (Claude Code without plugin hooks), skip this.

---

## CLAUDE.md Templates

CLAUDE.md is an **index**, not a document. It has just enough to orient
Claude on what the project is and where to look. All detailed content
lives in separate files (created in Step 3d) that are loaded on demand
when resuming the project.

### Common Frontmatter

Valid `status` values: `active`, `blocked`, `done` (set only by `/workspace:close-project`).

```yaml
---
project: <folder-name>
type: <bug|feature|ci-testing|docs|analysis>
created: <YYYY-MM-DD>
last-active: <YYYY-MM-DDTHH:MM>
status: active
jira: <URL or "none">
domain: <domain name from dev-env.yaml, or omit if none>
repos:
  - <repo1>
  - <repo2>
branch: <branch-name or omit if no worktrees>
worktrees:
  - <repo1>
# worktrees: subset of repos that have active worktrees (from Step 1e)
# branch: the branch name used for all worktrees
# Omit both if no worktrees were created (ci-testing, analysis non-PR)
worktree_path: <absolute path to .claude/worktrees/<branch>>
# worktree_path: for single-repo self-workspaces only (from Step 1e
# self-repo flow). The absolute path from git worktree add.
# Omit if no worktree was created (user opted out or creation failed)
# Mutually exclusive with worktrees: (multi-repo uses worktrees:,
# self-repo uses worktree_path:)
skills:
  - name: <skill-name>
    source: <repo that provides it>
# skills: repo skills linked into $WS/.claude/skills/ (from Step 1g)
# Omit if no skills were linked
related_links:
  - <any URLs provided>
# If user provided no URLs, use: related_links: []
---
```

### Template Structure

Every project CLAUDE.md follows this structure. The total file should
be ~50-80 lines. Generate using the common frontmatter above, then
these sections in order:

1. **`# <Title>`** — from JIRA ticket or user description

2. **`## <Type> Summary`** — heading varies by type (see below).
   Write a 2-3 sentence description of the task, then a short metadata
   bullet list (Jira link, Assignee if known). NO inline investigation
   details, timelines, or findings — those go in detail files.

3. **`## Reference Files`** — table with columns `| File | Content |`.
   One row per detail file created in Step 3d. This is the manifest —
   it is how future sessions discover detail files. During the project
   lifecycle, new detail files may be created organically (e.g.,
   `adversarial-reviews.md`, `jira-comment-root-cause.md`). When
   creating a new detail file, always add a row here.

4. **`## <Plan Section>`** — type-specific heading (see below) with
   a checklist of action items. Stays in CLAUDE.md because it is
   compact and action-oriented.

5. **`## Progress`** — high-level checklist starting with
   `- [x] Project created`, then type-specific milestone items
   (see below, all unchecked). Stays in CLAUDE.md because
   `/workspace:resume-project` reads it to suggest next steps.

### Type-Specific Content

For each type below, the specification defines:
- The summary heading name
- Metadata bullets to include in the summary
- Which detail files to create (→ rows in Reference Files table)
- The plan section heading and checklist items
- The progress checklist items

**Bug Investigation** (`type: bug`)
- Summary heading: `## Bug Summary`
- Metadata: Jira, Assignee (TBD)
- Detail files: `investigation.md`, `ci-runs.md`, `source-code-map.md`
- Plan heading: `## Fix Plan`
- Plan items: Identify root cause, Determine fix approach, Implement
  fix, Test on cluster, Submit PR
- Progress: Bug details captured, Logs collected and analyzed,
  Root cause identified, Fix implemented, PR submitted

**Feature Development** (`type: feature`)
- Summary heading: `## Feature Summary`
- Metadata: Jira, Target Version (TBD)
- Detail files: `design.md`, `source-code-map.md`
- Plan heading: `## Implementation Plan`
- Plan items: Review enhancement doc, Design approach, Implement
  changes, Write tests, Submit PRs
- Progress: Design documented, Implementation started, Tests written,
  PR(s) submitted, PR(s) merged

**CI/Testing** (`type: ci-testing`)
- Summary heading: `## Test Summary`
- Metadata: Jira, CI Job(s) (TBD)
- Detail files: `ci-runs.md`, `test-failures.md`
- Plan heading: `## Test Plan`
- Plan items: Identify failing jobs, Analyze failures, Implement fixes,
  Validate CI passing
- Progress: CI jobs identified, Failures analyzed, Fixes implemented,
  CI passing

**Documentation** (`type: docs`)
- Summary heading: `## Doc Summary`
- Metadata: Jira, Target (which docs are created/updated)
- Detail files: `drafts.md`
- Plan heading: `## Outline`
- Plan items: Research and outline, Write draft, Technical review,
  Editorial review, Submit PR
- Progress: Draft written, Technical review, Editorial review,
  PR submitted

**Analysis/Review** (`type: analysis`)
- Summary heading: `## Analysis Summary`
- Metadata: Jira, Scope (what is being analyzed/reviewed)
- Detail files: `findings.md`
- Plan heading: `## Analysis Plan`
- Plan items: Define scope, Gather data, Analyze findings,
  Write recommendations
- Progress: Analysis started, Findings documented, Recommendations
  made, Actions taken

---

## Detail File Templates

Use these templates when creating starter detail files in Step 3d.
Each file should have a heading and minimal structure — enough to guide
where content goes, but not so much that it feels like boilerplate.

### `investigation.md` (bug)

```markdown
# Investigation

## Failure Analysis

_Describe the observed failure and symptoms._

## Root Cause

_Root cause goes here once identified._

## Proposed Fix

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
```

### `ci-runs.md` (bug, ci-testing)

```markdown
# CI Runs

<!-- Add a section per CI run analyzed. Template: -->
<!-- ## Run <ID> (<short description>)              -->
<!--                                                -->
<!-- **Job:** `<job name>`                           -->
<!-- **Date:** <YYYY-MM-DD>                          -->
<!--                                                -->
<!-- | Artifact | Description |                     -->
<!-- |----------|-------------|                      -->
<!--                                                -->
<!-- **Timeline:**                                   -->
<!-- ```                                             -->
<!-- <chronological events>                          -->
<!-- ```                                             -->
```

### `source-code-map.md` (bug, feature)

```markdown
# Source Code Map

| Repo | Key Path | Purpose |
|------|----------|---------|
```

When populating this file:
- For each selected repo, check `$WS/repos/<repo>/CLAUDE.md` or
  `<domain>/context/<repo>.md` for "Key paths", "Key files",
  or similar sections.
- If found, add 1-3 most relevant paths to the table.
- If not found, add the repo name with an empty path and a TODO
  comment like "TODO: fill in relevant paths".

### `design.md` (feature)

```markdown
# Design

## Architecture

_High-level design and component interactions._

## API Changes

_New or modified APIs._

## Related PRs

| PR | Repo | Status | Description |
|----|------|--------|-------------|
```

### `test-failures.md` (ci-testing)

```markdown
# Test Failures

| Test | Error | Root Cause | Fix | Status |
|------|-------|------------|-----|--------|
```

### `drafts.md` (docs)

```markdown
# Drafts

## Target Documents

| Document | Path | Status |
|----------|------|--------|

## Outline

_Document outline goes here._

## Review Notes

_Technical and editorial review feedback._
```

### `findings.md` (analysis)

```markdown
# Findings

## Scope

_What is being analyzed and why._

## Findings

_Analysis results._

## Recommendations

| # | Recommendation | Priority | Status |
|---|----------------|----------|--------|
```

---

## Important Notes

- **Use the absolute `$WS/...` form for ALL Bash commands** (see Step 0).
  Shell state does not persist between Bash tool calls, so relative paths
  break when a prior command changes the working directory.
- Always use the Write tool to create files, never echo/cat via Bash
- Use Bash tool only for `mkdir -p` to create directories
- After creating the project, briefly list what was created and what
  the user should do next
- If the user provides enough context in the initial arguments, minimize
  questions — only ask what's truly missing
- The YAML frontmatter `status` field should always start as `active`
- Use today's date for the `created` field
