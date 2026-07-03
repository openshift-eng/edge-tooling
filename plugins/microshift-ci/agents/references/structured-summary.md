# Structured Summary Output Format

Output contract for CI job analysis skills, consumed by `aggregate.py`, `search-bugs.py`, and `create-report.py`.

## Output Template

```text
Error Severity: {1-5}
Stack Layer: {AWS Infra | External Infrastructure | build phase | deploy phase | test setup phase | Test Configuration | test | teardown}
Step Name: {CI step where the error occurred}
Error: {Exact error with log context}
Causal Chain: {numbered list, each link cites file:line}
Confidence: {high | medium | low}
Suggested Remediation: {fix direction; do NOT propose test tolerance (waits/retries/timeouts) unless the product behaved correctly}
```

| Severity | Meaning |
|---|---|
| 5 | Release-blocking product regression — no workaround |
| 4 | Persistent product or test failure — no workaround |
| 3 | Persistent failure with workaround, or scoped to single scenario/arch |
| 2 | Intermittent failure / likely flake |
| 1 | Infrastructure noise or self-healing condition |

## STRUCTURED SUMMARY JSON

Append after all prose. **Both markers are required** — the parser skips the report if either is missing.

```text
--- STRUCTURED SUMMARY ---
[ { ... } ]
--- END STRUCTURED SUMMARY ---
```

### Fields

| Field | Description |
|---|---|
| `severity` | 1-5 per rubric above |
| `stack_layer` | One of the values from the template |
| `step_name` | CI step where the error occurred |
| `error_signature` | Concise one-line description for dedup and bug titles |
| `root_cause` | WHY it failed — mechanism, not symptom (~80 chars, see rules below) |
| `raw_error` | Verbatim log text — deterministic anchor (see rules below) |
| `infrastructure_failure` | `true` if AWS/CI infra caused it, `false` otherwise |
| `job_url` | Full Prow job URL |
| `job_name` | Full job name |
| `release` | Release branch (e.g. `4.22`, `main`) |
| `remediation` | Fix direction (~120 chars). Infra → infra action. Product → code fix direction |
| `finished` | Job finish date, `YYYY-MM-DD` |
| `causal_chain` | Array of `{"cause", "evidence", "quote"}`. `evidence` = artifact file path with `:line`. `quote` = verbatim excerpt, no labels/commentary. **Re-read every cited file:line before finalizing** — wrong citations destroy trust. The `cause` text must use terms from actual log messages, not vague categories |
| `confidence` | `high` / `medium` / `low` (see rules below) |
| `analysis_gaps` | Array of strings naming missing evidence. Empty `[]` when nothing skipped |
| `scenarios` | Array of scenario names where this failure occurred. Empty `[]` for non-scenario jobs |

### CONFIDENCE rules

- **high**: every causal-chain link directly evidenced by a quoted artifact line or graph
- **medium**: mechanism is inferred but consistent with all evidence; citations still required — `medium` means the *interpretation* is inferred, not that citations can be omitted
- **low**: symptom-level only — chain stops before actionable cause; `analysis_gaps` MUST be populated

Do NOT inflate confidence — downstream automation acts on it.

### RAW_ERROR rules

Used for deterministic grouping. Two runs on the same job MUST produce the same value.

1. **Copy-paste exact error text** — do NOT paraphrase
2. **Pick ONE error** — the first fatal one
3. **Only strip timestamps** — keep everything else verbatim
4. **Never concatenate** multiple errors
5. **Truncate to ~150 chars** if very long — keep the distinctive part

### ROOT_CAUSE rules

Used alongside RAW_ERROR for cross-release deduplication. Same underlying problem across releases MUST produce the same ROOT_CAUSE.

| Field | Purpose |
|---|---|
| `error_signature` | WHAT failed (bug titles) |
| `root_cause` | WHY it failed (dedup) |
| `raw_error` | Verbatim log text (deterministic anchor) |

1. **~80 chars max** — short enough for token matching
2. **Focus on mechanism**, not symptom
3. **Consistent across releases** — same problem = same text
4. **Stable terms** — no version numbers, timestamps, or job names

Describe the specific mechanism, not architectural generalizations ("framework expects annotation X which MicroShift does not set", not "MicroShift is single-node").

### Multiple independent failures

1. One entry per independent failure (different scenarios, different root causes)
2. Same root cause = one entry — do NOT split
3. At most 5 entries per job
4. Cascading failures are NOT independent — report only the root failure
5. Single failures are still a JSON array
