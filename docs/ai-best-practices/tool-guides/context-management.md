# Context Management

[← AI Best Practices](../README.md)

Context is a finite resource. As the context window fills, model performance degrades — not catastrophically, but as a gradient of reduced precision for information retrieval and long-range reasoning. This guide covers how to manage context effectively across all AI tools.

## Core Concept: Context Engineering

Context engineering is the discipline of curating the optimal set of tokens during LLM inference. The goal: **find the smallest set of high-signal tokens that maximize the likelihood of your desired outcome.**

Every token introduced depletes the model's attention budget. The transformer architecture creates pairwise relationships between all tokens — as context grows, these relationships get stretched thin.

## Context Costs by Feature

Understanding what consumes context helps you make informed tradeoffs:

| Feature | When It Loads | Context Cost |
|---------|---------------|--------------|
| AGENTS.md / CLAUDE.md | Every request | **High** — full content loaded each time. Keep under 200 lines. |
| Skills (descriptions) | Session start | Low — just name + description metadata |
| Skills (full content) | When invoked | Medium — SKILL.md + reference files loaded on demand |
| MCP servers (schemas) | First tool use | Low until a tool is actually called |
| Sub-agents | When spawned | **Isolated** — separate context window, returns summary only |
| Hooks | On trigger | **Zero** — runs externally, no context cost unless output is added |
| Memory | When recalled | Low — loaded selectively based on relevance |

**Key insight:** AGENTS.md costs the most because it loads into every request. Move detailed reference material into skills (loaded on-demand) to keep AGENTS.md lean.

## Just-in-Time Data Loading

Rather than pre-loading all relevant data into context, use just-in-time retrieval:

- **Maintain lightweight identifiers** (file paths, stored queries, web links) instead of full data objects.
- **Load data at runtime** using tools when it's actually needed.
- **Progressive disclosure** — agents incrementally discover relevant context through exploration. Each interaction yields context that informs the next decision.

This mirrors how humans work — we don't memorize entire codebases. We organize information and retrieve it on demand.

**Apply this principle to:**

- **AGENTS.md** — keep it to overview, commands, and conventions. Don't embed detailed reference material.
- **Skills** — read reference files at execution time, not in the SKILL.md body. Use `Read` tool to load files from `references/` directories.
- **Agents** — give agents the tools to find what they need rather than pre-loading everything into their prompt.

## Compaction

When a conversation nears the context window limit, compaction summarizes the history and continues with the summary plus recent files.

**When to compact:**

- Long sessions with many tool calls
- Before starting a new phase of work in the same session
- When you notice Claude losing track of earlier decisions

**How to compact effectively:**

- Use `/compact` with a focused instruction: `/compact "Focus on the auth refactoring decisions and remaining TODOs"`
- The instruction guides what to preserve vs. discard

**What compaction preserves:**

- Architectural decisions
- Unresolved bugs and TODOs
- Implementation details for in-progress work
- Recent file contents (last 5 accessed files)

**What compaction discards:**

- Old tool call outputs already consumed
- Exploratory searches that didn't lead anywhere
- Verbose error logs from resolved issues

## Sub-agent Architectures

Sub-agents are the primary mechanism for context efficiency. Each sub-agent runs in an isolated context window and returns only a condensed summary.

**How it works:**

1. Main agent coordinates with a high-level plan
2. Sub-agents perform deep technical work in their own context (may use tens of thousands of tokens)
3. Each sub-agent returns a distilled summary (typically 1,000–2,000 tokens)
4. Main agent synthesizes results without ever seeing the raw exploration

**When to use sub-agents:**

- Codebase exploration and search
- Multi-file analysis
- Research and documentation lookups
- Any task where the raw output would bloat the main context
- Parallel independent tasks

**Pattern:**

```text
Main context: Coordinate, decide, edit
├── Sub-agent A: Explore module X → returns 1,500-token summary
├── Sub-agent B: Research API docs → returns 800-token summary
└── Sub-agent C: Analyze test coverage → returns 1,200-token summary
```

## Structured Note-Taking

For long-running tasks, write notes to persistent storage outside the context window:

- Use TODO files, NOTES.md, or structured JSON to track progress
- Read notes back after context compaction to restore state
- Maintain critical context and dependencies that would otherwise be lost

**Three-layer memory model:**

1. **Short-term** — conversation context, managed by compaction
2. **Extended thinking** — reasoning blocks, managed separately
3. **Long-term** — persistent files (memory, notes), never cleared by compaction

## Memory Best Practices

Claude Code's memory system stores information across sessions. Use it well:

**Do:**

- Store task-relevant patterns, debugging techniques, and architectural decisions
- Use clear, descriptive file names and directory structure
- Periodically review and clean up stale memory
- Scope memory per-project when possible

**Don't:**

- Store secrets, API keys, or PII in memory
- Let memory grow unbounded
- Store raw conversation history (store the insight, not the chat)
- Trust memory without verification — it may be stale

**Security:** Memory files are read back into context, creating a potential prompt injection vector. Treat stored content as data, not directives.

## Practical Tips

- **Run `/clear` between unrelated tasks.** Mixed-topic sessions cause confusion and waste context.
- **Use `/btw` for quick questions** that shouldn't persist in history.
- **After 2+ failed corrections, start fresh.** Clear context and write a better initial prompt rather than accumulating failed approaches.
- **Build tooling incrementally.** Start with AGENTS.md, add skills when you find yourself repeating prompts, then MCP when you need external data, then sub-agents when context bloat becomes a problem.
- **Match features to goals:** AGENTS.md for "always do X" rules, skills for reusable workflows, MCP for external services, sub-agents for isolated work, hooks for deterministic automation.

## References

- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — Anthropic's guide to context as a finite resource
- [Claude Code context costs](https://code.claude.com/docs/en/features-overview#understand-context-costs) — per-feature cost breakdown
- [Claude Code best practices](https://code.claude.com/docs/en/best-practices) — managing context, verification, and scaling
- [Tool use memory cookbook](https://platform.claude.com/cookbook/tool-use-memory-cookbook) — memory management patterns
