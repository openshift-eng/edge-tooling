# code-quality

Code convention enforcement plugin for Claude Code. Validates commit messages, checks for AI attribution trailers, and verifies Coderabbit configuration.

## Hooks

Three hooks fire automatically:

| Hook | Event | Behavior |
|------|-------|----------|
| `check-commit-message.sh` | PreToolUse(Bash) | **Blocks** commits that don't follow conventional commits format |
| `check-attribution.sh` | PostToolUse(Bash) | **Warns** if AI attribution trailers are missing (advisory, non-blocking) |
| `check-coderabbit-config.sh` | SessionStart | **Reports** missing or incomplete `.coderabbit.yaml` configuration |

## Conventions Enforced

### Commit Messages

Subject line must match: `type(scope): description`

Valid types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`, `style`

### AI Attribution Trailers

Commits should include one of:
- `Co-Authored-By: ...`
- `Assisted-by: ...`
- `Generated-by: ...`

### Coderabbit Configuration

Repos should have a `.coderabbit.yaml` with `auto_review`, `path_filters`/`path_instructions`, and `instructions` configured.

## Installation

```bash
/plugin marketplace add openshift-eng/edge-tooling code-quality
```

## License

Apache-2.0
