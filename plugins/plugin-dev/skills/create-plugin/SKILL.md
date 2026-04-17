---
name: create-plugin
description: "Use when creating a new Claude Code plugin for the edge-tooling marketplace. Orchestrates the full workflow: scaffold, customize, and validate. Trigger when the user says things like 'create a plugin', 'new plugin', 'add a plugin to the marketplace', or 'I want to make a plugin for X'."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, AskUserQuestion
user-invocable: true
---

# Create Plugin (Orchestrator)

This skill orchestrates the full plugin creation workflow by coordinating three sub-skills. Run them in sequence — each depends on the previous step's output.

## Phase 1: Scaffold

Invoke the `scaffold-plugin` skill to gather requirements and create the directory structure.

Read `plugins/plugin-dev/skills/scaffold-plugin/SKILL.md` and follow its workflow:

1. Gather plugin name, components, category, description, author from the user
2. Run `./marketplace new <name> [--flags]`
3. Report what was created

## Phase 2: Customize

Invoke the `customize-plugin` skill to replace all template files with real implementations.

Read `plugins/plugin-dev/skills/customize-plugin/SKILL.md` and follow its workflow for each component the plugin has:

- **plugin.json** — verify fields
- **README.md** — write real documentation
- **Skills** — replace boilerplate SKILL.md with actual workflow instructions
- **Hooks** — configure hooks.json matchers and write hook scripts
- **MCP** — configure the server connection
- **Agents** — write agent definitions

This is the most involved phase. Work through each component with the user, asking for input on behavior and requirements.

## Phase 3: Validate

Invoke the `validate-plugin` skill to run all checks.

Read `plugins/plugin-dev/skills/validate-plugin/SKILL.md` and follow its workflow:

1. `./marketplace validate <name>`
2. `markdownlint plugins/<name>/**/*.md`
3. Hook script validation (if applicable)
4. `./marketplace catalog-update`
5. Suggest `/reload-plugins`

## After Validation

Offer to commit the new plugin. Use a commit message like:

```text
feat: add <plugin-name> plugin

<one-line description of what it does>
```
