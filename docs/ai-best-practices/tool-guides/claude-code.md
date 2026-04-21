# Claude Code

[← AI Best Practices](../README.md)

Claude Code is our primary AI development tool. It runs as a CLI, VS Code extension, and desktop app, providing code generation, refactoring, analysis, and automation through an agentic conversation model.

## Setup and Configuration

### Installation

Claude Code is available in three forms:

- **CLI**: `npm install -g @anthropic-ai/claude-code`
- **VS Code extension**: Install "Claude Code" from the VS Code marketplace
- **Desktop app**: Download from [claude.ai/download](https://claude.ai/download)

The CLI and VS Code extension share configuration. Use whichever suits your workflow — the CLI for terminal-heavy work, the extension for editor-integrated sessions.

### AGENTS.md and CLAUDE.md

The team uses [AGENTS.md](https://github.com/agentsmd/agents.md) as the canonical agent instruction file. CLAUDE.md is a symlink to AGENTS.md:

```bash
ln -s AGENTS.md CLAUDE.md
```

This gives us vendor-neutral instructions (supported by 23+ tools) while maintaining Claude Code compatibility. Claude Code loads CLAUDE.md automatically; the symlink makes it read AGENTS.md.

AGENTS.md/CLAUDE.md files are loaded into every request, which makes them the most expensive context feature. Keep them under 200 lines. Move detailed reference material into skills (loaded on-demand) or separate files. See [Context Management](context-management.md) for details.

| Level | Location | Scope | Checked In? |
|-------|----------|-------|-------------|
| User-global | `~/.claude/CLAUDE.md` | All sessions, all repos | No (personal) |
| Repo root | `<repo>/AGENTS.md` (symlinked as `CLAUDE.md`) | All sessions in this repo | Yes |
| Component | `<repo>/<component>/CLAUDE.md` | Sessions in that directory | Yes |

**What belongs in AGENTS.md:**

- Project overview and component quick reference
- Build, test, and lint commands
- Code style guidelines and conventions
- PR/commit format expectations
- Architecture decisions that affect AI interactions

**What does NOT belong in AGENTS.md:**

- Information derivable from code or git history
- Detailed reference material (use skills with just-in-time loading)
- Personal preferences (use user-global CLAUDE.md or memory)
- Large context that rarely changes (move to reference files)

### Settings and Tool Scope

Claude Code uses settings files at two scopes:

| Scope | File | Purpose | Checked In? |
|-------|------|---------|-------------|
| **Workspace** (shared) | `.claude/settings.json` | Team settings: hooks, permissions, defaults | Yes |
| **User** (personal) | `.claude/settings.local.json` | Personal: additional permissions, tool allowlists | No (gitignored) |
| **Global** (all repos) | `~/.claude/settings.json` | Cross-repo defaults | No (personal) |

**Workspace settings** (`.claude/settings.json`) — shared with the team:

- Hook configurations (SessionStart checks, safety guards)
- Permission allowlists for tools the whole team uses
- Plugin configurations

**User settings** (`.claude/settings.local.json`) — personal:

- MCP tool permissions
- Personal bash command allowlists
- Tool-specific overrides

```json
{
  "permissions": {
    "allow": [
      "mcp__mcp-atlassian__jira_search",
      "mcp__mcp-atlassian__jira_get_issue",
      "Bash(gh pr *)",
      "Bash(git *)"
    ]
  }
}
```

### MCP Server Connections

MCP (Model Context Protocol) servers extend Claude Code with external tool integrations. Configure them in `.mcp.json` at the repo or user level.

Common team integrations:

| Server | Purpose | Config Location |
|--------|---------|-----------------|
| mcp-atlassian | Jira issue management | Plugin `.mcp.json` |
| openshift-ci | CI system queries | Plugin `.mcp.json` |
| GitHub | PR/issue management | Plugin `.mcp.json` |

See the [MCP Servers Guide](../building-ai-tools/mcp-servers.md) for configuration details.

## Team Conventions

### AGENTS.md vs. Memory vs. Plugins

| Information Type | Where It Goes | Context Cost |
|-----------------|---------------|--------------|
| Repo structure, workflows, conventions | AGENTS.md (checked in) | High — loaded every request |
| User role, preferences, working style | Claude Code memory (personal) | Low — loaded when relevant |
| Reusable team workflows | Plugin skills (checked in) | Zero until invoked |
| Event-driven automation | Plugin hooks (checked in) | Zero unless hook returns output |
| Detailed reference material | Skill reference files | Zero until skill reads them |

### Sub-agent Delegation

Claude Code can spawn sub-agents for isolated, parallel work. Our team convention is to **delegate relentlessly** — the main conversation is a coordination layer, not a workspace.

**Delegate to sub-agents:**

- Codebase search and exploration (use `Explore` sub-agent type)
- Multi-file reading and analysis
- Web searches and documentation lookups
- Independent parallel tasks
- Log analysis and data extraction

**Keep in main context:**

- Final code edits based on sub-agent findings
- User-facing decisions and communication
- Coordinating sub-agent results

Sub-agents provide **context isolation** — they explore extensively (tens of thousands of tokens) but return only a condensed summary (1,000–2,000 tokens). This is the primary mechanism for keeping the main context clean. See [Context Management](context-management.md#sub-agent-architectures).

### Plugin Marketplace

The team maintains a plugin marketplace in this repo (`plugins/`). Install plugins with:

```bash
/plugin marketplace add openshift-eng/edge-tooling
```

Then select the plugin to install. Available plugins include domain-specific tools for CI analysis, scrum workflows, and release management.

## Working with Claude Code

### Effective Prompting

- **Be specific about the outcome**, not the process. "Add retry logic to the API client with exponential backoff" is better than "improve the API client."
- **Provide context** when the task isn't obvious from the codebase. Link to issues, paste error messages, describe the user-facing behavior.
- **Give Claude a way to verify its work.** Provide tests, expected outputs, or screenshots. Without verification criteria, you become the only feedback loop.
- **Use skills** for repeatable workflows. If you find yourself giving the same multi-step instructions repeatedly, it should be a skill.
- **Let Claude explore** — don't micromanage tool calls. Provide the goal and constraints, not step-by-step tool instructions.
- **Explore first, then plan, then code.** For multi-file or ambiguous tasks, use Plan Mode to separate research from implementation.

### Skills and Slash Commands

Skills are invoked with `/skill-name` or triggered automatically based on their description. The team maintains domain-specific skills in plugins:

- `/microshift-ci:doctor` — CI health analysis across releases
- `/edge-scrum:sprint-health` — Sprint health assessment
- `/edge-scrum:create-epic` — Epic creation with team conventions

Run `/help` to see all available commands and skills.

### Worktrees

Claude Code can create isolated git worktrees for feature work. Use worktrees when:

- Starting a feature that shouldn't affect your current workspace
- Running parallel implementation tasks
- Experimenting with approaches you might discard
- Executing implementation plans in isolation

Worktrees provide full git isolation — changes in a worktree don't affect your main working directory. If the work is discarded, the worktree is cleaned up automatically.

### Sandboxing

Claude Code supports sandboxing to restrict what the agent can do:

- **Permission allowlists** in settings control which tools auto-execute vs. require approval.
- **`--allowedTools`** flag scopes permissions for batch operations.
- **Workspace trust** in VS Code controls extension execution.

Configure permissions thoughtfully — auto-allow known-safe read operations while prompting for writes, deletes, and external API calls.

### Context Management

Context is your scarcest resource. Key practices:

- Run `/clear` between unrelated tasks
- Use `/compact` with focused instructions to reclaim context
- Delegate exploration to sub-agents
- Use `/btw` for quick questions that shouldn't stay in history
- After 2+ failed corrections, clear and restart with a better prompt

For comprehensive guidance, see [Context Management](context-management.md).

## Code Attribution

### Commit Trailers

For substantial AI contributions, add trailers to commit messages:

```text
feat(api): add retry logic with exponential backoff

Implement retry with configurable backoff for all API client calls.
Handles transient failures (429, 5xx) with jitter.

Assisted-by: Claude Code
```

For code primarily generated by AI:

```text
Generated-by: Claude Code
```

Claude Code automatically adds `Co-Authored-By` trailers when it creates commits. This satisfies the attribution requirement.

### When to Mark

- **Always mark** substantial AI-generated or AI-modified code (multi-line implementations, new files, architectural changes).
- **No need to mark** trivial completions (single-line suggestions, boilerplate, import statements).
- **When in doubt, mark it.** Over-attribution is better than under-attribution.

## References

- [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- [Claude Code context costs](https://code.claude.com/docs/en/features-overview#understand-context-costs)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
