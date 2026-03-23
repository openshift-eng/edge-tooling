---
description: Create a new project workspace for a development task
argument-hint: [description]
---

# New Project Workspace

You are helping a developer create a new project workspace. Projects live
under the `projects/` directory and provide structured working environments
for specific tasks (bug investigations, feature development, CI work, etc.).

Everything after "new" in `$ARGUMENTS` is an optional initial description
of the task.

## Step 1: Gather Task Information

Ask the user questions to understand what they're working on. Use the
AskUserQuestion tool for structured questions and encourage free-text
descriptions.

**1a. Task Description**

If the user provided a description after "new" in the arguments, use that.
Otherwise, ask:

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

Ask which repos from this workspace are relevant. **Dynamically load
the repo list** from `dev-env.yaml` at the workspace root:

1. Read `dev-env.yaml` and extract each repo's `name` and `summary`
   fields from the `repos:` array.
2. Build AskUserQuestion options with multiSelect=true, using
   `name` as the label and `summary` as the description.
3. If `dev-env.yaml` does not exist or has no repos, skip this step
   and note that no repos are configured (the user can add them
   later by editing the project's CLAUDE.md frontmatter).

**1e. Additional Context (optional)**

Ask: "Any additional context? (PR URLs, Prow job URLs, related projects,
etc.) Say 'no' to skip."

## Step 2: Generate Folder Name

Based on the gathered information:

1. If a JIRA ticket was provided, extract the ticket ID (e.g.,
   `OCPBUGS-74679`) and use it as the suggested folder name.
2. Otherwise, generate a kebab-case slug from the task description
   (e.g., "Fix kubelet start timeout after fencing" becomes
   `fix-kubelet-start-timeout`). Keep it under 40 characters.
3. **Check if `projects/<suggestion>/` already exists** using ls. If it
   does, inform the user and ask:
   - Use a different name (suggest appending `-2`, `-3`, etc.)
   - Resume the existing project instead (point them to `/project:resume`)
4. Once you have a name that doesn't conflict, present the suggestion
   and ask the user to confirm or provide an alternative:

> "I suggest naming the project folder: `<suggestion>`. Is that OK, or
> would you prefer a different name?"

## Step 3: Create Project Scaffold

Create the project directory and generate files based on the task type.

**3a. Create directory structure**

Use the Bash tool to create directories. The base is always
`projects/<folder-name>/`.

Additional subdirectories by type:

| Type | Directories |
|------|-------------|
| Bug investigation | `logs/`, `docs/` |
| Feature development | `docs/`, `patches/` |
| CI/testing | `results/`, `scripts/` |
| Documentation | `drafts/` |
| Analysis/review | `docs/` |

**3b. Generate CLAUDE.md**

