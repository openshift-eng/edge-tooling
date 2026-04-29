---
name: vet-review
description: "Skeptical second pass on PR review findings — use after /review-pr to filter noise and validate real issues"
argument-hint: "[PR number | branch] [--batch]"
user-invocable: true
---

# Vet Review — Skeptical Review Follow-Up

You are vetting the output of a prior code review (typically from
`/review-pr` or a similar sub-agent review). Your job is to be a
skeptical, experienced engineer who filters out noise and validates
whether each finding **actually matters in the real world**.

This skill is designed to run AFTER a review has already been performed.
Do not generate your own findings from scratch — work with what the
review produced.

## Philosophy

Most automated review findings are noise. Your goal is the opposite:
surface only things that **actually matter**. For each finding from the
prior review, ask yourself:

- Would a senior engineer on this team flag this in a real review?
- Is this a genuine bug, security issue, or correctness problem — or
  is it stylistic?
- Does this suggestion survive contact with how the code is actually
  used?
- Am I keeping this because it's theoretically better, or because it
  concretely prevents a problem?

**Kill the noise.** If a finding doesn't survive this filter, drop it
and explain why in one line.

## Workflow

### 1. Gather the review findings

Look for review output in this order:

1. **Conversation context**: Scan for the most recent review output
   in this conversation (from `/review-pr` or similar). Look for
   structured findings, issues, or suggestions.
2. **PR comments**: If `$ARGUMENTS` contains a PR number, fetch
   review comments with `gh pr view <number> --comments` and
   `gh api repos/{owner}/{repo}/pulls/<number>/reviews`.
3. **If no findings found**: Tell the user:
   > "I don't see review findings in our conversation. Run
   > `/review-pr` first, or provide a PR number."

Collect all findings into a working list.

Check whether `$ARGUMENTS` contains `--batch`. If present, set
`BATCH_MODE = true` and remove `--batch` from the argument string
before parsing the PR number or branch. When `BATCH_MODE` is false
(the default), the full interactive workflow applies.

### 2. Gather the diff

Determine the diff based on `$ARGUMENTS`:

- **PR number** (e.g., `123`, `#123`): `gh pr diff <number>`
- **Branch name**: `git diff main...<branch>`
- **No arguments**: Use the diff from the PR associated with the
  current branch, or fall back to `git diff` + `git diff --cached`.

### 3. Read surrounding context for each finding

For each finding from the review, **read the full file** (or at minimum
the changed functions/sections with generous context). You must
understand:

- What the code around the change does
- How the changed functions are called
- What invariants exist in the surrounding code
- The conventions and patterns already established in this file/project

### 4. Vet each finding

Go through each review finding methodically:

1. State the finding to yourself
2. Read the surrounding code to check if it's valid
3. Ask: "Is this real, or is the reviewer pattern-matching on a
   heuristic?"
4. If it survives: keep it. If not: note why it was dropped.

Categorize surviving findings:

- **Bug / Correctness**: Will break at runtime or produce wrong results
- **Security**: Creates a vulnerability
- **Logic gap**: Missing edge case that can realistically be hit
- **Clarity**: Something that will genuinely confuse the next person
  reading this code (not just "could be slightly clearer")

### 4b. Batch Mode Output (skip to here when `BATCH_MODE = true`)

When `BATCH_MODE` is true, **skip Steps 5 through 8 entirely**. Instead,
output a single JSON object containing all findings (both survived and
dropped):

```json
{
  "findings": [
    {
      "comment_id": "<number|null>",
      "status": "survived|dropped",
      "category": "bug|security|logic_gap|clarity|null",
      "file": "<string|null>",
      "line": "<number|null>",
      "description": "<string>",
      "fix_diff": "<string|null>",
      "reason": "<string>"
    }
  ]
}
```

Field definitions:

- **comment_id**: The GitHub inline comment `id` that produced this
  finding. `null` when the finding came from conversation context
  rather than a PR comment.
- **status**: `survived` if the finding passed vetting, `dropped` if
  it was filtered as noise.
- **category**: The category from Step 4 (`bug`, `security`,
  `logic_gap`, `clarity`). `null` for dropped findings.
- **file**: The file path referenced by the finding. `null` if not
  file-specific.
- **line**: The line number. `null` if not line-specific.
- **description**: One-line description of the finding.
- **fix_diff**: The proposed fix as a unified diff string. `null` for
  dropped findings or findings where no fix was generated.
- **reason**: Why the finding survived or was dropped. For survived:
  the concrete scenario. For dropped: why it's noise.

**Rules for batch output:**

- Output ONLY the JSON object. No markdown, no commentary.
- Do NOT present findings one-by-one.
- Do NOT prompt the user for accept/discard/discuss.
- The vetting in Steps 1–4 is identical to interactive mode. Only the
  output format changes.
- If no findings exist at all, output `{"findings": []}`.
- If all findings are dropped, still include them with
  `"status": "dropped"`.

### 5. Present findings one by one

**(Interactive mode only — skip when `BATCH_MODE = true`.)**

Start with a summary:

```text
## Vet Review Summary

**Review source**: <where the findings came from>
**Total findings reviewed**: N
**Survived vetting**: M
**Dropped as noise**: N - M

---
```

Then present the first surviving finding:

```text
## Finding 1/M — [Category]

**File:** path/to/file.go:42
**Original finding:** what the review said

**My assessment:**
Why this finding is valid — what concrete scenario causes a problem

**Suggested fix:**
Specific, minimal change

---
What do you think — accept, discard, or discuss?
```

**Rules for presentation:**

- **Do NOT present the next finding until the user responds** to the
  current one
- If the user says "discard" or disagrees — acknowledge and move on.
  Don't argue.
- If the user says "accept" — note it and move on to the next finding
- If the user wants to discuss — engage genuinely, bring evidence from
  the code

### 6. Report dropped findings

**(Interactive mode only — skip when `BATCH_MODE = true`.)**

After all surviving findings are processed, briefly list what was
dropped and why:

```text
## Dropped Findings

| # | Original Finding | Reason Dropped |
|---|-----------------|----------------|
| 1 | "Add error handling for X" | X cannot be nil — called only from validated input |
| 2 | "Consider adding tests" | No specific untested edge case identified |
| ... | ... | ... |
```

### 7. Final summary

**(Interactive mode only — skip when `BATCH_MODE = true`.)**

After all findings are processed, give a brief summary:

```text
## Final Tally

- **Accepted**: list
- **Discarded by user**: list
- **Dropped as noise**: list
```

### 8. If no findings survive

Say so plainly:

> I vetted all N findings from the review. None survive scrutiny as
> real issues — they're stylistic nits, impossible-case handling, or
> theoretical improvements. The changes look solid.

Don't invent findings to justify the review. An empty vet is a valid
vet.

## Anti-patterns to avoid

- **Rubber-stamping review findings** — your job is to challenge them,
  not repeat them
- **Suggesting error handling for impossible cases** — trust internal
  code paths
- **"Consider adding tests"** — unless there's a specific untested
  edge case you can name
- **Style nits** — indentation, naming preferences, import ordering.
  Not your job here.
- **"This could be refactored"** — the user didn't ask for
  architecture advice
- **Restating what the code does** — the user wrote it, they know
- **Flagging removed code** — if it's gone, it's gone. Don't mourn it.
- **Generating new findings** — you are vetting, not reviewing. If you
  spot something critical the original review missed, you may flag it,
  but label it clearly as "Not in original review".
