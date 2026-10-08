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

The scaffolding script resolves the workspace root the same way. Pass it
explicitly (`WORKSPACE_ROOT="$WS"`) when running it, and use the absolute
`$WS/...` form for any path you read — shell state does not persist between
Bash tool calls.

## Step 1: Gather Task Information

Ask the user questions to understand what they're working on. Use the
AskUserQuestion tool for structured questions and encourage free-text
descriptions. If the arguments already give enough context, minimize
questions — only ask what's truly missing.

**1a. Task Description**

If the user provided a description in the arguments, use that. Otherwise,
ask:

> "What task are you working on? Please describe it in a sentence or two."

**1b. Task Type**

Based on the description, suggest a task type and confirm with the user.
Use AskUserQuestion with these options (the payload value is in
parentheses):

| Type | When to suggest |
|------|-----------------|
| Bug investigation (`bug`) | Description mentions a bug, issue, OCPBUGS, regression, failure, broken behavior |
| Feature development (`feature`) | Description mentions adding, implementing, creating new functionality |
| CI/testing (`ci-testing`) | Description mentions CI, Prow, test failures, promotion, job configuration |
| Documentation (`docs`) | Description mentions docs, writing, documenting, guide |
| Analysis/review (`analysis`) | Description mentions reviewing, analyzing, investigating (without a specific bug), understanding |

**1c. JIRA Ticket (optional)**

