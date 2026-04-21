# VS Code

[← AI Best Practices](../README.md)

VS Code is the team's primary development environment. It is not an AI tool itself, but it hosts AI extensions (Claude Code, GitHub Copilot) and requires deliberate configuration to manage data exposure, extension conflicts, and workspace trust.

## Setup and Configuration

### AI-Related Extensions

| Extension | Status | Notes |
|-----------|--------|-------|
| Claude Code | Approved | Primary AI tool. Install from VS Code marketplace. |
| GitHub Copilot | Check [AI Tools Source page] | Verify current approval status before enabling. Only use approved models. |
| Continue | Check [AI Tools Source page] | Local model support via Ollama. Verify approval before use. |
| Cody | Check [AI Tools Source page] | Verify approval before use. |

Any AI extension not listed on the [AI Tools Source page] requires an AI Assessment before use for Red Hat work. Email usingai@redhat.com to start the process.

### Workspace Trust

VS Code's workspace trust controls what extensions can execute in a given workspace. Configure it deliberately:

- **Trusted workspaces:** Your own repositories and known-safe projects. Extensions run with full permissions.
- **Restricted mode:** Untrusted or unfamiliar codebases. Extensions are limited — no task execution, no debugging, no terminal commands from extensions.
- **Review trust on first open.** When VS Code prompts for workspace trust on a new project, evaluate before granting. AI extensions in trusted workspaces can read and act on all files in the workspace.

### Data Exposure Controls

AI extensions can send code to external servers. Limit what they see.

**Telemetry:**

Disable or limit telemetry to reduce data exposure:

```json
{
  "telemetry.telemetryLevel": "off"
}
```

If full telemetry opt-out is not feasible, use `"error"` to limit telemetry to crash reports only.

**Extension settings:**

Review each AI extension's data-sharing settings individually. Common settings to check:

- Copilot: `github.copilot.advanced` settings for telemetry and snippet collection
- Continue: model endpoint configuration (ensure it points to local Ollama, not cloud)
- Any extension that offers "code improvement" or "snippet sharing" features

**Exclude sensitive files:**

Prevent AI extensions from reading sensitive files by adding exclusions to `.vscode/settings.json`:

```json
{
  "files.exclude": {
    "**/.env": true,
    "**/.env.*": true,
    "**/credentials.json": true,
    "**/secrets.yaml": true,
    "**/*.pem": true,
    "**/*.key": true
  }
}
```

This hides files from the VS Code explorer and from extensions that enumerate workspace files. It does not prevent direct file access by path. For stronger isolation, use `.gitignore` and tool-specific ignore files (`.cursorignore`, `.copilotignore`).

### Integration with Claude Code CLI

The Claude Code VS Code extension and the CLI share the same configuration:

- **Settings:** `.claude/settings.json` and `.claude/settings.local.json` apply to both.
- **AGENTS.md/CLAUDE.md:** Loaded by both the extension and CLI sessions.
- **Memory:** Shared across CLI and extension sessions.
- **MCP servers:** Configured once in `.mcp.json`, available in both.

Use the extension for editor-integrated sessions. Use the CLI for terminal-heavy work, scripting, and headless automation. There is no need to configure them separately.

## Usage Guidelines

### Managing Multiple AI Tools

Running multiple AI extensions simultaneously causes conflicts. Configure each tool for its strength and disable overlapping features.

**Recommended approach:**

- Use **Claude Code extension** for agentic tasks — multi-file edits, refactoring, exploration, planning.
- Use **GitHub Copilot** (if approved) for inline completions — single-line suggestions, boilerplate, import statements.
- Disable Copilot's chat features if using Claude Code for chat. Do not run two chat-based AI tools simultaneously.

**Disable conflicting completions:**

If running both Claude Code and Copilot, disable inline completions in one of them to avoid competing suggestions:

```json
{
  "github.copilot.enable": {
    "*": true
  },
  "claude-code.enableInlineCompletions": false
}
```

Or, if using Claude Code for completions, disable Copilot's:

```json
{
  "github.copilot.enable": {
    "*": false
  }
}
```

Pick one tool for completions and stick with it. Competing suggestions slow you down and create confusion.

### Extension Conflicts

Common conflicts when running multiple AI extensions:

| Conflict Area | Symptom | Resolution |
|---------------|---------|------------|
| Tab completion | Both extensions try to handle Tab key | Disable inline completions in one extension |
| Memory/context | Multiple tools reading workspace files increases data exposure | Audit which extensions have file access; disable those you are not actively using |
| Startup time | Multiple AI extensions increase VS Code startup time | Disable extensions you do not use daily; use extension profiles to switch sets |
| Keybindings | Overlapping keyboard shortcuts for AI features | Rebind conflicting shortcuts in `keybindings.json` |

### Settings Sync

VS Code settings sync shares configuration across machines. Be deliberate about what syncs:

- **Exclude sensitive settings** from sync. AI tool API keys, local file paths, and machine-specific configurations should not sync.
- **Use `.vscode/settings.json`** for project-specific settings (shared with the team via version control).
- **Use user settings** for personal preferences (synced across your machines only).
- **Review synced extensions.** Settings sync can install extensions on new machines automatically. Ensure only approved AI extensions are in your sync profile.

To exclude specific settings from sync:

```json
{
  "settingsSync.ignoredSettings": [
    "github.copilot.advanced",
    "claude-code.apiKey"
  ]
}
```

## Code Attribution

The same attribution conventions apply regardless of which AI extension generated the code. For substantial AI contributions, add trailers to commit messages:

```text
Assisted-by: Claude Code
```

or

```text
Assisted-by: GitHub Copilot
```

See the [Claude Code Guide](claude-code.md#code-attribution) for full attribution conventions.

<!-- Link references -->
[AI Tools Source page]: https://source.redhat.com/departments/it/ai-tools
