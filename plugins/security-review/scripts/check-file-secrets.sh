#!/usr/bin/bash
# PreToolUse(Write) hook — scan file content for credentials before writing
set -euo pipefail

parse_json_field() {
    local input="$1"
    local field="$2"
    if command -v jq &>/dev/null; then
        echo "$input" | jq -r "$field"
    elif command -v python3 &>/dev/null; then
        echo "$input" | python3 -c "
import sys, json, functools
data = json.load(sys.stdin)
keys = '$field'.strip('.').split('.')
val = functools.reduce(lambda d, k: d.get(k, '') if isinstance(d, dict) else '', keys, data)
print(val if val else '')
"
    else
        echo "security-review: neither jq nor python3 available — cannot parse hook input" >&2
        exit 1
    fi
}

INPUT=$(cat)
FILE_PATH=$(parse_json_field "$INPUT" '.toolInput.file_path')
CONTENT=$(parse_json_field "$INPUT" '.toolInput.content')

if [[ -z "$CONTENT" ]]; then
    exit 0
fi

FOUND=""

# AWS access key
if echo "$CONTENT" | grep -qE 'AKIA[0-9A-Z]{16}'; then
    FOUND="${FOUND}\n- AWS access key (AKIA...) detected"
fi

# Private keys
if echo "$CONTENT" | grep -qE 'BEGIN (RSA )?PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY'; then
    FOUND="${FOUND}\n- Private key detected"
fi

# Password assignments — skip env var refs ($VAR, ${VAR}) and templates ({{ }})
if echo "$CONTENT" | grep -qiE '(export[[:space:]]+)?password[[:space:]]*[:=][[:space:]]*[^${[:space:]]'; then
    FOUND="${FOUND}\n- Hardcoded password detected"
fi

# Token assignments
if echo "$CONTENT" | grep -qiE '(export[[:space:]]+)?token[[:space:]]*[:=][[:space:]]*[^${[:space:]]'; then
    FOUND="${FOUND}\n- Hardcoded token detected"
fi

# Secret assignments
if echo "$CONTENT" | grep -qiE '(export[[:space:]]+)?secret[[:space:]]*[:=][[:space:]]*[^${[:space:]]'; then
    FOUND="${FOUND}\n- Hardcoded secret detected"
fi

# API key assignments
if echo "$CONTENT" | grep -qiE '(export[[:space:]]+)?api_key[[:space:]]*[:=][[:space:]]*[^${[:space:]]'; then
    FOUND="${FOUND}\n- Hardcoded API key detected"
fi

# Database connection strings with embedded passwords (skip variable refs in password position)
if echo "$CONTENT" | grep -qiE '(mysql|postgres|postgresql|mongodb|redis)://[^:]+:[^${[:space:]@][^@]*@'; then
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
