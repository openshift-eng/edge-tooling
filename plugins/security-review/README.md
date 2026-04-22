# security-review

Security guards for credential protection and destructive command prevention. Hooks only -- no skills or commands.

## What It Does

Automatic PreToolUse hooks fire on every relevant tool invocation:

### Bash hooks

- **check-secrets.sh** -- Scans `git diff --cached` for credentials before `git commit` or `git add` runs. Detects AWS keys, private keys, hardcoded passwords/tokens/secrets/API keys, and `.env` files.
- **block-destructive.sh** -- Blocks dangerous commands: `rm -rf /`, `git push --force`, `git reset --hard`, `git clean -fd`, `git checkout -- .`, `git restore .`, `chmod -R 777`. Suggests safer alternatives.

### Write hooks

- **check-file-secrets.sh** -- Scans file content for credential patterns before writing. Detects the same credential types as check-secrets.sh plus database connection strings with embedded passwords.

## Detected Patterns

| Category | Examples |
|----------|----------|
| AWS keys | `AKIA` followed by 16 alphanumeric characters |
| Private keys | PEM-encoded RSA, generic, or OpenSSH private keys |
| Hardcoded credentials | `password`, `token`, `secret`, `api_key` assignments |
| Connection strings | Database URIs with embedded passwords |
| Sensitive files | `.env` files in staged git content |
| Destructive commands | Force push, hard reset, root deletion, insecure chmod |

## False Positives

If a hook blocks a legitimate action, Claude Code will present the block reason and let you approve the action through the permission prompt. No configuration changes needed.

## Installation

```bash
/plugin marketplace add openshift-eng/edge-tooling security-review
```
