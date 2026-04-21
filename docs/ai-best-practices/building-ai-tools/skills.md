# Skills

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

Skills are reusable, multi-step workflows written in Markdown that guide Claude through a task. They are the primary abstraction for encoding team processes, domain knowledge, and repeatable procedures into AI-assisted automation.

## When to Use

Use a skill when the workflow:

- Is **repeatable** — you find yourself giving the same multi-step instructions across sessions
- Requires **LLM reasoning** — analysis, synthesis, natural language interpretation, or judgment calls
- Benefits from **structure** — clear phases, validation steps, or defined output formats
- Needs **domain context** — team conventions, field mappings, or process rules that aren't obvious from the code

**Don't use a skill when:**

- A shell script suffices — if every step is deterministic with no LLM reasoning, write a script
- It's a single tool call — wrapping one MCP call or one bash command in a skill adds overhead without value
- It should be automatic — if the workflow should run without user invocation (on session start, before a tool call), use a [hook](hooks.md) instead

## Anatomy

Skills live in plugin directories:

```text
plugins/<plugin-name>/skills/<skill-name>/SKILL.md
```

### YAML Frontmatter

Every SKILL.md starts with YAML frontmatter that controls discovery and behavior:

```yaml
---
name: edge-scrum:create-epic
description: Use when creating a new Epic in Jira for the OpenShift Edge team — enforces team conventions for required fields, description template, sizing, and parent linkage from Edge Scrum Laws
allowed-tools: mcp__atlassian__jira_create_issue, mcp__atlassian__jira_get_issue, mcp__atlassian__jira_search, AskUserQuestion, Read
user-invocable: true
---
```

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | Yes | Unique identifier. Use `<plugin>:<skill>` namespacing for plugin skills. |
| `description` | Yes | Controls auto-triggering and helps users find the skill. Must be specific. |
| `user-invocable` | Yes | `true` if the user can call it with `/name`. `false` for sub-skills invoked only by other skills. |
| `allowed-tools` | No | Restricts which tools the skill can use. Omit to allow all tools. |
| `argument-hint` | No | Shows usage hint (e.g., `<release1,release2,...>`). |

**Good frontmatter:**

```yaml
---
name: microshift-ci:doctor
argument-hint: <release1,release2,...>
description: Analyze CI for multiple MicroShift releases and produce an HTML summary
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---
```

This description is specific: "analyze CI", "multiple MicroShift releases", "HTML summary". Claude can match user intent accurately.

**Bad frontmatter:**

```yaml
---
name: ci-helper
description: Helps with CI stuff
user-invocable: true
---
```

This description is vague. "Helps with CI stuff" matches too many intents and will trigger incorrectly.

### Body Structure

The Markdown body uses phases to structure the workflow. Phases give Claude a clear execution path.

```markdown
# Skill Name

Brief description of what the skill does and when to use it.

> **Before proceeding**: Read reference files for domain context.

## Configuration
Static values (field IDs, project keys, board IDs) that the skill needs.

## Phase 1: Gather
Collect inputs, read configuration, fetch data.

## Phase 2: Analyze
Process data, run scripts, spawn agents for parallel work.

## Phase 3: Output
Format results, validate output, present to user.
```

Reference files provide domain context without bloating the SKILL.md:

```markdown
> **Before proceeding**: Read `plugins/edge-scrum/references/Edge-Scrum-Laws.md`
> to identify which law files apply, then read those files.
```

This is just-in-time data loading — context is loaded when the skill runs, not when the session starts.

## Do's

**Write accurate descriptions with specific verbs.** The description is the primary mechanism for auto-triggering. Use concrete verbs ("analyze", "create", "generate") and specific nouns ("CI jobs", "Jira epic", "HTML summary"). Include both what the skill does and when to use it.

```yaml
# Good — specific, covers what and when
description: Use when analyzing sprint health — capacity at the start, risks mid-sprint, or retrospective input at the end

# Bad — vague, no context for when to trigger
description: Sprint stuff
```

**Constrain scope with `allowed-tools`.** Limit the skill to only the tools it needs. `edge-scrum:create-epic` restricts to Jira MCP tools, `AskUserQuestion`, and `Read` — it cannot accidentally run shell commands or modify files.

**Use scripts for deterministic steps.** If a step has a known algorithm (fetch data, transform JSON, aggregate results), write a shell or Python script. Reserve LLM processing for analysis and synthesis. `microshift-ci:doctor` uses deterministic scripts for data collection, artifact download, aggregation, and HTML generation. LLM agents handle only root cause analysis and Jira bug correlation.

**Structure with clear phases.** Number your phases. Each phase should have a clear input, action, and output. `edge-scrum:sprint-health` uses: gather configuration, fetch data via MCP, transform with scripts, delegate analysis to sub-agents, synthesize results.

**Reference real examples.** Point to existing skills when documenting patterns:

- `microshift-ci:doctor` — orchestration with parallel agents, deterministic scripts for data pipeline
- `edge-scrum:sprint-health` — sub-skill routing (capacity, midpoint, retro analyzers)
- `edge-scrum:create-epic` — enforces team conventions with constrained tool access

