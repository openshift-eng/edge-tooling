#!/usr/bin/bash
# PostToolUse(Bash) hook — warns if AI attribution trailers are missing after commits
set -euo pipefail

if ! command -v jq &>/dev/null; then
    exit 0
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.toolInput.command // empty')

# Fast path: not a commit command
if [[ "$COMMAND" != *"git commit"* ]]; then
    exit 0
fi

# Get the last commit message
COMMIT_MSG=$(git log -1 --format=%B 2>/dev/null || true)

if [ -z "$COMMIT_MSG" ]; then
    exit 0
fi

# Check for attribution trailers (case-insensitive)
if echo "$COMMIT_MSG" | grep -qi 'Co-Authored-By:'; then
    exit 0
fi
if echo "$COMMIT_MSG" | grep -qi 'Assisted-by:'; then
    exit 0
fi
if echo "$COMMIT_MSG" | grep -qi 'Generated-by:'; then
    exit 0
fi

# No attribution found — emit advisory context
cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "AI ATTRIBUTION MISSING: The last commit has no AI attribution trailer. Consider amending with one of:\n  Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>\n  Assisted-by: Claude Code\n  Generated-by: Claude Code"
  }
}
EOF

exit 0
