# Plugins

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

Plugins package related skills, hooks, agents, MCP servers, and scripts into a single distributable unit for a domain. They are the container format for Claude Code extensions.

## When to Use

- You have **multiple related components** for a domain (e.g., CI analysis needs several skills, agents, and scripts).
- You want to **distribute via the marketplace** so teammates can install with a single command.
- The domain has **shared reference material** (templates, conventions, configuration) that multiple skills need.

**Don't use a plugin when:**

- You have a single skill with no supporting components. Use a standalone skill instead.
- The tools span unrelated domains. Split into separate plugins.

## Anatomy

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json          # Manifest (required)
├── skills/
│   ├── do-thing/
│   │   └── SKILL.md         # Skill definition
│   └── analyze-thing/
│       └── SKILL.md
├── agents/
│   └── data-fetcher.md      # Agent definitions
├── hooks/
│   └── hooks.json           # Hook configurations
├── scripts/
│   └── fetch-data.sh        # Deterministic utility scripts
├── references/
│   └── conventions.md       # Shared reference material for skills
├── .mcp.json                # MCP server connections (optional)
└── README.md                # Purpose, installation, usage
```

**Key directories:**

| Directory | Purpose |
|-----------|---------|
| `.claude-plugin/` | Contains the manifest. Required for marketplace discovery. |
| `skills/` | Each subdirectory is a skill. Must contain `SKILL.md`. |
| `agents/` | Agent definitions as markdown files with YAML frontmatter. |
| `hooks/` | Hook configurations in `hooks.json`. |
| `scripts/` | Shell/Python scripts for deterministic operations. |
| `references/` | Shared reference files loaded on demand by skills. |

## Manifest: `plugin.json`

The manifest lives at `.claude-plugin/plugin.json` and controls plugin identity and marketplace metadata.

```json
{
  "name": "edge-scrum",
  "description": "Agents, skills, and workflows relevant to scrum process management for the OpenShift Edge Team",
  "version": "1.0.0",
  "author": { "name": "jeff-roche" },
  "homepage": "https://github.com/openshift-eng/edge-tooling",
  "license": "Apache-2.0"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | Lowercase, hyphenated. Becomes the plugin namespace (e.g., `edge-scrum:sprint-health`). |
| `description` | Yes | One sentence. Describes the domain, not individual components. |
| `version` | Yes | Semver. Bump when adding or changing components. |
| `author.name` | Yes | GitHub username or team identifier. |
| `homepage` | Yes | Repository URL. |
| `license` | Yes | Use `Apache-2.0`. |

## Do's

- **Group by domain.** All CI-related tools go in one plugin (`microshift-ci`), all scrum tools in another (`edge-scrum`). Domain boundaries should be obvious.
- **Use `${PLUGIN_DIR}` / `${CLAUDE_PLUGIN_ROOT}` for paths.** These variables resolve to the plugin's installation directory at runtime. Never hardcode absolute paths.

  ```markdown
  Run: `bash "${PLUGIN_DIR}/scripts/fetch-data.sh"`
  Read: `${PLUGIN_DIR}/references/conventions.md`
  ```

- **Include a README.** Cover purpose, prerequisites, installation, available skills, and examples. This is the entry point for anyone evaluating or onboarding to the plugin.
- **Validate before publishing.** Run the plugin validator to catch structural issues before teammates install a broken plugin.

  ```bash
  ./marketplace validate my-plugin
  ```

- **Use the marketplace CLI to scaffold.** It creates the correct directory structure and template files.

  ```bash
  ./marketplace new my-plugin
  ```

## Don'ts

- **Don't create a plugin for a single skill.** A plugin adds structural overhead (manifest, directory hierarchy). If you have one skill with no agents, hooks, or shared references, use a standalone skill.
- **Don't hardcode paths.** Absolute paths break when the plugin is installed on another machine. Use `${PLUGIN_DIR}` and `${CLAUDE_PLUGIN_ROOT}`.
- **Don't mix unrelated concerns.** A plugin for CI analysis should not also manage sprint ceremonies. If you find yourself adding skills that don't share reference material or scripts, split into separate plugins.
- **Don't skip the manifest.** Without `.claude-plugin/plugin.json`, the marketplace cannot discover or validate the plugin.
- **Don't duplicate across plugins.** If two plugins need the same functionality, extract it into a shared script or MCP server rather than copying code.

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|-------------|---------|-----|
| Monolith plugin | One plugin with 15+ skills spanning unrelated domains. Hard to maintain, slow to load. | Split by domain. Each plugin should have a clear, one-sentence description. |
| No README | Teammates cannot evaluate or onboard without reading every SKILL.md. | Add a README with purpose, prerequisites, and available skills. |
| Hardcoded credentials | API keys or tokens embedded in scripts or reference files. | Use environment variables. Configure secrets in `.mcp.json` via `${VAR}` syntax. |
| Dead components | Skills or agents that no longer work but remain in the plugin. | Remove or fix. Dead components waste context when Claude discovers them. |
| Version stagnation | Version stays at `1.0.0` despite significant changes. | Bump version on each meaningful change. Helps teammates know when to update. |

## Examples from This Repo

### `edge-scrum` -- Domain-scoped plugin with multiple skills and shared references

```text
plugins/edge-scrum/
├── .claude-plugin/plugin.json
├── skills/
│   ├── sprint-health/SKILL.md
│   ├── sprint-health-capacity-analyzer/SKILL.md
│   ├── sprint-health-midpoint-analyzer/SKILL.md
│   ├── sprint-health-retro-analyzer/SKILL.md
│   ├── release-health/SKILL.md
│   ├── release-health-analysis/SKILL.md
│   └── create-epic/SKILL.md
├── references/
│   └── laws/                    # Team conventions loaded on demand
├── bin/                         # Data transformation scripts
└── README.md
```

Skills share `references/laws/` for team conventions. The `sprint-health` skill routes to sub-skills (capacity, midpoint, retro) based on sprint timing.

### `microshift-ci` -- CI automation with skill-to-agent orchestration

```text
plugins/microshift-ci/
├── .claude-plugin/plugin.json
├── skills/
│   ├── doctor/SKILL.md
│   ├── prow-job/SKILL.md
│   ├── test-job/SKILL.md
│   ├── test-scenario/SKILL.md
│   └── create-bugs/SKILL.md
├── scripts/                     # Data fetching and aggregation
│   ├── doctor.sh
│   ├── download-jobs.sh
│   ├── aggregate.py
│   └── create-report.py
└── README.md
```

The `doctor` skill orchestrates parallel agents for multi-release CI analysis. Scripts handle data fetching and transformation; agents handle analysis.

### `hello-world` -- Minimal plugin template

```text
plugins/hello-world/
├── .claude-plugin/plugin.json
├── skills/
│   └── hello-world/SKILL.md
├── command.sh
└── README.md
```

Demonstrates the minimum viable plugin structure. Use as a starting point or reference.

## Marketplace Workflow

The marketplace CLI (`./marketplace`) manages the full plugin lifecycle:

### 1. Scaffold

```bash
./marketplace new my-plugin
```

Creates the directory structure, template `plugin.json`, and starter files. Follow the interactive prompts to select which components to include (skills, hooks, agents, MCP).

### 2. Develop

Build out skills, agents, scripts, and references. Use `${PLUGIN_DIR}` for all internal paths. Test each skill individually before integrating.

### 3. Validate

```bash
./marketplace validate my-plugin
```

Checks plugin structure, manifest fields, and component integrity. Fix all reported issues before publishing.

### 4. Register

Add the plugin to the marketplace catalog so teammates can discover it:

```bash
./marketplace catalog-update
```

### 5. Install

Teammates install plugins from the marketplace:

```bash
/plugin marketplace add openshift-eng/edge-tooling
```

Then select the plugin to install from the available catalog.

## References

- [Building AI Tools overview](README.md) -- decision matrix and shared design principles
- [Skills guide](skills.md) -- skill development best practices
- [Hooks guide](hooks.md) -- hook configuration and patterns
- [Agents guide](agents.md) -- agent design and orchestration
- [MCP Servers guide](mcp-servers.md) -- MCP server integration
