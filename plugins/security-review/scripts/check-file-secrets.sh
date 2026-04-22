#!/bin/bash
# PreToolUse(Write) hook — scan file content for credentials before writing
set -euo pipefail

if ! command -v jq &>/dev/null; then
    exit 0  # Fail open if jq is missing
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.toolInput.file_path // empty')
CONTENT=$(echo "$INPUT" | jq -r '.toolInput.content // empty')

if [[ -z "$CONTENT" ]]; then
    exit 0
fi

FOUND=""

# Scan content for credential patterns

# AWS access key
if echo "$CONTENT" | grep -qE 'AKIA[0-9A-Z]{16}'; then
    FOUND="${FOUND}\n- AWS access key (AKIA...) detected"
fi

# Private keys
if echo "$CONTENT" | grep -qE 'BEGIN (RSA )?PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY'; then
    FOUND="${FOUND}\n- Private key detected"
fi

# Password assignments
if echo "$CONTENT" | grep -qiE 'password\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded password detected"
fi

# Token assignments
if echo "$CONTENT" | grep -qiE 'token\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded token detected"
fi

# Secret assignments
if echo "$CONTENT" | grep -qiE 'secret\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded secret detected"
fi

# API key assignments
if echo "$CONTENT" | grep -qiE 'api_key\s*[:=]\s*['\''"][^'\''"]+['\''"]'; then
    FOUND="${FOUND}\n- Hardcoded API key detected"
fi

# Database connection strings with embedded passwords
if echo "$CONTENT" | grep -qiE '(mysql|postgres|postgresql|mongodb|redis)://[^:]+:[^@]+@'; then
    FOUND="${FOUND}\n- Database connection string with embedded password detected"
fi

# Sensitive file path check (warn only if content also has secrets)
if [[ -n "$FOUND" ]]; then
    if echo "$FILE_PATH" | grep -qiE '\.(pem|key|p12|pfx)$'; then
        FOUND="${FOUND}\n- Writing to sensitive file type: ${FILE_PATH}"
    fi
    if echo "$FILE_PATH" | grep -qiE '(credential|secret)'; then
        FOUND="${FOUND}\n- Writing to sensitive path: ${FILE_PATH}"
    fi
    if echo "$FILE_PATH" | grep -qE '\.env$'; then
        FOUND="${FOUND}\n- Writing to .env file: ${FILE_PATH}"
    fi
fi

if [[ -n "$FOUND" ]]; then
    echo -e "BLOCKED: Potential credentials detected in file content (${FILE_PATH}):${FOUND}\n\nUse environment variables or external secret management instead of hardcoded credentials." >&2
    exit 1
fi

exit 0
