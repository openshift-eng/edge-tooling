# Edge Tooling Plugin Marketplace

A curated collection of Claude Code skills, commands, and automation
tools for OpenShift and edge computing workflows.

## Quick Start

All `./marketplace` commands must be run from the repository root.

```bash
# List all available plugins
./marketplace list

# Search for plugins
./marketplace search ovn

# Install a plugin
./marketplace install plugin-name

# Uninstall a plugin
./marketplace uninstall plugin-name

# Update all plugins
./marketplace update

# Create a new plugin from template
./marketplace new my-plugin-name
```

## What are Plugins?

Plugins extend Claude Code's capabilities with domain-specific
knowledge and automation for:

- OpenShift cluster operations and debugging
- Edge computing deployment workflows
- CI/CD pipeline automation
- Operator development and testing
- Network troubleshooting and analysis

## Plugin Categories

- **cluster-ops**: Cluster lifecycle, health checks, upgrades
- **debug**: Troubleshooting tools and analysis
- **deploy**: Deployment automation and provisioning
- **network**: Network diagnostics and configuration
- **operator**: Operator development and testing
- **ci-cd**: CI/CD pipeline automation
- **util**: General utilities and helpers

## Available Plugins

<!-- Auto-generated plugin list -->
Run `./marketplace list` to see all available plugins with descriptions and versions.

## Installing Plugins

The marketplace CLI handles installation automatically:

```bash
./marketplace install <plugin-name>
```

This will:

1. Validate plugin compatibility
2. Check and install dependencies
3. Copy plugin files to the appropriate locations
4. Register the plugin with Claude Code
5. Display usage instructions

## Creating Your Own Plugin

Use the built-in generator to scaffold a new plugin:

```bash
./marketplace new my-awesome-plugin
```

Follow the prompts to select:

- Plugin type (skill, command, subagent)
- Category
- Dependencies

See [Plugin Development Guide](docs/DEVELOPMENT.md) for detailed authoring instructions.

## Plugin Structure

Each plugin follows a standard structure:

```text
plugin-name/
├── plugin.yml           # Metadata and configuration
├── README.md            # Plugin documentation
├── skill.md            # Skill definition (for skills)
├── command.sh          # Command script (for commands)
├── tests/              # Optional tests
└── examples/           # Optional usage examples
```

## Versioning and Updates

Plugins use semantic versioning (major.minor.patch). The marketplace
tracks installed versions and can update plugins:

```bash
# Check for updates
./marketplace status

# Update specific plugin
./marketplace update plugin-name

# Update all plugins
./marketplace update
```

## Plugin Compatibility

Each plugin declares:

- Minimum Claude Code version
- OpenShift version compatibility
- Required tools/binaries
- Other plugin dependencies

The marketplace validates compatibility before installation.

## Contributing

To contribute a plugin to the marketplace:

1. Create your plugin using `./marketplace new`
2. Test thoroughly with your workflows
3. Run `./marketplace catalog-update` to rebuild the catalog
4. Submit a pull request to this repository

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for submission
guidelines.

## Support

For plugin issues:

- Check plugin README for troubleshooting
- Review plugin compatibility requirements
- Report issues to the plugin maintainer (listed in plugin.yml)

For marketplace issues:

- Report at [https://github.com/openshift-eng/edge-tooling/issues](https://github.com/openshift-eng/edge-tooling/issues)
