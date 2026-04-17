---
name: validate-plugin
description: "Use when validating a marketplace plugin — runs marketplace validate, markdownlint, and catalog update. Trigger when the user says 'validate the plugin', 'check the plugin', 'is the plugin ready', or after finishing plugin customization."
allowed-tools: Bash, Read, Edit
user-invocable: true
---

# Validate Plugin

Run all checks against a plugin to confirm it's ready for use.

## Step 1: Marketplace Validation

```bash
./marketplace validate <plugin-name>
```

This checks:

- `.claude-plugin/plugin.json` exists and is valid JSON
- Plugin name in manifest matches directory name
- Version follows semver (`X.Y.Z`)
- Author name is present
- `README.md` exists
- At least one component present (skills/, hooks/, .mcp.json, or agents/)

Fix any warnings before proceeding.

## Step 2: Markdown Linting

```bash
markdownlint plugins/<plugin-name>/**/*.md
```

The repo's `.markdownlint.json` disables line-length (MD013) and bare-URL (MD034) rules. Common issues to fix:

- Missing language specifier on fenced code blocks (MD040)
- Inconsistent heading levels
- Missing blank lines around headings or lists

## Step 3: Hook Script Validation

If the plugin has hooks, verify:

- Hook scripts are executable (`chmod +x`)
- `hooks.json` is valid JSON
- Script paths in `hooks.json` are relative to repo root and point to existing files
- Scripts handle missing/irrelevant input gracefully (exit 0 for non-matching files)

Quick test:

```bash
echo '{"tool_input":{"file_path":"/tmp/test.md"}}' | plugins/<name>/hooks/<script>.sh
echo "exit: $?"
```

## Step 4: Catalog Update

```bash
./marketplace catalog-update
```

Confirm the plugin appears in the updated catalog with the correct name and description.

## Step 5: Verify Plugin Load

After validation passes, suggest the user reload plugins to confirm everything loads:

```text
/reload-plugins
```

Report the plugin count and confirm the new plugin's skills/hooks/MCP appear.
