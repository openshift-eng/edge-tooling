# Cursor

[← AI Best Practices](../README.md)

Cursor is an AI-native IDE built on VS Code. It provides inline code generation, refactoring, and chat-based assistance through cloud-hosted models. Cursor sends code to external servers for inference — understand this data profile before use.

## Setup and Configuration

### Installation

Download Cursor from [cursor.com](https://cursor.com). It runs as a standalone application (not a VS Code extension). Cursor imports VS Code settings, extensions, and keybindings on first launch.

### Model Selection

Cursor supports multiple model providers. **Only use models explicitly approved for code assistant use on the [AI Tools Source page].**

Approved tool does not mean approved model. Each model must be individually approved. Before selecting a model in Cursor's settings, verify it appears on the AI Tools Source page with code assistant approval.

To configure: **Cursor Settings > Models** — select only approved models. Disable any models not on the approved list.

### Rules Files

Cursor reads project-level rules from `.cursorrules` (legacy) or `.cursor/rules/` (current) files in the repository root. These serve the same purpose as AGENTS.md — providing project context and conventions to the AI.

**Our convention:** The team maintains AGENTS.md as the canonical agent instruction file. To share context with Cursor:

1. Create a `.cursorrules` file that references or mirrors the relevant sections of AGENTS.md.
2. Keep `.cursorrules` focused on conventions Cursor needs — build commands, style rules, naming patterns.
3. Do not duplicate detailed content already in AGENTS.md. Keep it lean.

```text
# .cursorrules
# Project conventions for Cursor — see AGENTS.md for full details.

- Follow Go conventions: gofmt, golint, go vet
- Commit messages: type(scope): description
- Tests: table-driven, _test.go suffix
- No hardcoded paths — use environment variables
```

### Privacy Settings

Cursor is a cloud-hosted tool. Code is sent to external servers for model inference.

**Required configuration:**

- **Disable telemetry** if your organization requires it: **Cursor Settings > Privacy > Telemetry**.
- **Enable Privacy Mode** if available: prevents Cursor from storing your code on their servers for training.
- **Configure `.cursorignore`** to exclude sensitive files from AI context:

```text
# .cursorignore
.env
.env.*
**/credentials*
**/secrets*
**/*secret*
**/*token*
**/kubeconfig
```

Add `.cursorignore` to the repository so the entire team benefits from the same exclusions.

## Usage Guidelines

### Cursor vs. Claude Code

Use the right tool for the task. They complement each other.

| Task | Cursor | Claude Code |
|------|--------|-------------|
| Inline code edits in a single file | Preferred (Cmd/Ctrl+K) | Capable |
| Tab completion while typing | Preferred | N/A |
| Multi-file refactoring | Capable (Composer) | Preferred |
| Codebase-wide analysis | Limited | Preferred (sub-agents) |
| Complex multi-step automation | Not designed for this | Preferred (skills, hooks) |
| Quick questions about open file | Preferred (Cmd/Ctrl+L) | Capable |
| PR creation and git workflows | Not designed for this | Preferred |
| Plugin/skill development | Not designed for this | Preferred |
| Writing tests for visible code | Good | Good |
| Exploring unfamiliar codebases | Limited | Preferred (Explore sub-agent) |

**General rule:** Cursor for fast, editor-integrated edits. Claude Code for agentic, multi-step, or codebase-wide work.

### Input Sanitization

Cursor sends code to cloud-hosted models. Apply these safeguards:

- **Never paste confidential data** (API keys, customer data, credentials) into Cursor chat or inline prompts.
- **Close sensitive files** before using AI features. Cursor reads open tabs as context.
- **Use `.cursorignore`** to prevent sensitive files from being indexed.
- **Sanitize code snippets** before pasting into chat. Remove identifying information, real endpoints, and credentials.
- **Use synthetic data** in examples and test cases provided to Cursor.

### Cursor Features

**Tab Completion** — Cursor predicts the next edit as you type. Accept with Tab. This is the lowest-friction AI feature and handles boilerplate, repetitive patterns, and obvious completions well.

**Inline Chat (Cmd/Ctrl+K)** — Select code and press Cmd/Ctrl+K to edit it with a natural language instruction. Best for targeted, single-file modifications: "add error handling," "convert to async," "rename this variable."

**Composer (Cmd/Ctrl+I)** — Multi-file edit mode. Describe a change and Cursor proposes edits across multiple files. Review each proposed change carefully before accepting. Composer is useful for coordinated changes but less reliable than Claude Code for large refactors.

**Chat Panel (Cmd/Ctrl+L)** — Conversational interface for questions about your codebase. Cursor uses open files and codebase indexing as context. Good for quick questions about the code you are actively editing.

**Codebase indexing** — Cursor indexes your repository for context retrieval. Verify that `.cursorignore` excludes sensitive files before enabling indexing on a repository with confidential content.

## Code Attribution

### Commit Trailers

The same attribution conventions apply regardless of tool. For substantial AI contributions via Cursor, add trailers to commit messages:

```text
feat(api): add input validation for edge cases

Add parameter validation with descriptive error messages
for all public API endpoints.

Assisted-by: Cursor
```

For code primarily generated by Cursor:

```text
Generated-by: Cursor
```

### When to Mark

- **Always mark** substantial AI-generated or AI-modified code (multi-line implementations, new files, architectural changes).
- **No need to mark** trivial completions (single-line tab suggestions, boilerplate, import statements).
- **When in doubt, mark it.** Over-attribution is better than under-attribution.

<!-- Link references -->
[AI Tools Source page]: https://source.redhat.com/departments/it/ai-tools
