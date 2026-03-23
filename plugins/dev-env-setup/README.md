# Dev Environment Setup Plugin

Initialize or refresh multi-repo development environments from presets or custom configuration.

## Overview

The `dev-env-setup` skill helps you bootstrap multi-repository development environments. It provides:

- **Preset-based setup**: Choose from curated repository collections
- **Custom configuration**: Build your own repo list from scratch
- **Context distribution**: Automatically generate or distribute CLAUDE.md files for each repo
- **Root documentation**: Updates the workspace root CLAUDE.md with repo table

## Usage

```bash
/dev-env-setup              # Interactive preset selection
/dev-env-setup setup        # Same as above
/dev-env-setup setup custom # Build custom dev environment
```

## Workflow

### From Preset

1. Select from available presets
2. Clone all repositories defined in the preset
3. Distribute context files to repos (if provided)
4. Generate root CLAUDE.md repo table
5. Ready to start development

### Custom Setup

1. Describe your project or focus area
2. Build repo list interactively
3. Clone repositories
4. Generate context files collaboratively (optional)
5. Generate root CLAUDE.md repo table

## Requirements

- Git installed
- Access to clone repositories (SSH keys, credentials)
- Multi-repo workspace structure (repos/ directory)

## Example

For a preset workspace:

```bash
/dev-env-setup
# Select "lvm-operator" preset
# Clones: lvm-operator, topolvm, release, konflux-release-data, product-definitions
# Distributes context files for each repo
# Updates root CLAUDE.md with repo table
```

For custom workspace:

```bash
/dev-env-setup setup custom
# Describe: "Working on OpenShift authentication"
# Add repos: oauth-server, cluster-authentication-operator, console
# Generate context files collaboratively
# Updates root CLAUDE.md
```

## Integration

This skill expects to run in a workspace with:
- `presets/` directory (optional, for preset-based setup)
- `repos/` directory (created if needed)
- `dev-env.yaml` file (generated during setup)
- Root `CLAUDE.md` file

## License

Apache-2.0
