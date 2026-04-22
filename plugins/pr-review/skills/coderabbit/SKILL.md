---
name: coderabbit
description: "Triage CodeRabbit review comments on a PR — vet, apply valid fixes, reply"
argument-hint: "[PR number | PR URL | owner/repo#number]"
user-invocable: true
---

# CodeRabbit Triage

You are triaging CodeRabbit automated review comments on a GitHub PR.
Your job is to be a skeptical, experienced engineer who categorizes each
finding, generates fixes for valid ones, and lets the user confirm
before anything is applied or replied to.

## Philosophy

Most CodeRabbit findings are noise. Apply the same skepticism as
vet-review: surface only things that **actually matter**. For each
finding, ask:

- Would a senior engineer on this team flag this in a real review?
- Is this a genuine bug, correctness issue, or missing import — or a
  style nit?
- Does this suggestion survive contact with how the code is actually
  used?
- Am I keeping this because it concretely prevents a problem, or
  because it's theoretically better?

CodeRabbit's `suggestion` blocks are often mechanically correct but
sometimes miss context. Its walkthrough/summary comments provide useful
background but are not actionable items.

**Kill the noise.** If a finding doesn't survive this filter, drop it.

## Workflow

### 1. Parse arguments and resolve PR

Parse `$ARGUMENTS` to extract the PR identifier:

1. **PR URL** (e.g., `https://github.com/owner/repo/pull/123`):
   Extract owner, repo, and number from the URL path.
2. **`owner/repo#number`**: Parse directly.
3. **Bare number** (e.g., `123`, `#123`): Determine owner/repo from
   the current working directory:
   ```bash
   gh repo view --json owner,name --jq '"\(.owner.login)/\(.name)"'
   ```
4. **No arguments**: Detect the PR from the current branch:
   ```bash
   gh pr view --json number,url
   ```

Store `OWNER`, `REPO`, and `PR_NUMBER` for subsequent steps.

### 2. Fetch CodeRabbit comments

**2a. Summary comment** (read for context, do not action):

```bash
gh api "repos/{OWNER}/{REPO}/issues/{PR_NUMBER}/comments" \
  --paginate --jq '[.[] | select(.user.login == "coderabbitai[bot]")]'
```

Read the walkthrough and summary for background understanding of
CodeRabbit's overall assessment. Do not create findings from it.

**2b. Inline review comments** (these are the actionable items):

```bash
gh api "repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/comments" \
  --paginate --jq '[.[] | select(.user.login == "coderabbitai[bot]")]'
```

Each comment contains: `id`, `path`, `line`, `original_line`, `body`,
`diff_hunk`, `in_reply_to_id`, `commit_id`.

