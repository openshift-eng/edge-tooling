# Skill Quality Guidelines

Quality standards for SKILL.md files in the edge-tooling plugin marketplace. These guidelines are enforced by `scripts/lint-skills.py` (automated checks) and CodeRabbit (PR review).

## Linter Quick Reference

Errors block PRs. Warnings are advisory.

| ID | Check | Severity | What it validates |
|----|-------|----------|-------------------|
| E001 | `frontmatter-exists` | error | YAML frontmatter between `---` markers exists |
| E002 | `name-present` | error | `name` field present and non-empty |
| E003 | `description-present` | error | `description` field present and non-empty |
| E004 | `description-not-placeholder` | error | Description is not a TODO/placeholder from template |
| E005 | `body-not-empty` | error | Skill body below frontmatter has content |
| E006 | `description-length` | error | Description ≤ 1024 characters |
| E007 | `line-count-max` | error | File ≤ 1000 lines |
| E008 | `name-format` | error | `name` is lowercase alphanumeric + hyphens (plus optional `plugin:skill` colon), max 64 chars, no leading/trailing/consecutive hyphens |
| E009 | `name-matches-dir` | error | `name` (or skill segment after colon) matches parent directory name |
| W001 | `description-quality` | warning | Description does not start with filler phrases ("A skill that...", "This plugin...") |
| W002 | `line-count` | warning | File ≤ 500 lines |
| W003 | `has-examples` | warning | Body contains Examples/Synopsis/Usage section |
| W004 | `allowed-tools` | warning | `allowed-tools` frontmatter is specified (user-invocable skills only) |
| W005 | `has-steps` | warning | Body contains Steps/Workflow section |
| W006 | `argument-hint` | warning | If `$ARGUMENTS` is used, `argument-hint` is set |
| W007 | `plugin-dir-usage` | warning | Absolute paths suggest using `$PLUGIN_DIR` |
| W008 | `side-effect-guard` | warning | Destructive skills have safety guards |
| W009 | `description-intent-mismatch` | warning | Description claims read-only but body has destructive actions |
| W010 | `credential-pattern` | warning | Hardcoded credential patterns found in body |
| W011 | `orchestrator-edge-cases` | warning | Orchestrator skills have an Edge Cases section |
| W012 | `description-too-short` | warning | Description under 20 characters |

Run locally:

```bash
make lint-skills              # lint changed SKILL.md files
scripts/lint-skills.py --check-all-files        # lint all skills
```

## Frontmatter

Every SKILL.md starts with YAML frontmatter between `---` markers.

### Required Fields

