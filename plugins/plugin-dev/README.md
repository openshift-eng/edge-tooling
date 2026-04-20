# plugin-dev

Skills for developing Claude Code plugins in the edge-tooling marketplace.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install plugin-dev
```

## Skills

| Skill | Invocable | Description |
|-------|-----------|-------------|
| `create-plugin` | yes | Orchestrator — full workflow from scaffold to validation |
| `scaffold-plugin` | yes | Gather requirements and run `marketplace new` |
| `customize-plugin` | yes | Replace template files with real implementations |
| `validate-plugin` | yes | Run marketplace validate, markdownlint, and catalog update |

## Prerequisites

- `marketplace` CLI available in PATH (symlink or direct)
- `markdownlint` for markdown linting

## Author

jeff-roche