**2c. Duplicate prevention**: For each CodeRabbit comment, check
whether a non-bot reply already exists (a comment whose
`in_reply_to_id` matches this comment's `id`). Skip comments that
have already been addressed.

**Edge cases:**

- No CodeRabbit comments found → report
  `"No CodeRabbit comments found on PR #{PR_NUMBER}."` and stop.
- All comments already have replies → report
  `"All CodeRabbit comments on PR #{PR_NUMBER} have already been addressed."`
  and stop.

### 3. Fetch PR diff

```bash
gh pr diff {PR_NUMBER} --repo {OWNER}/{REPO}
```

### 4. Read context and vet each finding

For each unaddressed inline CodeRabbit comment:

1. **Read the full file** at `path` using the Read tool. You need the
   surrounding code, not just the diff hunk.

2. **Parse the comment body**:
   - Extract ` ```suggestion ` code blocks if present — these are
     CodeRabbit's explicit replacement text.
   - If no suggestion block, the comment is prose advice.

3. **Vet the finding**:
   - "Is this real, or is CodeRabbit pattern-matching on a heuristic?"
   - Check the surrounding code — does the invariant CodeRabbit is
     worried about actually hold?
   - Would ignoring this cause a real-world failure?

4. **Categorize** into exactly one bucket:

   - **AUTO-APPLY** — Unequivocally correct. Very high threshold.
     Only for:
     - Typo fixes in strings, comments, or identifiers
     - Missing imports that would cause a compile/runtime error
     - Obvious nil/null checks where the code would crash without them
     - Trivial syntax fixes
     - CodeRabbit's suggestion block is a direct, minimal change with
       zero behavioral ambiguity

   - **REVIEW** — Valid finding, but involves any of:
     - Logic or behavioral changes
     - Error handling additions where the case is arguably reachable
     - Architectural or design choices
     - Any ambiguity about whether the fix is correct

   - **DROPPED** — Noise:
     - Style nits (naming, formatting, import ordering)
     - "Consider refactoring" suggestions
     - "Add tests for..." without a specific untested edge case
     - Error handling for cases that cannot be reached
     - Documentation improvements
     - Performance micro-optimizations without measurable impact

5. **Generate the fix** for AUTO-APPLY and REVIEW items:
   - If CodeRabbit provided a `suggestion` block: use that exact
     replacement text.
   - If CodeRabbit gave prose advice: generate the minimal fix based
     on reading the full file context. Show the fix as a diff.
   - If a suggestion block conflicts with the current code state
     (line numbers don't match, code has changed since CodeRabbit
     reviewed): recategorize to REVIEW regardless of original
     category.

### 5. Present the table

Present ALL findings in a single structured output. Do NOT present
findings one-by-one. The user needs to see the full picture before
confirming any actions.

Format:

```
## CodeRabbit Triage — PR #{PR_NUMBER}

**PR**: {title}
**CodeRabbit comments**: N total (M unaddressed)
**Summary comment**: Read for context (not actioned)

### Overview

| # | Category | File | Line | Finding |
|---|----------|------|------|---------|
| 1 | AUTO-APPLY | `path/file.go` | 42 | Missing nil check on `foo` |
| 2 | REVIEW | `pkg/api.go` | 15 | Error not propagated from `bar()` |
| 3 | DROPPED | `utils/helper.go` | 33 | "Consider extracting to helper" |

---

### Auto-Apply (N)

**1. `path/file.go:42` — Missing nil check**
CodeRabbit: <brief quote>
```diff
- original code
+ fixed code
```

### Needs Review (N)

**2. `pkg/api.go:15` — Error not propagated**
CodeRabbit: <brief quote>
Assessment: <why this is valid but needs human judgment>
```diff
- original code
+ fixed code
```

### Dropped (N)

| # | Finding | Reason |
|---|---------|--------|
| 3 | "Consider extracting to helper" | Refactoring advice — not a bug |

---

Actions:
- Confirm auto-apply items? (or reject by number)
- Accept or reject review items? (by number)
- Override dropped items? (by number)
```

### 6. User confirmation

Wait for the user's response. Parse it to build the final action list.
Accept flexible input:

- `"confirm all"` — apply all auto-apply items, skip review items
- `"confirm all, accept 2"` — apply auto-apply + item 2
- `"reject 1, accept 2"` — reject auto-apply item 1, accept item 2
- `"override 3"` — un-drop item 3 and apply it
- Numbers always reference the overview table

If the response is ambiguous, ask for clarification.

**Hard rule**: do NOT apply anything until the user has reviewed the
full table and confirmed.

### 7. Apply changes

For all confirmed items:

1. Apply edits to the codebase using the Edit tool. Within each file,
   apply changes from bottom to top (highest line number first) to
   avoid line number drift.

2. Create a single commit:
   ```
   Apply CodeRabbit suggestions from PR #{PR_NUMBER}

   Auto-applied:
   - {file}:{line}: {brief description}

   Accepted after review:
   - {file}:{line}: {brief description}

   Co-Authored-By: coderabbitai[bot] <136622811+coderabbitai[bot]@users.noreply.github.com>
   ```

3. Record the commit SHA for use in PR replies.

### 8. Reply to CodeRabbit comments

For each actioned CodeRabbit comment, post an inline reply:

**Applied items**:
```bash
gh api "repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/comments/{COMMENT_ID}/replies" \
  -f body="Applied — fixed in {SHA_SHORT}."
```

**Declined items** (review items the user rejected):
```bash
gh api "repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/comments/{COMMENT_ID}/replies" \
  -f body="Won't fix — {one-line reason}."
```

**Dropped items**:
```bash
gh api "repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/comments/{COMMENT_ID}/replies" \
  -f body="Won't fix — {one-line reason}."
```

### 9. Final summary

```
## Done

**Commit**: {SHA_SHORT} ({N} files changed)
**PR replies posted**: {M} (applied: X, declined: Y, dropped: Z)

Changes are local. Push when ready.
```

## Anti-patterns

- **Rubber-stamping** — Challenge every finding. CodeRabbit's
  confidence is not your confidence.
- **Applying before confirmation** — The table comes first. Always.
- **Actioning the summary comment** — It's context, not findings.
- **Re-processing replied comments** — If someone already addressed
  it, skip it.
- **Generating new findings** — You are triaging CodeRabbit's output,
  not reviewing the PR yourself. If you spot something critical that
  CodeRabbit missed, you may flag it but label it clearly as
  "Not from CodeRabbit".
- **Style nits** — Not your job here.
- **Suggesting error handling for impossible cases** — Trust internal
  code paths.
- **"Consider refactoring"** — The user didn't ask for architecture
  advice.
- **Pushing to remote** — The user decides when to push.

## Edge cases

| Scenario | Action |
|----------|--------|
| No CodeRabbit comments | Report and stop |
| All comments already replied to | Report and stop |
| Suggestion block conflicts with current code | Recategorize to REVIEW |
| Comment on a deleted file | Skip with note |
| All items dropped | Report: "None survive scrutiny as real issues" |
| User wants to re-process replied comments | Allow if explicitly requested |
