# Contributing to the Plugin Marketplace

## Quick Checklist

- [ ] Plugin follows Claude Code plugin format (`.claude-plugin/plugin.json`)
- [ ] Includes comprehensive README.md
- [ ] `plugin.json` has all required fields
- [ ] At least one component (skill, hook, agent, or MCP)
- [ ] Passes `./marketplace validate <name>`
- [ ] Smoke tests pass: `bash plugins/tests/marketplace_smoke_test.sh`
- [ ] Skills pass quality checks: `scripts/lint-skills.py <plugin>/skills/*/SKILL.md`
- [ ] Skills follow [Skill Quality Guidelines](SKILL-GUIDELINES.md)
- [ ] No security vulnerabilities or hardcoded credentials

## Contribution Types

### New Plugin

1. Create plugin: `./marketplace new my-plugin`
2. Implement components
3. Validate: `./marketplace validate my-plugin`
4. Update catalog: `./marketplace catalog-update`
5. Run smoke tests: `bash plugins/tests/marketplace_smoke_test.sh`
6. Submit PR with `feat(plugins): add my-plugin` commit

### Plugin Update

1. Make changes in the plugin directory
2. Bump version in `.claude-plugin/plugin.json`
3. Bump the matching version in the root `.claude-plugin/marketplace.json` — Claude Code uses this for update detection
4. Validate and test
5. Submit PR with `fix(plugins): description` or `feat(plugins): description`

### Documentation

1. Edit markdown files
2. Submit PR with `docs:` prefix

## Plugin Requirements

### Structure

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json     # Required
├── skills/             # At least one component
├── hooks/              #   required from
├── agents/             #   these four
├── .mcp.json           #   options
└── README.md           # Required
```

### plugin.json

Required fields: `name`, `description`, `version`, `author.name`

### Naming

- Plugin names: lowercase, start with letter, hyphens allowed
- Good: `ovn-topology-visualizer`, `cluster-health-check`
- Bad: `tool1`, `my_plugin`, `ANALYZER`

## Commit Messages

```text
<type>(plugins): <subject>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`

## Review Process

Maintainers review for:

- Code quality and security
- Documentation completeness
- Smoke tests pass
- Usefulness to OpenShift/edge workflows
- No duplication of existing plugins

## Community Guidelines

- Be respectful and professional
- Provide constructive feedback
- Report security issues privately
