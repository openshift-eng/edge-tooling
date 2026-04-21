# AI Best Practices

Best practices for AI-assisted development on the OpenShift Edge team. This guide operationalizes Red Hat's AI policies into day-to-day engineering practices and documents our approved tools, conventions, and guardrails.

This is not a replacement for Red Hat AI policy — it's how we apply it. For full policy details, see the [Policy Reference](#policy-reference) section.

## Quick Reference

| Question | Answer |
|----------|--------|
| Is this tool approved? | Check the [AI Tools Source page]. If not listed, email usingai@redhat.com to start an AI Assessment. |
| Is this model approved? | Approved tool ≠ approved model. Each model must be explicitly approved for code assistant use. |
| Can I use confidential data? | Only with tools explicitly approved for it. Default: **public data only**. |
| Do I need to mark AI contributions? | Yes. Use `Assisted-by:` or `Generated-by:` trailers in commits/PRs for substantial AI contributions. |
| Contributing AI code upstream? | Check the upstream project's AI contribution policy first. Some projects prohibit it entirely. |
| Can I buy/subscribe to a new AI tool? | No. All tool acquisition goes through Buy@RH. No personal credit cards or P-Cards for software. |

## Contents

| Document | Description |
|----------|-------------|
| [Security Best Practices](security.md) | Data protection, input sanitization, sandboxing |
| **Tool Guides** | |
| [Claude Code](tool-guides/claude-code.md) | Setup, AGENTS.md, sub-agents, worktrees, attribution |
| [Cursor](tool-guides/cursor.md) | Model selection, rules files, features, sanitization |
| [Coderabbit](tool-guides/coderabbit.md) | PR review configuration and workflow |
| [VS Code](tool-guides/vscode.md) | AI extensions, workspace trust, multi-tool management |
| [Local Agents (Ollama)](tool-guides/local-agents.md) | Local model usage, licensing, sandboxes |
| [Context Management](tool-guides/context-management.md) | Context costs, compaction, sub-agents, JIT loading |
| **Building AI Tools** | |
| [Overview & Decision Matrix](building-ai-tools/README.md) | Which tool type to use, shared principles |
| [Skills](building-ai-tools/skills.md) | Degrees of freedom, templates, feedback loops |
| [Hooks](building-ai-tools/hooks.md) | Event types, script patterns, safety guards |
| [Plugins](building-ai-tools/plugins.md) | Structure, marketplace, distribution |
| [MCP Servers](building-ai-tools/mcp-servers.md) | Protocol architecture, transports, configuration |
| [Agents](building-ai-tools/agents.md) | Orchestration, sub-agent patterns, isolation |
| [Structured Outputs](building-ai-tools/structured-outputs.md) | JSON schema conformance for tool builders |
| [Guardrails](building-ai-tools/guardrails.md) | Hallucinations, consistency, prompt injection |

## Core Principles

These are non-negotiable. They apply to all AI tool usage regardless of tool, context, or task.

1. **Human review required** — AI output is a suggestion, not final code. You are accountable for everything you commit. If AI-generated code is outside your expertise, get a peer review from a domain expert before sharing.

2. **Approved tools and models only** — Check the [AI Tools Source page] before using any AI tool for Red Hat work. Tool-level approval does not grant blanket model approval. If a tool supports multiple models, only use models explicitly approved for code assistant use.

3. **No confidential data in unapproved tools** — Never input personal information, customer data, partner data, Red Hat confidential information, API keys, credentials, or proprietary code into any AI tool unless the tool is explicitly approved for that data class. Sanitize code snippets before sharing. Use synthetic data for development.

4. **Mark AI-generated code** — For substantial AI contributions, add attribution trailers to commit messages and PR descriptions:
   - `Assisted-by: <tool name>` — AI helped write or modify the code
   - `Generated-by: <tool name>` — AI generated the code with minimal human modification
   - `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>` — auto-added by Claude Code when it creates commits

5. **Check upstream policies** — Before contributing AI-assisted code to open-source projects, verify the project permits AI contributions and follow any marking requirements. Non-compliance is the contributor's responsibility.

For security-specific guidance, see [Security Best Practices](security.md).

## AGENTS.md Convention

