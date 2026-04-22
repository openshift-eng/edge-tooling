#!/usr/bin/bash
# PreToolUse(Bash) hook — validates conventional commits format on git commit commands
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

# Extract commit message subject line
SUBJECT=""

# Heredoc pattern: git commit -m "$(cat <<'EOF' ... EOF )"
if [[ "$COMMAND" =~ cat\ \<\<[\']?EOF ]]; then
    # Extract first non-empty line after the heredoc delimiter
    SUBJECT=$(echo "$COMMAND" | sed -n '/<<.*EOF/,/^EOF/{/<<.*EOF/d;/^EOF/d;/^[[:space:]]*$/d;p;}' | head -1 | sed 's/^[[:space:]]*//')
# Standard -m pattern with double or single quotes
elif [[ "$COMMAND" =~ git\ commit.*-m\ \" ]]; then
    SUBJECT=$(echo "$COMMAND" | sed -n 's/.*git commit[^"]*-m "\([^"]*\)".*/\1/p' | head -1)
elif [[ "$COMMAND" =~ git\ commit.*-m\ \' ]]; then
    SUBJECT=$(echo "$COMMAND" | sed -n "s/.*git commit[^']*-m '\\([^']*\\)'.*/\\1/p" | head -1)
fi

# If we couldn't extract a message, fail open
if [ -z "$SUBJECT" ]; then
    exit 0
fi

# For multi-line messages, take only the first line (subject)
SUBJECT=$(echo "$SUBJECT" | head -1)

# Validate against conventional commits pattern
if echo "$SUBJECT" | grep -qE '^(feat|fix|docs|refactor|test|chore|build|ci|perf|style)(\(.+\))?: .+'; then
    exit 0
fi

# Invalid format
cat >&2 <<'MSG'
Commit message does not follow conventional commits format.
Expected: type(scope): description
Types: feat, fix, docs, refactor, test, chore, build, ci, perf, style
Examples:
  feat(api): add health endpoint
  fix(deploy): correct subnet CIDR validation
  docs: update contributing guide
MSG
exit 1