Ask: "Do you have a JIRA ticket for this task? If so, paste the URL
(e.g., https://issues.redhat.com/browse/OCPBUGS-12345). Otherwise, just
say 'no'." If a ticket was given, fetch its summary (title) for use as the
payload `title`.

**1d. Related Repositories**

**Single-repo self-workspace check:** if `$WS/dev-env.yaml` has a
top-level `self:` block, this workspace wraps the repo it lives in. Note
`self.name` and `self.summary` for the Step 4 summary, then **skip steps 1d
and 1g** — no repo selection (the repo is implicit), no skill linking (the
repo's `.claude/skills/` already is the workspace's). Send `repos: []` and
no `pr` in the payload (the script ignores both in a self-workspace); record
any PR URL in `links` only. The script creates the isolated worktree itself
(see 1e).

Otherwise ask which repos from this workspace are relevant. **Dynamically
load the repo list** from `$WS/dev-env.yaml`:

1. Read `$WS/dev-env.yaml` and extract each repo's `name` and `summary`
   fields from the `repos:` array.
2. Build AskUserQuestion options with multiSelect=true, using
   `name` as the label and `summary` as the description.
3. If `$WS/dev-env.yaml` does not exist or has no repos, skip this step
   and note that no repos are configured (the user can add them
   later by editing the project's CLAUDE.md frontmatter).

**1e. Worktree Setup**

The script creates worktrees; here you only collect the inputs.

- **Multi-repo, type `feature`, `bug` or `docs`, repos selected in 1d:** ask
  which repos the user plans to **modify** (vs. reference-only) with
  AskUserQuestion multiSelect=true (skip the question if only one repo was
  selected — assume it is modified). Send the answer as `worktree_repos`.
- **Self-workspace, type `feature`, `bug` or `docs`:** a worktree is created
  automatically.
- `ci-testing` and `analysis` get no worktree (except PR checkouts, 1f).
- If the user explicitly asks for no worktree, send `no_worktree: true`.

Branch name: if JIRA was provided, extract the ticket ID (e.g.,
`OCPEDGE-2608`) and ask for a short slug to append (`multi-hypervisor` →
`ocpedge-2608-multi-hypervisor`); with no JIRA the project folder name is
the branch; `bug` gets a `fix/` prefix (e.g.,
`fix/ocpbugs-84336-port-race`). Confirm the final name with the user and
send it as `branch`. If you skip this question, omit `branch` and the
script derives the same default (`fix/<ticket-id>`, or the folder name).
Branches must not start with `-`, contain `..` or whitespace, or end in
`.lock`; the script rejects them.

**1f. Additional Context (optional)**

Ask: "Any additional context? (PR URLs, Prow job URLs, related projects,
etc.) Say 'no' to skip." Send URLs as `links`.

**If the project type is `analysis` (multi-repo) and the user provided a PR
URL**, parse the repo and PR number (e.g., `cluster-etcd-operator` and
`1620` from `https://github.com/openshift/cluster-etcd-operator/pull/1620`)
and send `pr: {"<repo>": <number>}` plus the URL in `links`. The script
fetches `pull/<number>/head` into `pr/<number>` and adds a worktree for it.

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

6. Record every linked or reused skill for the frontmatter (the `skills` payload field in Step 3):

   ```yaml
   skills:
     - name: <name>
       source: <repo>
   ```

## Step 2: Choose the Folder Name

Preview the scaffold without writing anything. Build the payload (below)
and run with `--dry-run`:

```bash
WORKSPACE_ROOT="$WS" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/new-project.py" \
  create --dry-run <<'JSON'
{ ...payload... }
JSON
```

The returned `folder` is the script's suggestion: the JIRA ticket ID if
there is one, otherwise a kebab-case slug of the description under 40
characters, with `-2`, `-3`, ... appended if `$WS/projects/<folder>/`
already exists. If the name was bumped because of a collision, tell the
user and offer to point them at `/workspace:resume-project` for the
existing project instead.

Present the suggestion and ask the user to confirm or provide an
alternative:

> "I suggest naming the project folder: `<folder>`. Is that OK, or would you
> prefer a different name?"

The final name is sent as `folder` in Step 3 (an explicitly named folder
that already exists is an error, not renamed).

## Step 3: Create the Project

Payload — a JSON object on stdin; only `description` and `type` are
required:

| Field | Meaning |
|-------|---------|
| `description` | Task description from 1a |
| `type` | `bug`, `feature`, `ci-testing`, `docs` or `analysis` |
| `title` | H1 of the project CLAUDE.md (JIRA summary; default: first sentence of the description) |
| `jira` | Ticket URL, default `none` |
| `repos` | Repos from 1d |
| `worktree_repos` | Repos to get a worktree (1e) |
| `branch` | Confirmed branch name (1e) |
| `pr` | `{repo: number}` PR checkouts (1f) |
| `links` | Related URLs (1f) |
| `skills` | `[{"name", "source"}]` from Step 1g, linked or reused |
| `no_worktree` | `true` to skip worktrees |
| `folder` | Confirmed folder name (Step 2) |

Run the same command without `--dry-run`. The script creates
`$WS/projects/<folder>/` with the CLAUDE.md index (frontmatter, summary,
Reference Files table, plan, progress), the type-specific detail files and
subdirectories, `.gitignore`, and the git worktrees (adding `.worktrees/`
or `.claude/worktrees/` to the repo's `.git/info/exclude`).

Output: `{"status", "folder", "path", "worktrees": [{"repo", "path",
"branch"}], "errors": [...]}`. On `status: "error"`, show
`error_message` and fix the input (nothing was written for payload errors).
Worktree failures never block creation — they appear in `errors`, and the
failed worktree is simply omitted from the frontmatter. Report them in the
summary; in a self-workspace this means edit-in-place was the fallback.

Detail-file `source-code-map.md` rows start as `TODO: fill in relevant
paths`. For each selected repo, check `$WS/repos/<repo>/CLAUDE.md` or
`<domain>/context/<repo>.md` for "Key paths" / "Key files" sections and
fill in the 1-3 most relevant paths (use the Edit tool).

## Step 4: Suggest Skills and Next Steps

After creating the project, provide a summary:

1. List the files and directories created (`path` from the output)
2. If `worktrees` is non-empty, list them with their paths:
   > **Worktrees created:**
   > - `<path>` → branch `<branch>`
   >
   > When working on code changes, use the worktree paths above instead of
   > the main checkout (`$WS/repos/<repo>/`).

   For a self-workspace entry (`repo: "(self)"`):
   > **Worktree created:**
   > - `<path>` → branch `<branch>`
   >
   > Code changes happen in this worktree. The main checkout stays on its
   > current branch for reference.

   If a self-workspace project has no worktree (opted out, not applicable
   to the type, or creation failed): remind that code changes happen
   directly in this checkout — suggest creating a git branch named after
   the project folder before starting.
3. If skills were linked in Step 1g, list them:
   > **Skills linked:**
   > - `/<name>` (from `<repo>`)
   >
   > These are available in autocomplete now — no restart needed.

   If `link` reported `created_dir: true`, say instead: "Restart the
   session to pick up the new skills (the `.claude/skills/` directory was
   just created)."
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

---

## Important Notes

- All scaffolding (folder naming, CLAUDE.md, detail files, `.gitignore`,
  worktrees) lives in `scripts/new-project.py`, which the Claude Code mod UI
  also uses — do not write those files or run `git worktree add` by hand
- Pass the payload through a quoted heredoc (`<<'JSON'`) so the description
  is never interpreted by the shell
- Use the absolute `$WS/...` form for ALL Bash commands (see Step 0)
- After creating the project, briefly list what was created and what the
  user should do next
