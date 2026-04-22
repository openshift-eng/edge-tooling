# Edge Tooling Plugin Marketplace

A Claude Code plugin marketplace for OpenShift and edge computing workflows.

## For Users

Add this marketplace to Claude Code:

```text
/plugin marketplace add openshift-eng/edge-tooling
```

Browse and install plugins:

```text
/plugin
```

## For Developers

### Quick Start

```bash
# Create a new plugin (interactive)
./marketplace new my-plugin

# Create with specific components
./marketplace new my-plugin --skill --agent

# Validate
./marketplace validate my-plugin

# Update marketplace catalog
./marketplace catalog-update

# List plugins
./marketplace list
```

### Shell Completion

Enable tab completion for marketplace commands and plugin names:

**Bash** — add to `~/.bashrc`:

```bash
source <(./marketplace completion bash)
```

**Zsh** — add to `~/.zshrc`:

```bash
source <(./marketplace completion zsh)
```

If `marketplace` is installed globally (e.g. symlinked into `$PATH`), omit the `./` prefix.

Restart your shell or re-source the file to activate.

### Plugin Structure

Plugins follow the Claude Code plugin format:

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json       # Plugin manifest (required)
├── skills/               # Skill definitions
│   └── my-plugin/
│       └── SKILL.md
├── hooks/                # Event hooks
│   └── hooks.json
├── agents/               # Agent definitions
│   └── my-plugin.md
├── .mcp.json             # MCP server config
└── README.md             # Documentation (required)
```

### Categories

| Category | Description |
| -------- | ----------- |
| cluster-ops | Cluster lifecycle, health, upgrades |
| debug | Troubleshooting and analysis |
| deploy | Deployment automation |
| network | Network diagnostics |
| operator | Operator development |
| ci-cd | CI/CD automation |
| util | General utilities |

## Documentation

- [Plugin Development Guide](docs/DEVELOPMENT.md)
- [Contributing](docs/CONTRIBUTING.md)
