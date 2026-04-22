#!/bin/bash
# PreToolUse(Bash) hook — detect credentials in git staged content
set -euo pipefail

if ! command -v jq &>/dev/null; then
    exit 0  # Fail open if jq is missing
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.toolInput.command // empty')

# Fast path: only inspect git commit/add commands
if [[ -z "$COMMAND" ]]; then
    exit 0
fi

if ! echo "$COMMAND" | grep -qE 'git (commit|add)'; then
    exit 0
fi

# Get staged diff content
STAGED=$(git diff --cached --diff-filter=ACM 2>/dev/null || true)
if [[ -z "$STAGED" ]]; then
    exit 0
fi

FOUND=""

# AWS access key
if echo "$STAGED" | grep -qE 'AKIA[0-9A-Z]{16}'; then
    FOUND="${FOUND}\n- AWS access key (AKIA...) detected in staged content"
fi

# Private keys
if echo "$STAGED" | grep -qE 'BEGIN (RSA )?PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY'; then
    FOUND="${FOUND}\n- Private key detected in staged content"
fi

# Password assignments
if echo "$STAGED" | grep -qiE 'password\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded password detected in staged content"
fi

# Token assignments
if echo "$STAGED" | grep -qiE 'token\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded token detected in staged content"
fi

# Secret assignments
if echo "$STAGED" | grep -qiE 'secret\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded secret detected in staged content"
fi

# API key assignments
if echo "$STAGED" | grep -qiE 'api_key\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded API key detected in staged content"
fi

# .env files being staged
if echo "$STAGED" | grep -qE '^\+\+\+ b/.*\.env$'; then
    FOUND="${FOUND}\n- .env file detected in staged content"
fi

if [[ -n "$FOUND" ]]; then
    echo -e "BLOCKED: Potential credentials found in staged changes:${FOUND}\n\nUse environment variables instead of hardcoded credentials. Remove sensitive data and use .gitignore to exclude secret files." >&2
    exit 1
fi

exit 0
