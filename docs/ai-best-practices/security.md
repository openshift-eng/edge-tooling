# Security Best Practices

[← AI Best Practices](README.md)

Security guidance for AI-assisted development. This document collects security-relevant practices from across the guide into one reference and adds security-specific guidance not covered elsewhere.

## Data Protection

**Hard rules** from Red Hat AI policy — these are non-negotiable:

- **Never input confidential data into unapproved tools.** This includes API keys, customer data, passwords, database credentials, support tickets, partner information, and Red Hat protected content. See [Core Principles](README.md#core-principles).
- **Sanitize code before sharing.** Remove all sensitive or identifying information from code snippets before providing them to any AI tool. Use synthetic data during development.
- **Know your tool's data profile.** Cloud-hosted tools (Cursor, Coderabbit) send code to external servers. Claude Code CLI runs locally but sends prompts and code context to Anthropic's API for inference — code is not processed entirely on your machine. Ollama runs fully local with no external calls. Understand the difference — see individual [tool guides](tool-guides/).

## Input Sanitization

Prevent accidental exposure of secrets:

- **Use `.gitignore`** to exclude `.env`, credentials, and secret files from version control.
- **Use tool-specific ignore files** — `.cursorignore` for Cursor, `files.exclude` in VS Code settings — to prevent AI tools from reading sensitive files.
- **Implement pre-commit hooks** to detect and block commits containing secrets (API keys, tokens, passwords).
- **Use synthetic data** for development and testing rather than copies of production data.
- **Close sensitive files** before using AI features that read open editor tabs as context.

## Code Review

- **All AI-generated code requires human review.** AI output is a suggestion. You are accountable for what you commit.
- **If code is outside your expertise**, get a peer review from a domain expert before sharing or committing.
- **Coderabbit complements but does not replace human review.** See [Coderabbit Guide](tool-guides/coderabbit.md#complement-not-replacement).
- **AI can introduce security vulnerabilities.** Be especially vigilant for injection risks, improper input validation, insecure defaults, and missing authentication checks.

## Attribution and Disclosure

- **Mark AI-generated code** with `Assisted-by:` or `Generated-by:` trailers. This ensures reviewers know to apply appropriate scrutiny. See [hub document](README.md#core-principles).
- **Check upstream policies** before contributing AI-assisted code to open-source projects.

## Tool Approval

- **Only use approved tools and models.** Tool approval ≠ model approval. See [AI Tools Source page].
- **No personal purchases.** All tool acquisition goes through Buy@RH.
- **Free tools with terms still require Buy@RH.** You cannot sign agreements on Red Hat's behalf.
- **Report concerns** to infosec@redhat.com (security) or secalert@redhat.com (product vulnerabilities).

## Building Secure AI Tools

When building skills, hooks, plugins, and MCP servers:

- **Never hardcode credentials.** Use environment variables via `${VAR}` syntax in `.mcp.json`. See [MCP Servers Guide](building-ai-tools/mcp-servers.md#use-environment-variables-for-secrets).
- **Run MCP servers in containers** for process isolation. See [MCP Servers Guide](building-ai-tools/mcp-servers.md#use-containers-for-isolation).
- **Don't expose destructive endpoints** without confirmation requirements. See [MCP Servers Guide](building-ai-tools/mcp-servers.md#dont-expose-sensitive-endpoints).
- **Treat memory as an attack surface.** Memory files are read back into context, creating a prompt injection vector. Sanitize stored content and scope memory per-user/per-project. See [Context Management](tool-guides/context-management.md#memory-best-practices).
- **Validate hook inputs.** Hook scripts receive JSON from Claude — validate before acting on it. See [Hooks Guide](building-ai-tools/hooks.md).
- **Pin container image versions** using SHA256 digests for immutability. See [MCP Servers Guide](building-ai-tools/mcp-servers.md#pin-image-versions).

## Guardrails for Tool Builders

For reducing hallucinations, increasing output consistency, and mitigating prompt injection in AI tools you build, see [Guardrails](building-ai-tools/guardrails.md).

## Sandboxing

Sandboxing limits what AI agents can do on your machine or in your environment. Multiple layers are available — use them together for defense in depth.

### Permission Controls

Claude Code's built-in permission system is the first line of defense:

- **Configure permission allowlists** in `.claude/settings.json` to auto-allow known-safe operations (read-only commands, specific MCP tools) while prompting for everything else. See [Claude Code Guide](tool-guides/claude-code.md#settings-and-tool-scope).
- **Use `--allowedTools`** to scope permissions for batch or automated operations.
- **Use worktrees** for isolated feature work that doesn't affect your current workspace. See [Claude Code Guide](tool-guides/claude-code.md#worktrees).

### Container-Based Sandboxing

Running Claude Code inside a container provides process-level isolation — the agent can only access what the container exposes.

**Dev Containers (official Anthropic support):**

Anthropic provides an official [Dev Container Feature](https://github.com/anthropics/devcontainer-features) that installs Claude Code CLI into any dev container. Add it to your `devcontainer.json`:

```json
{
  "features": {
    "ghcr.io/anthropics/devcontainer-features/claude-code:1.0": {}
  }
}
```

This is the recommended approach for containerized Claude Code. It works with VS Code Dev Containers, GitHub Codespaces, and any tool supporting the [Dev Container specification](https://containers.dev/). See the [official Claude Code Dev Container docs](https://code.claude.com/docs/en/devcontainer) for full setup guidance.

**Benefits of container sandboxing:**

- File system isolation — agent can only access mounted volumes
- Network isolation — control what the agent can reach
- Process isolation — agent cannot affect the host system
- Reproducible environments — consistent setup across team members
- Safe for full-auto mode — grant broader permissions inside the container since damage is contained

**When to use containers:**

- Working with untrusted codebases or unfamiliar plugins
- Running Claude Code in CI/CD pipelines
- Granting broader auto-allow permissions without risk to your host
- Automated batch operations where manual approval is impractical

### MCP Server Isolation

MCP servers should run in containers for isolation from the host. This is already the team's standard pattern — see [MCP Servers Guide](building-ai-tools/mcp-servers.md#use-containers-for-isolation).

Key practices:

- Run servers via `podman run --rm -i` for process isolation
- Pin container images to SHA256 digests for immutability
- Pass credentials via environment variables, not mounted files
- Limit container capabilities to the minimum required

<!-- Link references -->
[AI Tools Source page]: https://source.redhat.com/departments/it/ai-tools