## Don'ts

**Don't wrap single tool calls.** If the skill just runs one MCP call or one bash command, it adds overhead without value. Use the tool directly.

**Don't embed complex logic in Markdown.** Markdown is for instructions and flow control. Deterministic algorithms belong in scripts under `plugins/<plugin>/scripts/`. The SKILL.md orchestrates; scripts execute.

**Don't write vague descriptions.** "Helps with CI" or "does sprint things" will auto-trigger on the wrong inputs. Be specific about the action and the context.

**Don't skip frontmatter.** Without frontmatter, the skill has no name, no description for auto-triggering, and no tool constraints. It becomes invisible and uncontrolled.

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|-------------|---------|-----|
| **Kitchen-sink skill** | One skill tries to handle every case in a domain. Too broad to trigger accurately, too complex to maintain. | Split into focused skills. `edge-scrum:sprint-health` routes to three sub-skills (capacity, midpoint, retro) rather than handling all three inline. |
| **Duplicate functionality** | Skill reimplements what an existing MCP tool or script already does. | Check existing tools first. If `gh pr list` does the job, don't wrap it in a skill. |
| **Misleading description** | Description doesn't match actual behavior, causing false triggers or missed triggers. | Test the description: would a user searching for this workflow find it? Would unrelated queries falsely match? |
| **No error guidance** | Skill doesn't tell Claude what to do when things fail. | Add error handling instructions: "If the API returns 404, ask the user to verify the project key." |
| **Hardcoded paths** | Absolute paths to files or directories that break on other machines. | Use `${PLUGIN_DIR}` for paths relative to the skill, `${CLAUDE_PLUGIN_ROOT}` for plugin root. |

## Degrees of Freedom

Match the level of instruction specificity to the task's fragility:

| Freedom Level | When to Use | Example |
|--------------|-------------|---------|
| **High** (text instructions) | Many valid approaches; Claude can choose | "Analyze this data and present insights" |
| **Medium** (pseudocode/scripts with params) | A preferred pattern exists | "Use the template in `references/` to format the report" |
| **Low** (exact scripts, no modification) | Operations are fragile or consistency is critical | "Run exactly: `bash ${PLUGIN_DIR}/scripts/deploy.sh`" |

Use high freedom for analysis and synthesis. Use low freedom for destructive operations, data mutations, and anything where consistency matters more than creativity.

`microshift-ci:doctor` demonstrates this well: low freedom for data collection scripts (deterministic, must run exactly as written), high freedom for LLM agents doing root cause analysis (many valid interpretations).

## Template Pattern

When output format matters, provide a concrete template:

**Strict (for API responses, data formats):**

```markdown
ALWAYS use this exact structure for the report:

## Summary
[1-2 sentence overview]

## Findings
| Finding | Severity | Recommendation |
|---------|----------|----------------|
| ... | ... | ... |

## Next Steps
[Numbered action items]
```

**Flexible (for general guidance):**

```markdown
Use a sensible default format, but adjust based on the data. If there are
fewer than 3 findings, skip the table and use bullet points.
```

Strict templates are appropriate for outputs consumed by scripts or other tools. Flexible templates work for human-readable reports where rigid formatting would be awkward.

## Feedback Loops

For skills that produce artifacts (code, configs, reports), add validate-fix-repeat loops:

```markdown
## Phase 3: Validate

Run the validation script:
`bash "${PLUGIN_DIR}/scripts/validate-output.sh" ${WORKDIR}/report.json`

If validation fails, fix the errors and re-run validation. Repeat until clean.
```

**The core pattern:** run validator, fix errors, repeat until clean. This works for:

- Code generation (run linter/tests after generating)
- Data transformation (validate schema after transforming)
- Document generation (check against style guide)

Make validation scripts verbose — error messages like "Field 'signature_date' not found. Available fields: customer_name, order_total" help Claude self-correct without additional LLM reasoning about what went wrong.

## Checklist for Effective Skills

Before publishing a skill, verify:

**Core quality:**

- [ ] Description is specific, includes key terms, covers both what and when
- [ ] SKILL.md body is under 500 lines
- [ ] Additional details split into separate reference files
- [ ] Consistent terminology throughout (don't mix "API endpoint" / "URL" / "route")
- [ ] Concrete examples, not abstract descriptions
- [ ] File references are one level deep from SKILL.md
- [ ] Workflows have clear, numbered steps

**Scripts and tools:**

- [ ] Scripts solve problems rather than punt to Claude
- [ ] Explicit, helpful error messages
- [ ] Required packages listed and verified as available
- [ ] No hardcoded paths — uses `${PLUGIN_DIR}` variables

**Testing:**

- [ ] Tested with real usage scenarios
- [ ] Tested in a clean session (not the authoring session)
- [ ] Team feedback incorporated where applicable

## References

- [Skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) -- comprehensive guide
- [Template pattern](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#template-pattern)
- [Workflows and feedback loops](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#workflows-and-feedback-loops)
- [Checklist for effective skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#checklist-for-effective-skills)
