# Plugin Development Guide

Complete guide to creating and publishing plugins for the Edge Tooling marketplace.

## Table of Contents

1. [Plugin Types](#plugin-types)
2. [Creating a Plugin](#creating-a-plugin)
3. [Plugin Metadata](#plugin-metadata)
4. [Testing Your Plugin](#testing-your-plugin)
5. [Publishing to Marketplace](#publishing-to-marketplace)
6. [Best Practices](#best-practices)
7. [Plugin Templates](#plugin-templates)
8. [Support](#support)
9. [Changelog Format](#changelog-format)

## Plugin Types

### Skill Plugin

Claude Code skills that extend Claude's capabilities with domain knowledge.

**When to use:**

- Adding domain-specific expertise (e.g., OVN debugging, LVMS operations)
- Implementing complex multi-step workflows
- Providing structured guidance for specific tasks

**Structure:**

```text
my-skill/
├── plugin.yml
├── README.md
├── skill.md           # Primary skill definition
└── examples/
    └── example-1.md   # Usage examples
```

### Command Plugin

Executable scripts or tools invoked via custom commands.

**When to use:**

- Wrapping existing tools with better UX
- Automating repetitive operations
- Integrating external APIs or services

**Structure:**

```text
my-command/
├── plugin.yml
├── README.md
├── command.sh         # Main executable
├── lib/               # Supporting scripts
└── tests/             # Optional; test infrastructure is planned for a future release
    └── test.sh
```

### Subagent Plugin

Specialized sub-agents for focused research or analysis tasks.

**When to use:**

- Deep codebase exploration
- Parallel research tasks
- Isolating complex analysis from main context

**Structure:**

```text
my-subagent/
├── plugin.yml
├── README.md
└── agent.md
```

### Hybrid Plugin

Combination of multiple plugin types working together.
The marketplace scaffolds hybrids from the subagent template;
customize the generated files for your use case.

**When to use:**

- Complete feature requiring skills + commands
- Multi-modal workflows
- Complex integrations

**Structure:**

```text
my-hybrid/
├── plugin.yml
├── README.md
├── skill.md           # Skill definition
├── command.sh         # Supporting command
└── agent.md           # Sub-agent definition
```

## Creating a Plugin

> **Note:** All `./marketplace` commands must be run from the
> repository root directory.

### Using the Generator

The easiest way to create a plugin:

```bash
./marketplace new my-plugin-name
```

This interactive wizard will:

1. Prompt for plugin type
2. Ask for category and metadata
3. Generate boilerplate structure
4. Create template files

### Manual Creation

1. **Create plugin directory:**

   ```bash
   mkdir -p plugins/my-plugin
   cd plugins/my-plugin
   ```

2. **Create plugin.yml:**

   ```yaml
   name: my-plugin
   version: 1.0.0
   type: skill
   category: cluster-ops
   description: Short description of what this plugin does
   author: your-github-handle

   compatibility:
     claude_code_min: "1.0.0"
     openshift_versions: ["4.14+"]
     required_tools: ["oc", "kubectl"]

   install:
     skills:
       - skill.md

   usage:
     trigger_keywords:
       - "my plugin trigger"
     examples:
       - "Use my-plugin to analyze cluster health"
   ```

3. **Create README.md:**

   ```markdown
   # My Plugin

   Brief description.

   ## Installation

   \`\`\`bash
   ./marketplace install my-plugin
   \`\`\`

   ## Usage

   Describe how to use the plugin.

   ## Examples

   Provide real-world examples.
   ```

4. **Create your skill/command file:**

   For skills, create `skill.md` following the skill definition format.
   For commands, create executable scripts.

## Plugin Metadata

### Required Fields

| Field | Description | Example |
| --- | --- | --- |
| `name` | Unique ID (letter, lowercase, numbers, hyphens) | `ovn-topology` |
| `version` | Semantic version | `1.0.0` |
| `type` | Plugin type | `skill`, `command`, `subagent`, `hybrid` |
| `category` | Plugin category | `cluster-ops`, `debug`, `deploy` |
| `description` | Short description (< 120 chars) | `Describe OVN topology` |
| `author` | Maintainer GitHub handle | `jeff-roche` |

### Optional Fields

| Field | Description | Example |
| --- | --- | --- |
| `long_description` | Detailed description (markdown) | Long-form text |
| `homepage` | Documentation URL | `https://github.com/...` |
| `license` | SPDX license identifier | `Apache-2.0` |
| `tags` | Search keywords | `["ovn", "network", "visualization"]` |

### Compatibility Section

Declare what your plugin requires:

```yaml
compatibility:
  claude_code_min: "1.0.0"              # Minimum Claude Code version
  openshift_versions: ["4.14+", "4.15"] # Compatible OCP versions
  required_tools: ["oc", "kubectl"]     # Required CLI tools
  platforms: ["linux", "darwin"]        # Supported platforms
```

### Dependencies

Declare plugin dependencies:

```yaml
dependencies:
  plugins:                    # Required plugins
    - base-openshift-utils
  optional_plugins:           # Enhanced if present
    - advanced-networking
```

### Installation Configuration

Define what gets installed:

```yaml
install:
  skills:                     # Skill files to install
    - skill.md
    - advanced-skill.md
  commands:                   # Commands to install
    - bin/my-command
  agents:                     # Sub-agent definition files to install
    - agent.md
  files:                      # Additional files
    - config/defaults.yml
  hooks:                      # Hook scripts
    - hooks/pre-commit.sh
  post_install: scripts/setup.sh  # Post-install script
```

### Usage Hints

Help users discover your plugin:

```yaml
usage:
  trigger_keywords:           # Keywords for discovery
    - "analyze ovn topology"
    - "visualize network"
  examples:                   # Usage examples
    - "Generate OVN topology for cluster"
    - "Show network flow between nodes"
```

## Testing Your Plugin

### Manual Testing

1. **Validate your plugin:**

   ```bash
   ./marketplace validate my-plugin
   ```

   Use `./marketplace validate <plugin-name>` to verify your plugin before publishing.

2. **Test functionality:**

   - Invoke skills/commands
   - Verify expected behavior
   - Test edge cases

3. **Check compatibility:**

   - Test on different OpenShift versions
   - Verify required tools are detected
   - Test on supported platforms

### Marketplace Smoke Tests

Run the marketplace smoke tests to verify core CLI behavior:

```bash
bash plugins/tests/marketplace_smoke_test.sh
```

This covers: help output, catalog validation, plugin creation scaffolding,
and field-level validation.

## Publishing to Marketplace

### Pre-publish Checklist

- [ ] Plugin follows naming conventions
- [ ] plugin.yml is valid and complete
- [ ] README.md is comprehensive
- [ ] All dependencies are declared
- [ ] Compatibility requirements are accurate
- [ ] Examples are tested and work
- [ ] License is specified

### Validation

Run marketplace validation:

```bash
./marketplace validate my-plugin
```

This checks:

- Metadata schema compliance
- Required files exist
- No conflicting plugin names
- Valid version number
- Dependency availability

### Submission Process

1. **Fork the repository**

2. **Add your plugin:**

   ```bash
   cp -r my-plugin edge-tooling/plugins/
   ```

3. **Update catalog:**

   ```bash
   ./marketplace catalog-update
   ```

4. **Commit and push:**

   ```bash
   git add plugins/my-plugin
   git commit -m "feat: add my-plugin for X functionality"
   git push origin add-my-plugin
   ```

5. **Create pull request**

6. **Address review feedback**

### Review Criteria

Maintainers will review for:

- Code quality and security
- Documentation completeness
- Smoke test passes (`bash plugins/tests/marketplace_smoke_test.sh`);
  plugin-specific automated tests are planned for a future release
- Usefulness to OpenShift/edge workflows
- No duplication of existing plugins
- Follows repository standards

## Best Practices

### Naming Conventions

- **Plugin names:** start with a letter, followed by lowercase letters,
  numbers, or hyphens
- **Skills:** Use descriptive trigger keywords
- **Commands:** Prefix with category (e.g., `ovn-topology`)

### Documentation

- Write for users unfamiliar with your domain
- Include real examples from actual workflows
- Document prerequisites clearly
- Provide troubleshooting section

### Compatibility

- Test on oldest supported OpenShift version
- Declare all tool dependencies
- Use version checks for required tools
- Gracefully handle missing dependencies

### Security

- Never hardcode credentials
- Use environment variables for secrets
- Validate all user inputs
- Follow principle of least privilege
- Scan for vulnerabilities before publishing

### Performance

- Avoid unnecessary tool invocations
- Cache expensive operations
- Provide progress feedback for long operations
- Use sub-agents for parallel work

### Maintainability

- Keep plugins focused (single responsibility)
- Version dependencies explicitly
- Document breaking changes in changelog
- Respond to issues promptly

### User Experience

- Provide clear error messages
- Include usage examples
- Make common tasks easy
- Advanced features can require flags
- Fail fast with actionable guidance

## Plugin Templates

The marketplace includes starter templates for each plugin type
in [.templates/](../.templates/). Use `./marketplace new` to scaffold
from these templates, or study them directly:

- `skill/` — Skill definition with trigger conditions and examples
- `command/` — Bash command with argument parsing and error handling
- `subagent/` — Sub-agent with spawn conditions and I/O specs

## Support

Need help developing your plugin?

- Review existing plugins for patterns
- Check [schema.yml](../.registry/schema.yml) for metadata reference
- Ask questions in GitHub Discussions
- Join the OpenShift Edge community

## Changelog Format

Track changes in plugin.yml:

```yaml
changelog:
  - version: 1.1.0
    date: 2026-03-23
    changes:
      - "Added support for OCP 4.16"
      - "Fixed topology rendering bug"
      - "Improved performance for large clusters"
  - version: 1.0.0
    date: 2026-01-15
    changes:
      - "Initial release"
```
