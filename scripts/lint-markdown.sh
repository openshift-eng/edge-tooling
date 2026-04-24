#!/usr/bin/bash
# Lint modified markdown files with markdownlint
#
# Usage:
#   Claude Code Stop hook:  piped JSON on stdin, outputs hookSpecificOutput
#   Git pre-commit hook:    called directly, exits non-zero on lint failure
#   Direct:                 ./scripts/lint-markdown.sh [--pre-commit]

set -euo pipefail

PRE_COMMIT=false
if [[ "${1:-}" == "--pre-commit" ]]; then
    PRE_COMMIT=true
fi

if ! command -v npx &>/dev/null; then
    exit 0
fi

# Determine working directory
if [ "$PRE_COMMIT" = true ]; then
    CWD="$(git rev-parse --show-toplevel)"
elif [ -t 0 ]; then
    CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
else
    INPUT=$(cat)
    if command -v jq &>/dev/null; then
        CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
    fi
    if [ -z "${CWD:-}" ]; then
        CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    fi
fi

cd "$CWD"

# Find .md files to lint based on context
if [ "$PRE_COMMIT" = true ]; then
    ALL_FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.md' 2>/dev/null || true)
else
    MODIFIED_FILES=$(git diff --name-only HEAD -- '*.md' 2>/dev/null || true)
    UNTRACKED_FILES=$(git ls-files --others --exclude-standard -- '*.md' 2>/dev/null || true)
    ALL_FILES=$(printf '%s\n%s' "$MODIFIED_FILES" "$UNTRACKED_FILES" | sort -u | sed '/^$/d')
fi

if [ -z "$ALL_FILES" ]; then
    exit 0
fi

# Filter to files that exist (use array for safe handling of special characters)
FILES_TO_LINT=()
while IFS= read -r file; do
    [ -f "$file" ] && FILES_TO_LINT+=("$file")
done <<< "$ALL_FILES"

if [ "${#FILES_TO_LINT[@]}" -eq 0 ]; then
    exit 0
fi

# Verify markdownlint-cli is available (handles offline / npx failure)
if ! npx --yes markdownlint-cli --version &>/dev/null; then
    exit 0
fi

# Run markdownlint
LINT_EXIT=0
LINT_OUTPUT=$(npx markdownlint-cli "${FILES_TO_LINT[@]}" 2>&1) || LINT_EXIT=$?

if [ "$LINT_EXIT" -eq 0 ]; then
    exit 0
fi

# Lint failed
if [ "$PRE_COMMIT" = true ]; then
    echo "$LINT_OUTPUT" >&2
    echo "" >&2
    echo "Fix markdownlint errors before committing." >&2
    exit 1
fi

# Claude Code hook — report via hookSpecificOutput
if command -v jq &>/dev/null; then
    LINT_ESCAPED=$(printf '%s' "$LINT_OUTPUT" | jq -Rs '.')
    jq -n --argjson errors "$LINT_ESCAPED" '{
      hookSpecificOutput: {
        hookEventName: "Stop",
        additionalContext: ("MARKDOWN LINT ERRORS FOUND in modified .md files. Please fix these issues:\n\n" + $errors)
      }
    }'
fi

exit 0