| Field | Purpose | Constraints |
|-------|---------|-------------|
| `name` | Slash command identifier | Max 64 chars. Lowercase letters, numbers, and hyphens only. No leading/trailing/consecutive hyphens. Must match parent directory name. Claude Code extension: may include a `plugin:skill` colon separator (e.g., `two-node:bug-reproducer`). |
| `description` | Agent uses this to decide when to invoke the skill | Max 1024 chars. See [Description Quality](#description-quality). |

### Optional Fields (Agent Skills Spec)

These fields are defined by the [Agent Skills specification](https://agentskills.io/specification) and are portable across any agent that supports the format.

| Field | When to use | Example |
|-------|-------------|---------|
| `license` | Skill has a specific license | `Apache-2.0` or `Proprietary. LICENSE.txt has complete terms` |
| `compatibility` | Skill requires specific tools or environment | `Requires oc, podman, and access to the internet` |
| `metadata` | Additional key-value metadata (author, version) | `metadata:\n  author: edge-team\n  version: "1.0"` |
| `allowed-tools` | Limit what tools the skill can use (experimental) | `Read Bash Agent` |

### Recommended Fields (Claude Code Extensions)

These fields are Claude Code-specific extensions not part of the base Agent Skills spec. They control Claude Code's skill registration and invocation behavior.

| Field | When to use | Example |
|-------|-------------|---------|
| `user-invocable` | Always (defaults to `false` if omitted) | `true` or `false` |
| `allowed-tools` | Always for user-invocable skills | `Read, Bash, Agent` |
| `argument-hint` | When skill accepts `$ARGUMENTS` | `"OCPBUGS-XXXXX"` |
| `disable-model-invocation` | Non-user-invocable skills with side effects | `true` |

### Description Quality

The description is the most important field. Agents read it to decide when to invoke the skill. Front-load the use case and keep it under 1024 characters (the [Agent Skills spec limit](https://agentskills.io/specification#description-field)).

**Good descriptions** — start with an action verb or "Use when..." and specify the trigger context:

```yaml
# action verb + specific context
description: Reproduce an OpenShift bug on a TNA (arbiter) or TNF (fencing) cluster

# "Use when..." pattern
description: Use when analyzing sprint health — capacity at the start, risks mid-sprint, or retrospective input at the end

# specific trigger
description: Adversarial examination of the hypothesis currently under discussion
```

**Negative triggers** — for skills with overlapping domains, add what the skill is NOT for:

```yaml
description: "Use when analyzing sprint health — capacity at the start, risks mid-sprint, or retrospective input at the end. Not for release health or individual Jira issue lookups"
```

> **Note:** The frontmatter parser does not support YAML block scalars (`>-`, `|`). Use a single quoted or unquoted line for descriptions.

**Bad descriptions** — vague, generic, or starting with "A skill that...":

```yaml
description: "A skill that helps with things"
description: "This skill does stuff"
description: "TODO: add description"
```

**Intent alignment** — the description must accurately reflect the skill's actual capabilities. If the skill deploys clusters, don't describe it as "read-only analysis." The linter checks for this mismatch (W009).

### Tool Restrictions

Specify `allowed-tools` to limit what the skill can do. This reduces blast radius and makes the skill's capabilities explicit to reviewers.

```yaml
# Read-only analysis skill
allowed-tools: Read, Glob, Grep, Bash

# Skill that spawns sub-agents and asks questions
allowed-tools: Agent, AskUserQuestion, Write, Read, Bash

# Skill with MCP integration
allowed-tools: Agent, Read, Bash, mcp__plugin_mcp-atlassian_mcp-atlassian__jira_search
```

Omitting `allowed-tools` grants the skill access to all tools — acceptable only for simple, low-risk skills (like `hello-world`).

**Permission minimality** — `allowed-tools` should be the minimum set needed. Reviewers should verify no unused tools are listed. Skills that invoke `Bash` should always declare it explicitly — skills bundling executable scripts are significantly more likely to have issues.

## Structure

### Recommended Sections

Skill complexity determines which sections to include.

**Simple skills** (~20-50 lines) — steps and done:

```markdown
# skill-name
Description paragraph.
## Usage
How to run it.
```

Example: `hello-world`

**Standard skills** (~100-200 lines) — steps, output format, rules:

```markdown
# skill-name
Description paragraph.
## Steps
1. Step one
2. Step two
## Output Structure
Expected output format.
## Rules
Constraints and invariants.
## Examples
Concrete usage examples.
```

Example: `challenge`

**Orchestrator skills** (~250-500 lines) — configuration, phased workflow, agents:

```markdown
# skill-name
Description and execution model.
## Configuration
Constants, field IDs, board IDs.
## Execution Model
Phase overview, rules, agent roles.
## Workflow
### Step 0: Setup
### Phase 1: Data Fetch
### Phase 2: Analysis (sub-agent)
### Step 3: Report
## Edge Cases
Failure modes and how they're handled.
## Critical Rules
Safety constraints, file boundaries, destructive operation guards.
```

Examples: `sprint-health`, `bug-reproducer`

### Output Format

Standard and orchestrator skills should define their expected output structure. A concrete template is more reliable than prose description.

Example from `challenge`:

```markdown
## Output Structure
# Adversarial Challenge: <short hypothesis label>
**Date**: <YYYY-MM-DD>
## Counter-Arguments
### CA-1: <Title>
**Severity**: FATAL | MAJOR | MINOR
**Resolving experiment**: <specific command or test>
```

### Gotchas

The highest-value content in any skill. Document environment-specific corrections to mistakes the agent will make — concrete, testable facts that aren't obvious from the code.

Example from `bug-reproducer`:

```markdown
- **OVN arbiter bug**: If monitoring detects OVN CrashLoopBackOff
  on arbiter topology, warn user this is a known OVN issue,
  not the target bug
```

Bad gotchas explain things the agent already knows ("YAML uses indentation"). Good gotchas save 30 minutes of debugging a subtle environment quirk.

### Line Count

Keep SKILL.md under 500 lines. If a skill exceeds this:

- Move reference material to separate files in the skill directory (e.g., `dfd-elements-lvms.md` alongside `SKILL.md`)
- Move domain knowledge to a `references/` directory in the plugin
- Split into an orchestrator skill + sub-agent skills (see [Orchestration](#orchestration))

**Progressive disclosure** — only metadata (name + description, ~50-100 tokens) is loaded at session start. The full body (~500-1000 tokens) is injected only when the skill activates. Reference files are loaded only when the skill explicitly reads them. Front-load the most critical instructions; defer detailed reference material to separate files with conditional triggers:

```markdown
> Read `references/api-errors.md` if the API returns a non-200 status code.
```

## Instructions

### Actionable Steps

Write steps that Claude can execute without ambiguity:

- Number all steps and phases
- Specify exact commands, tool calls, or file paths
- Define clear stop conditions ("If X, stop with: error message")
- Specify expected output format

**Good** — from `bug-reproducer`:

```markdown
**Check 2: Inventory**
Read `inventory.ini` (at `$TNT_DEPLOY_DIR/inventory.ini`). Extract the EC2 IP
from the `[metal_machine]` group. If no valid IP found, stop with:
> No EC2 IP found in inventory.ini. Run `make inventory` first.
```

**Bad** — vague, no stop condition:

```markdown
Check the inventory file and get the IP address.
```

### Content Litmus Test

For every line in a SKILL.md, ask: **"Would the agent get this wrong without this instruction?"** If removing the line wouldn't cause Claude to make a mistake, cut it.

- Don't explain what the agent already knows (HTTP, YAML syntax, how `grep` works)
- Don't present menus of equivalent options — pick one default, mention alternatives briefly
- Write procedures, not declarations: "Read the schema, join on the FK convention" beats "Join orders to customers on customer_id" (the latter breaks when the schema changes)

### Variable Usage

Use runtime variables for portability:

| Variable | Purpose |
|----------|---------|
| `$PLUGIN_DIR` | Absolute path to the installed plugin directory |
| `$ARGUMENTS` | Arguments passed after the slash command |

Reference plugin files via `$PLUGIN_DIR`:

```bash
!`bash "${PLUGIN_DIR}/command.sh" --name "${ARGUMENTS}"`
```

When referencing files relative to the repo root (e.g., `plugins/edge-scrum/references/`), this is acceptable — these are repo-relative paths, not hardcoded absolutes.

## Safety

### Side Effects

Skills that modify state (deploy clusters, write files, run destructive commands) must:

1. **Declare side effects explicitly** — state "read-write" or "read-only" in a Notes section
2. **Require user confirmation** before destructive operations (use `AskUserQuestion`)
3. **Define file safety boundaries** — list which files the skill MAY and MUST NOT modify

Example from `bug-reproducer`:

```markdown
## TNT Repository File Safety
**Files the skill MAY modify:**
- `roles/dev-scripts/install-dev/files/config_arbiter.sh`
**Files the skill MUST NOT modify:**
- `inventory.ini`
- Any playbook
```

### Credential Safety

- Never log, print, or embed credentials in output
- Check that credentials are set without revealing values:

  ```bash
  [ -n "$JIRA_API_TOKEN" ] && echo "SET" || echo "MISSING"
  ```

- Use environment variables, never hardcoded tokens

### Auto-Invocation Safety

If a skill has side effects and is NOT user-invocable (Claude invokes it automatically), set:

```yaml
disable-model-invocation: true
```

This prevents Claude from running the skill without explicit user action.

## Orchestration

For complex workflows, skills orchestrate sub-agents in phases.

### Pattern

1. Skill gathers input and configuration (main context)
2. Skill fetches data inline or via MCP (main context)
3. Skill spawns sub-agents with substituted `{VARIABLE}` placeholders
4. Agents write results to JSON files in a shared `$WORKDIR`
5. Skill reads agent output, applies guard checks, and assembles final result

### Guard Checks

Between phases, the orchestrator must validate agent output before proceeding:

```markdown
**After agent completes**, read `$WORKDIR/result.json`:
- If `status` is `success`, proceed to next phase.
- If `status` is `failed`, show error and ask user what to do.
```

### Work Directory

Use `/tmp/` for ephemeral work directories. Clean up after completion:

```bash
WORKDIR="$(mktemp -d /tmp/skill-name-XXXXXXXX)"
```

## Testing Your Skill

Skills currently lack standardized testing frameworks. At minimum, document these for each non-trivial skill:

1. **Manual test invocation** — a concrete `/skill-name <args>` command a teammate can run
2. **Expected input/output** — what the skill should produce given known inputs
3. **Known failure modes** — what breaks and how the skill handles it

For higher confidence, test with and without the skill to measure the delta it provides — does the skill actually improve Claude's output for the target task?

## Description Optimization

A skill only helps if it gets activated. The `description` field carries the entire burden of triggering — if it doesn't convey when the skill is useful, the agent won't invoke it.

### Writing for triggering

- **Use imperative phrasing** — "Use when..." rather than "This skill does..."
- **Focus on user intent** — describe what the user is trying to achieve, not the skill's internal mechanics
- **Be specific but not narrow** — list contexts where the skill applies, including cases where the user doesn't name the domain directly
- **Stay under 1024 characters** — long enough to cover scope, short enough to not bloat startup context

### Testing trigger accuracy

For non-trivial skills, test whether your description triggers on the right prompts. Create ~20 eval queries (half should-trigger, half should-not) and run them against the agent:

- **Should-trigger queries**: vary phrasing (formal/casual), explicitness (names domain vs. describes need), and complexity
- **Should-not-trigger queries**: focus on near-misses that share keywords but need something different

The most valuable test cases are ones where the skill would help but the connection isn't obvious from the query alone — these are where description wording makes the difference.

For a structured approach with train/validation splits and iterative optimization, see [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions).

## Anti-Patterns

| Anti-pattern | Why it's bad | Fix |
|-------------|-------------|-----|
| TODO markers in shipped skills | Signals incomplete work | Remove or implement |
| Description starting with "A skill that..." | Wastes description space on filler words | Start with action verb |
| Description says "read-only" but body deploys | Intent-description mismatch (W009) | Align description with actual capabilities |
| Hardcoded absolute paths | Breaks portability across installations | Use `$PLUGIN_DIR` |
| Hardcoded credentials or token patterns | Security risk, breaks across environments | Use environment variables |
| Missing `allowed-tools` on Bash-running skill | Unlimited blast radius | Specify minimum tool set |
| Over 500 lines in one SKILL.md | Claude may ignore parts | Split into orchestrator + agents |
| No edge cases section on orchestrator | Silent failures in production | Add Edge Cases section |
| Embedding raw API responses in context | Wastes context window | Persist to files, read selectively |
| Over-explaining what the agent knows | Wastes context tokens on HTTP, YAML, etc. | Cut anything the agent wouldn't get wrong without |
| Presenting menus instead of defaults | Agent decision paralysis | Pick one default, mention alternatives briefly |
| Declarations instead of procedures | "Join on customer_id" breaks when schema changes | "Read schema, join on FK convention" |

## Quality Checklist

For PR reviewers — verify these before approving a new or updated skill:

- [ ] Frontmatter has `name`, `description`, `user-invocable`
- [ ] Description is specific, action-oriented, and accurately reflects capabilities
- [ ] Description includes negative triggers for overlapping domains
- [ ] `allowed-tools` is the minimum set needed (for non-trivial skills)
- [ ] Steps are numbered and actionable
- [ ] Output format is specified (for standard/orchestrator skills)
- [ ] Stop conditions are defined for error paths
- [ ] Edge cases are documented (for orchestrator skills)
- [ ] No TODO markers remain
- [ ] No hardcoded credentials or token patterns
- [ ] File is under 500 lines
- [ ] Side effects are declared and guarded
- [ ] Passes `scripts/lint-skills.py <path>`
