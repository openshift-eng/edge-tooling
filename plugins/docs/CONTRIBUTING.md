# Contributing to the Plugin Marketplace

Thank you for contributing to the Edge Tooling Plugin Marketplace!
This guide covers submission guidelines and review process.

## Quick Contribution Checklist

- [ ] Plugin solves a real OpenShift/edge workflow problem
- [ ] No existing plugin provides this functionality
- [ ] Follows plugin structure and naming conventions
- [ ] Includes comprehensive README.md
- [ ] plugin.yml is complete and valid
- [ ] Tested on declared compatible OpenShift versions
- [ ] Examples are working and documented
- [ ] Tests included (if applicable)
- [ ] No security vulnerabilities
- [ ] License is compatible (Apache-2.0 preferred)

## Contribution Types

### New Plugin

Submit a completely new plugin to the marketplace.

**Process:**

1. Check existing plugins to avoid duplication
2. Create plugin following [Development Guide](DEVELOPMENT.md)
3. Test thoroughly
4. Run `./marketplace catalog-update` to rebuild the catalog
5. Submit PR with plugin added to `plugins/` directory

### Plugin Update

Update an existing plugin (bug fix, feature, version bump).

**Process:**

1. Fork the repository
2. Make changes to plugin directory
3. Update version in plugin.yml
4. Add changelog entry
5. Submit PR with clear description of changes

### Documentation Improvement

Improve plugin documentation or marketplace docs.

**Process:**

1. Edit relevant markdown files
2. Submit PR with "docs:" prefix in commit message

### Bug Report

Report issues with plugins or marketplace.

**Process:**

1. Check existing issues
2. Create new issue with template
3. Include reproduction steps
4. Tag with appropriate labels

## Submission Guidelines

### Plugin Requirements

#### Functionality

- Solves a specific, well-defined problem
- Relevant to OpenShift or edge computing workflows
- Doesn't duplicate existing plugins (or improves significantly)
- Works as documented

#### Code Quality

- Clean, readable code
- Follows shell script best practices (for commands)
- Proper error handling
- Input validation
- No hardcoded credentials or secrets

#### Documentation

- Clear, concise README.md
- Usage examples that work
- Prerequisites listed
- Troubleshooting section
- Installation instructions

#### Testing

- Manual testing completed
- Works on declared compatible versions
- Edge cases considered
- Tests included where appropriate

#### Security

- No known vulnerabilities
- No credential leakage
- Validates user inputs
- Follows least privilege principle
- Uses secure defaults

### Metadata Requirements

Your plugin.yml must include:

**Required:**

- name (unique, lowercase-with-hyphens)
- version (semantic versioning)
- type (skill|command|subagent|hybrid)
- category (valid category from schema)
- description (< 120 chars, clear value prop)
- author (GitHub handle or name)

**Strongly Recommended:**

- compatibility.claude_code_min
- compatibility.openshift_versions
- compatibility.required_tools
- usage.trigger_keywords
- usage.examples

**Optional but Encouraged:**

- long_description
- homepage (link to docs/repo)
- license
- tags
- dependencies
- changelog

### File Structure

Minimum required files:

```text
my-plugin/
├── plugin.yml          # Required: metadata
└── README.md           # Required: documentation
```

Typical structure:

```text
my-plugin/
├── plugin.yml
├── README.md
├── skill.md            # For skills
├── command.sh          # For commands
├── examples/
│   └── example-1.md
└── tests/
    └── test.sh
```

### Naming Conventions

- **Plugin name:** lowercase-with-hyphens, descriptive
  - Good: `ovn-topology-visualizer`, `cluster-health-check`
  - Bad: `tool1`, `my_plugin`, `ANALYZER`

- **Files:** lowercase, standard extensions
  - Skills: `skill.md`, `skill-advanced.md`
  - Commands: `command.sh`, descriptive names
  - Config: `config.yml`, `defaults.yml`

- **Categories:** Use predefined categories only
  - cluster-ops, debug, deploy, network, operator, ci-cd, util