Write the CLAUDE.md file at `projects/<folder-name>/CLAUDE.md` using the
Write tool. The content MUST follow the template for the detected type
(see [CLAUDE.md Templates](#claudemd-templates) below).

**3c. Generate .gitignore**

Write a `.gitignore` at `projects/<folder-name>/.gitignore` with:

```
# Large files that shouldn't be committed
*.log
*.txt.gz
*.tar.gz
```

## Step 4: Suggest Skills and Next Steps

After creating the project, provide a summary:

1. List the files and directories created
2. Suggest relevant skills based on the task type:

| Type | Skills to suggest |
|------|-------------------|
| bug | `/prow-job:analyze-test-failure`, `/prow-job:analyze-install-failure`, `/prow-job:extract-must-gather`, `/feature-dev:feature-dev` |
| feature | `/feature-dev:feature-dev`, `/pr-review-toolkit:review-pr` |
| ci-testing | `/prow-job:analyze-test-failure`, `/prow-job:analyze-install-failure`, `/prow-job:analyze-resource`, `/prow-job:extract-must-gather` |
| docs | `/feature-dev:feature-dev` |
| analysis | `/pr-review-toolkit:review-pr`, `/prow-job:analyze-test-failure`, `/feature-dev:feature-dev` |

3. Suggest concrete next steps for starting the work
4. Remind the user they can resume this project later with
   `/project:resume`

---

## CLAUDE.md Templates

All generated CLAUDE.md files start with YAML frontmatter for machine
readability, followed by type-specific sections.

### Common Frontmatter

```yaml
---
project: <folder-name>
type: <bug|feature|ci-testing|docs|analysis>
created: <YYYY-MM-DD>
status: active
jira: <URL or "none">
repos:
  - <repo1>
  - <repo2>
related_links:
  - <any URLs provided>
# If user provided no URLs, use: related_links: []
---
```

### Template Structure

Every project CLAUDE.md follows this structure. Generate the full
markdown using the common frontmatter above, then these sections in
order:

1. **`# <Title>`** — from JIRA ticket or user description
2. **`## <Type> Summary`** — heading varies by type (see below),
   followed by the user's description and metadata bullet list
3. **Type-specific middle sections** — unique to each type (see below)
4. **`## Progress`** — checklist starting with `- [x] Project created`,
   then type-specific items (see below, all unchecked)
5. **`## Related Source Code`** — table with columns: Repo, Key Path,
   Purpose (populate from repo context files, or leave as TODO)
6. **`## Suggested Skills`** — populate from the type-to-skill mapping
   in Step 4

### Type-Specific Content

**Bug Investigation** (`type: bug`)
- Summary heading: `## Bug Summary`
- Metadata: Jira, Priority (TBD), Component, Affected Version (TBD),
  Assignee (TBD)
- Sections: `## Attachments` (file/description table),
  `## Timeline` (code block for event reconstruction),
  `## Investigation Findings`, `## Root Cause`,
  `## Fix Plan` (checklist: identify root cause, determine approach,
  implement fix, test on cluster, submit PR)
- Progress: Bug details captured, Logs collected and analyzed,
  Root cause identified, Fix implemented, PR submitted

**Feature Development** (`type: feature`)
- Summary heading: `## Feature Summary`
- Metadata: Jira, Target Version (TBD), Enhancement (link if applicable)
- Sections: `## Design Notes` (with `### Architecture` and
  `### API Changes` subsections),
  `## Implementation Plan` (checklist: review enhancement doc, design
  approach, implement changes, write tests, submit PRs),
  `## Related PRs` (PR/repo/status/description table)
- Progress: Design documented, Implementation started, Tests written,
  PR(s) submitted, PR(s) merged

**CI/Testing** (`type: ci-testing`)
- Summary heading: `## Test Summary`
- Metadata: Jira, CI Job(s), Test Suite
- Sections: `## CI Job Links` (job/status/link table),
  `## Test Failures` (with `### Failure Analysis` table:
  test/error/root cause/fix), `## Scripts`
- Progress: CI jobs identified, Failures analyzed, Fixes implemented,
  CI passing

**Documentation** (`type: docs`)
- Summary heading: `## Doc Summary`
- Metadata: Jira, Target (which docs are created/updated)
- Sections: `## Target Documents` (document/repo path/status table),
  `## Review Notes`
- Progress: Draft written, Technical review, Editorial review,
  PR submitted

**Analysis/Review** (`type: analysis`)
- Summary heading: `## Analysis Summary`
- Metadata: Jira, Scope (what is being analyzed/reviewed)
- Sections: `## Findings`, `## Recommendations`
- Progress: Analysis started, Findings documented, Recommendations made,
  Actions taken

---

## Important Notes

- Always use the Write tool to create files, never echo/cat via Bash
- Use Bash tool only for `mkdir -p` to create directories
- After creating the project, briefly list what was created and what
  the user should do next
- If the user provides enough context in the initial `/project:new`
  arguments, minimize questions — only ask what's truly missing
- The YAML frontmatter `status` field should always start as `active`
- Use today's date for the `created` field
- When populating the "Related Source Code" table:
  - For each selected repo, check `repos/<repo>/CLAUDE.md` or
    `presets/*/context/<repo>.md` for "Key paths", "Key files",
    or similar sections
  - If found, add 1-3 most relevant paths to the table
  - If not found, add the repo name with an empty path and a TODO
    comment like "TODO: fill in relevant paths"
