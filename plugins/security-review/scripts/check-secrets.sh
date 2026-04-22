#!/usr/bin/bash
# PreToolUse(Bash) hook — detect credentials in git staged content
set -euo pipefail

parse_command() {
    if command -v jq &>/dev/null; then
        echo "$1" | jq -r '.toolInput.command // empty'
    elif command -v python3 &>/dev/null; then
        echo "$1" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("toolInput",{}).get("command",""))'
    else
        echo "security-review: neither jq nor python3 available — cannot parse hook input" >&2
        exit 1
    fi
}

INPUT=$(cat)
COMMAND=$(parse_command "$INPUT")

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Only inspect git commit — git add is covered by the Write hook (check-file-secrets.sh)
if ! echo "$COMMAND" | grep -qE 'git\s+commit'; then
    exit 0
fi

STAGED=$(git diff --cached --diff-filter=ACM 2>/dev/null || true)
if [[ -z "$STAGED" ]]; then
    exit 0
fi

FOUND=""

if echo "$STAGED" | grep -qE 'AKIA[0-9A-Z]{16}'; then
    FOUND="${FOUND}\n- AWS access key (AKIA...) detected in staged content"
fi

if echo "$STAGED" | grep -qE 'BEGIN (RSA )?PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY'; then
    FOUND="${FOUND}\n- Private key detected in staged content"
fi

if echo "$STAGED" | grep -qiE '(export\s+)?password\s*[:=]\s*\S'; then
    FOUND="${FOUND}\n- Hardcoded password detected in staged content"
fi

if echo "$STAGED" | grep -qiE '(export\s+)?token\s*[:=]\s*\S'; then
    FOUND="${FOUND}\n- Hardcoded token detected in staged content"
fi

if echo "$STAGED" | grep -qiE '(export\s+)?secret\s*[:=]\s*\S'; then
    FOUND="${FOUND}\n- Hardcoded secret detected in staged content"
fi

if echo "$STAGED" | grep -qiE '(export\s+)?api_key\s*[:=]\s*\S'; then
    FOUND="${FOUND}\n- Hardcoded API key detected in staged content"
fi

if echo "$STAGED" | grep -qE '^\+\+\+ b/.*\.env$'; then
    FOUND="${FOUND}\n- .env file detected in staged content"
fi

if [[ -n "$FOUND" ]]; then
    echo -e "BLOCKED: Potential credentials found in staged changes:${FOUND}\n\nUse environment variables instead of hardcoded credentials. Remove sensitive data and use .gitignore to exclude secret files." >&2
    exit 1
fi

exit 0
