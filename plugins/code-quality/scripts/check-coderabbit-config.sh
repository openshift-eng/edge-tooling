#!/bin/bash
# SessionStart hook — validates .coderabbit.yaml presence and structure
set -euo pipefail

if ! command -v jq &>/dev/null; then
    exit 0
fi

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

if [ -z "$CWD" ]; then
    exit 0
fi

CONFIG="$CWD/.coderabbit.yaml"

# Check if config exists
if [ ! -f "$CONFIG" ]; then
    cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "CODERABBIT CONFIG MISSING: No .coderabbit.yaml found. AI code review is not configured for this repo. Consider adding one with auto_review enabled, path_filters, and project-specific instructions."
  }
}
EOF
    exit 0
fi

# Validate config structure
MISSING=()

if ! grep -q 'auto_review' "$CONFIG"; then
    MISSING+=("auto_review")
fi

if ! grep -qE 'path_filters|path_instructions' "$CONFIG"; then
    MISSING+=("path_filters or path_instructions")
fi

if ! grep -q 'instructions' "$CONFIG"; then
    MISSING+=("instructions")
fi

# If anything missing, report findings
if [ ${#MISSING[@]} -gt 0 ]; then
    MISSING_LIST=$(printf '%s, ' "${MISSING[@]}" | sed 's/, $//')
    MISSING_ESCAPED=$(printf '%s' "$MISSING_LIST" | sed 's/"/\\"/g')

    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "CODERABBIT CONFIG INCOMPLETE: .coderabbit.yaml exists but is missing: ${MISSING_ESCAPED}. Consider adding these for comprehensive AI code review coverage."
  }
}
EOF
fi

exit 0
