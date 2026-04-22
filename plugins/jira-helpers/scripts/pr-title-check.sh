#!/usr/bin/env bash
set -euo pipefail

command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

INPUT=$(cat)
TITLE=$(echo "$INPUT" | jq -r '.toolInput.title // empty')

if [[ -z "$TITLE" ]]; then
    jq -n '{
        "decision": "block",
        "reason": "PR title is empty or missing. It must start with a Jira ticket key followed by colon and space (e.g., '\''OCPEDGE-1234: Add feature X'\'')."
    }'
    exit 0
fi

if [[ "$TITLE" =~ ^[A-Z][A-Z0-9]+-[0-9]+:\ .+ ]]; then
    jq -n '{ "decision": "allow" }'
else
    jq -n --arg title "$TITLE" '{
        "decision": "block",
        "reason": ("PR title must start with a Jira ticket key followed by colon and space (e.g., '\''OCPEDGE-1234: Add feature X'\''). Got: '\''" + $title + "'\''")
    }'
fi
