# Building AI Tools

[← AI Best Practices](../README.md)

This guide covers best practices for building Claude Code plugins, skills, hooks, MCP servers, and agents for the OpenShift Edge team. Each tool type serves a different purpose — start here to pick the right one, then follow the detailed guide.

## Decision Matrix

| I want to... | Use a... | Guide |
|--------------|----------|-------|
| Automate a repeatable, multi-step workflow | [Skill](skills.md) | User-invocable or auto-triggered |
| React to an event (session start, tool call, etc.) | [Hook](hooks.md) | Transparent, event-driven |
| Validate or guard tool usage | [Hook](hooks.md) | Blocks or modifies actions |
| Expose an external API to Claude | [MCP Server](mcp-servers.md) | Persistent service integration |
| Package related skills, hooks, and agents for distribution | [Plugin](plugins.md) | Container for components |
| Run a focused, isolated subtask within a skill | [Agent](agents.md) | Spawned on demand |
| Guarantee JSON schema conformance from model output | [Structured Outputs](structured-outputs.md) | API-level schema enforcement |
| Reduce hallucinations or increase output consistency | [Guardrails](guardrails.md) | Verification loops, screening |

## Comparison

| Aspect | Skill | Hook | Plugin | MCP Server | Agent |
|--------|-------|------|--------|------------|-------|
| **Trigger** | User invokes (`/name`) or auto-detected from description | Event-driven (SessionStart, PreToolUse, etc.) | N/A (container for other components) | Tool call from Claude | Spawned by skill or user |
| **Scope** | Single workflow, possibly multi-step | Single event response | Multiple related components | API integration layer | Isolated subtask |
| **Complexity** | Low–Medium | Low | Medium–High | Medium–High | Low–Medium |
| **User-facing** | Yes (slash command) | No (transparent) | Indirect (through its components) | Indirect (through tool calls) | No (spawned programmatically) |
| **State** | Conversation context | Stateless (per event) | Stateless (container) | Persistent process | Isolated context |
| **Communication** | Direct conversation | JSON on stdin/stdout | N/A | Tool call/response | JSON files in `{WORKDIR}` |

## Shared Design Principles

These apply regardless of which tool type you're building.

**Deterministic scripts for deterministic work.** If a step has a known, repeatable algorithm (fetch data, transform JSON, validate fields), write it as a shell or Python script. Reserve LLM processing for analysis, synthesis, and natural language tasks. Pre-made utility scripts are more reliable than generated code, save tokens, and ensure consistency.

**Single responsibility.** Each tool does one job. A skill that creates epics should not also analyze sprint health. A hook that checks submodule freshness should not also lint code.

**Composability.** Tools should work together through well-defined interfaces, not embed each other. A skill spawns agents; it doesn't copy-paste agent logic into itself. A plugin packages components; it doesn't duplicate functionality from another plugin.

**Fail loudly.** No silent fallbacks that mask errors. If a hook blocks an action, explain why. If an MCP server can't reach its API, return an error — don't return empty data. If a skill can't find required input, ask the user rather than guessing.

**Just-in-time data loading.** Don't pre-load everything into context. Maintain lightweight identifiers (file paths, queries, links) and load data at runtime when needed. This applies to skills (read reference files on demand), agents (give tools to find data, don't embed it), and AGENTS.md (keep it lean, defer to skills for detail). See [Context Management](../tool-guides/context-management.md#just-in-time-data-loading).

**Test before publishing.** Validate plugins with the plugin validator. Test skills manually before adding to the marketplace. Verify hooks don't block legitimate operations. Use the "Claude A / Claude B" pattern — author with one session, test in a clean session.

**API wrappers are not skills.** If the "skill" just calls a single API or tool, it adds overhead without value. Use the tool directly. Skills are for multi-step workflows that involve LLM reasoning, not thin wrappers around existing tools.

## Orchestration Pattern

The most common pattern for complex workflows in this repo:

```text
Skill (orchestrator)
├── Gathers input and configuration
├── Runs deterministic scripts for data fetching/transformation
├── Spawns Agent 1 (background) ──→ writes results to {WORKDIR}/output-1.json
├── Spawns Agent 2 (background) ──→ writes results to {WORKDIR}/output-2.json
├── Spawns Agent 3 (background) ──→ writes results to {WORKDIR}/output-3.json
├── Reads agent outputs
└── Synthesizes final result
```

Key elements:

- Skill controls the overall flow
- Agents run in parallel for independent subtasks
- Communication happens through JSON files in a shared work directory (`{WORKDIR}`)
- Deterministic transforms happen in scripts, not LLM calls

See `microshift-ci:doctor` and `edge-scrum:release-health` for real examples of this pattern.

## Naming Conventions

- **Skills:** Use colon-based namespacing — `<domain>:<action>` (e.g., `microshift-ci:doctor`, `edge-scrum:create-epic`)
- **Plugins:** Lowercase, hyphenated (e.g., `edge-scrum`, `microshift-ci`)
- **Agents:** Descriptive names matching their purpose (e.g., `bug-analyzer`, `epic-fetcher`)
- **Hooks:** Named by what they check, not when they run (e.g., `update-submodules.sh`, `detect-new-tools.sh`)

## Commit Conventions

Follow the repo's commit format when working on AI tools:

```text
feat(plugins): add cluster-health plugin
fix(plugins): correct field mapping in epic-fetcher agent
docs(plugins): update skill description for sprint-health
```

## References

- [Skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — Anthropic's official guide
- [MCP specification](https://modelcontextprotocol.io) — Model Context Protocol standard
- [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — Context as a finite resource
