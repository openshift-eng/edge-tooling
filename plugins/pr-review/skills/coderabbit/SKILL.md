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
sometimes miss context. Its walkthrough/summary comments are mostly
noise, but occasionally contain substantive findings that don't appear
as inline comments — these should be surfaced and vetted.

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

**2a. Summary comment** (read for context; extract actionable findings):

```bash
gh api "repos/{OWNER}/{REPO}/issues/{PR_NUMBER}/comments" \
  --paginate --jq '[.[] | select(.user.login == "coderabbitai[bot]")]'
```

Read the walkthrough and summary for background understanding.

**Then parse for actionable findings.** CodeRabbit sometimes posts
substantive findings only in the summary — cross-cutting issues,
missing artifacts, or whole-PR concerns that don't map to a single
diff line. These appear as bulleted or numbered items under headings
like "Actionable comments", "Additional comments", or similar
sections. Extract each discrete finding that:

- Identifies a concrete bug, missing piece, or correctness issue
- Is not just a walkthrough description of what the PR does
- Is not a duplicate of an inline comment

Tag each extracted finding with `SOURCE: summary`. These findings
will not have a `COMMENT_ID`, `path`, or `line` — record the
`ISSUE_COMMENT_ID` (the `id` of the issue comment they came from)
for use in the reply step.

**Most summary content is walkthrough noise — apply the same
skepticism as inline findings.** Only surface items that would
survive the vet filter in Step 4.

**2b. Review body** (nitpicks and other non-inline findings):

```bash
gh api "repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/reviews" \
  --paginate --jq '[.[] | select(.user.login == "coderabbitai[bot]")] | .[].body'
```

CodeRabbit nests nitpick findings inside the review body rather than
posting them as standalone inline comments. These appear in
`<details>` blocks under headings like `🧹 Nitpick comments (N)`.
Each nitpick typically includes a file path, line number, description,
and sometimes a proposed diff.

Parse the review body for discrete findings:

- Look for `<summary>🧹 Nitpick comments` sections
- Extract each finding with its file path and line reference
- Ignore meta-sections: "Prompt for AI Agents", "Autofix",
  "Review info" — these are not findings
- Skip any finding that duplicates an inline comment (Step 2c)

Tag each extracted finding with `SOURCE: review-body`. These
findings have file/line context (unlike summary findings) but no
individual `COMMENT_ID` for inline replies.

**2c. Inline review comments** (primary line-level actionable items):

```bash
gh api "repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/comments" \
  --paginate --jq '[.[] | select(.user.login == "coderabbitai[bot]")]'
```

Each comment contains: `id`, `path`, `line`, `original_line`, `body`,
`diff_hunk`, `in_reply_to_id`, `commit_id`.

**2d. Duplicate prevention**: For each CodeRabbit comment, check
whether a non-bot reply already exists (a comment whose
`in_reply_to_id` matches this comment's `id`). Skip comments that
have already been addressed.

**Edge cases:**

- No findings from any source (inline, review-body, summary) →
  report `"No CodeRabbit comments found on PR #{PR_NUMBER}."` and stop.
- No inline comments BUT review-body or summary findings exist →
  continue with those findings. Report:
  `"No CodeRabbit inline comments found. Triaging N finding(s) from review body/summary."`
- All inline comments already have replies AND no other findings →
  report `"All CodeRabbit comments on PR #{PR_NUMBER} have already been addressed."`
  and stop.
- All inline comments already have replies BUT review-body or summary
  findings exist → continue with those findings.

### 3. Fetch PR diff

```bash
gh pr diff {PR_NUMBER} --repo {OWNER}/{REPO}
```

### 4. Read context and vet each finding

For each unaddressed inline CodeRabbit comment:

1. **Read the full file** at `path` using the Read tool. You need the
   surrounding code, not just the diff hunk.

For each review-body-sourced finding (`SOURCE: review-body`):

1. **Read the full file** at the referenced path. These findings
   include file/line context, so treat them like inline findings
   for vetting purposes.

For each summary-sourced finding (`SOURCE: summary`):

1. **Identify affected files** from the finding's description and
   the PR diff. Read the relevant files for context. If the finding
   is about a missing artifact (e.g., missing docs, missing tests),
   verify it is actually missing.

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
**CodeRabbit comments**: N inline (M unaddressed), J review-body, K summary

### Overview

| # | Category | Source | File | Line | Finding |
|---|----------|--------|------|------|---------|
| 1 | AUTO-APPLY | inline | `path/file.go` | 42 | Missing nil check on `foo` |
| 2 | REVIEW | inline | `pkg/api.go` | 15 | Error not propagated from `bar()` |
| 3 | REVIEW | review-body | `cmd/main.go` | 88 | Parenthetical contradicts new behavior |
| 4 | REVIEW | summary | — | — | No migration docs for schema change |
| 5 | DROPPED | inline | `utils/helper.go` | 33 | "Consider extracting to helper" |

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

**3. `cmd/main.go:88` — Parenthetical contradicts new behavior** (review-body)
CodeRabbit: <brief quote from nitpick>
Assessment: <why this is a genuine inconsistency>
```diff
- old wording
+ fixed wording
```

**4. (summary) No migration docs for schema change**
CodeRabbit: <brief quote from summary>
Assessment: <why this is valid — e.g., schema change confirmed in diff but no migration guide found>
Affected files: `db/migrations/0042_add_column.sql`

### Dropped (N)

| # | Finding | Reason |
|---|---------|--------|
| 5 | "Consider extracting to helper" | Refactoring advice — not a bug |

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

**For inline-sourced findings**, post an inline reply:

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

**For review-body and summary-sourced findings**, post a top-level
issue comment (since there is no inline comment to reply to):

```bash
gh api "repos/{OWNER}/{REPO}/issues/{PR_NUMBER}/comments" \
  -f body="Re: CodeRabbit summary finding — {brief description}

{Applied — fixed in {SHA_SHORT}. | Won't fix — {one-line reason}.}"
```

If multiple review-body/summary findings were actioned, batch them
into a single issue comment to avoid noise:

```bash
gh api "repos/{OWNER}/{REPO}/issues/{PR_NUMBER}/comments" \
  -f body="Addressed CodeRabbit summary findings:

- {finding 1}: Applied — fixed in {SHA_SHORT}.
- {finding 2}: Won't fix — {one-line reason}."
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
- **Treating summary walkthrough as findings** — Walkthrough
  descriptions of what the PR does are context, not findings. Only
  extract discrete actionable items (bugs, missing pieces,
  correctness issues) from the summary.
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
| No findings from any source | Report and stop |
| No inline comments but review-body/summary findings exist | Triage non-inline findings only |
| All inline comments replied to, no other findings | Report and stop |
| All inline comments replied to, review-body/summary findings exist | Triage non-inline findings only |
| Suggestion block conflicts with current code | Recategorize to REVIEW |
| Comment on a deleted file | Skip with note |
| Review-body/summary finding duplicates an inline comment | Use inline version, skip duplicate |
| All items dropped | Report: "None survive scrutiny as real issues" |
| User wants to re-process replied comments | Allow if explicitly requested |