### Commit Message Format

Follow conventional commits:

```text
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**

- `feat`: New plugin or feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Test additions/changes
- `refactor`: Code refactoring
- `chore`: Maintenance tasks

**Examples:**

```text
feat(plugins): add ovn-topology visualizer plugin

Adds a new skill for generating and visualizing OVN-Kubernetes
network topology diagrams from must-gather data.

Closes #123
```

```text
fix(cluster-health): handle missing kubeconfig gracefully

Previously failed with unclear error when KUBECONFIG not set.
Now provides clear error message and suggests fixes.
```

## Review Process

### Automatic Checks

When you submit a PR, automated checks run:

- Schema validation
- Required file presence
- Naming convention compliance
- No conflicting plugin names
- Valid version format

### Manual Review

Maintainers will review:

1. **Functionality**
   - Does it work as described?
   - Is it useful for the target audience?
   - Any bugs or edge cases missed?

2. **Code Quality**
   - Readable and maintainable?
   - Proper error handling?
   - Security best practices followed?

3. **Documentation**
   - Clear and complete?
   - Examples tested and working?
   - Prerequisites accurate?

4. **Testing**
   - Adequate test coverage?
   - Works on declared compatible versions?

5. **Integration**
   - Conflicts with existing plugins?
   - Dependencies available?
   - Follows repository standards?

### Review Timeline

- Initial review: Within 7 days
- Follow-up reviews: Within 3 days
- Complex plugins may take longer

### Addressing Feedback

When maintainers request changes:

1. Read feedback carefully
2. Ask questions if unclear
3. Make requested changes
4. Push updates to same PR branch
5. Respond to comments when done

## Publishing

Once approved:

1. Maintainer merges PR
2. Plugin added to catalog
3. Available via `./marketplace list`
4. Announced in release notes

## Plugin Maintenance

### Your Responsibilities

As a plugin author, you're expected to:

- Respond to issues within reasonable time
- Keep plugin updated for new OpenShift versions
- Fix security vulnerabilities promptly
- Update documentation as needed
- Maintain backward compatibility when possible

### Deprecation

If you can no longer maintain a plugin:

1. Open an issue announcing deprecation
2. Update plugin.yml with deprecation notice
3. Suggest alternatives if available
4. Allow 90 days before removal

### Transfer of Ownership

To transfer plugin ownership:

1. Find new maintainer
2. Update author field in plugin.yml
3. Submit PR with both parties acknowledging
4. Update GitHub repository permissions

## Community Guidelines

### Code of Conduct

- Be respectful and professional
- Welcome newcomers
- Provide constructive feedback
- Focus on ideas, not individuals
- Report unacceptable behavior

### Getting Help

- **Plugin development:** Review [DEVELOPMENT.md](DEVELOPMENT.md)
- **Questions:** Open a GitHub Discussion
- **Bugs:** Create an issue with template
- **Security issues:** Email maintainers privately

### Communication Channels

- **GitHub Issues:** Bug reports, feature requests
- **GitHub Discussions:** Questions, ideas, general discussion
- **Pull Requests:** Code contributions

## Recognition

Contributors are recognized:

- Listed in plugin.yml author field
- Mentioned in release notes
- Karma in the community

## Legal

### License

- All plugins must have compatible licenses
- Apache-2.0 strongly preferred
- Declare license in plugin.yml
- Include LICENSE file if custom

### Copyright

- You retain copyright of your contributions
- Grant repository license for distribution
- Ensure you have rights to all submitted code
- Don't submit proprietary or confidential code

### Third-Party Code

If including third-party code:

- Ensure license compatibility
- Include attribution
- List in dependencies
- Document in README

## Questions?

Not sure about something?

- Review existing plugins for examples
- Check [DEVELOPMENT.md](DEVELOPMENT.md) for details
- Ask in GitHub Discussions
- Open an issue for specific questions

Thank you for contributing to the Edge Tooling ecosystem!
