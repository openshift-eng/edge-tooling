#!/bin/bash
# Check AGENTS.md (or CLAUDE.md) line count against 200-line limit
# SessionStart hook — outputs hookSpecificOutput JSON if over limit

set -euo pipefail

# Require jq for JSON parsing
if ! command -v jq &>/dev/null; then
    echo "Error: jq is required but not installed. Install it with your package manager (e.g., 'sudo dnf install jq')." >&2
    exit 1
fi

# Read hook input
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

if [ -z "$CWD" ]; then
    exit 0
fi

cd "$CWD"

LINE_LIMIT=200
TARGET=""

if [ -f "AGENTS.md" ]; then
    TARGET="AGENTS.md"
elif [ -f "CLAUDE.md" ]; then
    TARGET="CLAUDE.md"
else
    # No file to check
    exit 0
fi

LINE_COUNT=$(wc -l < "$TARGET")

if [ "$LINE_COUNT" -le "$LINE_LIMIT" ]; then
    exit 0
fi

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "AGENTS.md SIZE WARNING: ${TARGET} is ${LINE_COUNT} lines (limit: ${LINE_LIMIT}). Large agent instruction files degrade AI performance. Refactor to use just-in-time data loading for detailed context. See /repo-standards:health-check for a full audit."
  }
}
EOF

exit 0