The team uses [AGENTS.md](https://github.com/agentsmd/agents.md) as the canonical agent instruction file. AGENTS.md is a vendor-neutral standard (Linux Foundation / Agentic AI Foundation) supported by 23+ AI tools including Claude Code, Cursor, Codex, Copilot, and Gemini CLI.

**Our convention:** Maintain `AGENTS.md` as the source of truth for project-level agent instructions. Create `CLAUDE.md` as a symlink to `AGENTS.md` for Claude Code compatibility.

```bash
ln -s AGENTS.md CLAUDE.md
```

AGENTS.md should contain:

- Project overview and architecture
- Build, test, and lint commands
- Code style guidelines and conventions
- PR/commit format expectations
- Security considerations and sensitive areas

Keep AGENTS.md under 200 lines. Use just-in-time data loading (skills, reference files) for detailed context rather than front-loading everything. See [Context Management](tool-guides/context-management.md) for details.

## Approved Tools

| Tool | Type | Primary Use | Guide |
|------|------|-------------|-------|
| Claude Code | CLI / IDE extension | Code generation, refactoring, analysis, automation, plugin development | [Claude Code Guide](tool-guides/claude-code.md) |
| Cursor | AI IDE | AI-assisted editing, code generation, inline chat | [Cursor Guide](tool-guides/cursor.md) |
| Coderabbit | PR review service | Automated code review on pull requests | [Coderabbit Guide](tool-guides/coderabbit.md) |
| VS Code | IDE | Development environment with AI extensions | [VS Code Guide](tool-guides/vscode.md) |
| Local models (Ollama) | Local inference | Private, offline AI assistance | [Local Agents Guide](tool-guides/local-agents.md) |

For context management across all tools, see [Context Management](tool-guides/context-management.md).

## Building AI Tools

The team builds and maintains Claude Code plugins, skills, hooks, MCP servers, and agents to automate OpenShift Edge workflows. See the [Building AI Tools Guide](building-ai-tools/README.md) for:

- Decision matrix: which tool type to use for your problem
- Comparison of skills, hooks, plugins, MCP servers, and agents
- Per-tool-type best practices with do's, don'ts, and examples from this repo
- [Structured outputs](building-ai-tools/structured-outputs.md) for reliable programmatic integration
- [Guardrails](building-ai-tools/guardrails.md) for reducing hallucinations and increasing consistency

## Policy Reference

Condensed summaries of Red Hat AI policies. These summaries are for quick reference — consult the full documents on The Source for authoritative guidance.

### Policy on the Use of AI Technology

The governing policy for all AI tool usage at Red Hat. Effective June 23, 2025 (Version 2).

- All AI tools require approval via the [AI Tools Source page] or an AI Assessment (AIA) before use for company-related work.
- The AIA evaluates tools against standards for security, privacy, IP/licensing, and compliance.
- The AIA requirement applies to all AI tools — free or paid, new or existing vendor, standalone or embedded in another tool.
- Personal, confidential, customer, and partner data may not be used with any AI tool unless explicitly approved.
- New tool acquisitions must go through Buy@RH procurement. No personal credit card or P-Card purchases.
- AI augments human intelligence — it does not replace it. All AI output requires human review and validation.
- Non-compliance may result in disciplinary action up to termination.

Full document: Search "Policy on the Use of AI Technology" on The Source.

### Guidelines for Responsible Use of AI Code Assistants

Supplement to the AI Policy, specific to code assistant tools. Version 4 (July 30, 2025).

- Treat all AI-generated code as suggestions. Never blindly trust output. Thoroughly test before integrating.
- Only use AI code assistants specifically approved through an AIA. Approved tool ≠ approved model.
- Never input confidential, personal, or proprietary data. Sanitize code snippets. Use synthetic data.
- Enable code matching/similarity features when available. Comply with licenses on matched code.
- Check upstream project policies before contributing AI-assisted code.
- Mark substantial AI contributions with `Assisted-by:` or `Generated-by:` trailers in commits/PRs.
- Standard copyright/license notices remain appropriate for files with AI-generated elements, as long as they are not misleading.

Full document: Search "Guidelines for responsible use of AI code assistants" on The Source.

### AI Tools FAQs

Approval process details, pre-approved exceptions, and practical guidance. Version 33 (July 2, 2025).

- The [AI Tools Source page] lists already-approved tools. If your tool is not listed, email usingai@redhat.com.
- Using AI for general search with only public data (comparable to Google Search) does not require pre-approval.
- Open source tools that are not models and don't use unapproved external APIs don't need an AIA — but the models used with them still do.
- Red Hat prefers models under genuine open-source licenses (e.g., Apache-2.0 Granite and Mistral models). Models with restrictive licenses (e.g., Meta Llama) require AIA review.
- Sandbox environments (models.corp, MOSAIC Platform) are available at no cost for experimentation.
- For paid tools, start with a Buy@RH request — this triggers both procurement review and AI Assessment.
- Even free tools with click-through terms require a Buy@RH request. You cannot sign agreements on Red Hat's behalf.

Full document: Search "Approval process for AI tools" on The Source.

### Key Contacts

| Purpose | Contact |
|---------|---------|
| General AI usage questions | usingai@redhat.com |
| AI Assessment status/process | aia@redhat.com |
| Privacy/data classification | privacy@redhat.com |
| Security concerns | infosec@redhat.com |
| Product security vulnerabilities | secalert@redhat.com |
| AI platform support | #help-it-ai-platforms (Slack) |

<!-- Link references -->
[AI Tools Source page]: https://source.redhat.com/departments/it/ai-tools
