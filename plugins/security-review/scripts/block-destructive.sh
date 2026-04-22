#!/bin/bash
# PreToolUse(Bash) hook — block dangerous commands
set -euo pipefail

if ! command -v jq &>/dev/null; then
    exit 0  # Fail open if jq is missing
fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.toolInput.command // empty')

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# rm -rf targeting root, home, or bare /
if echo "$COMMAND" | grep -qE 'rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(/|~|\$HOME)\s*$' || \
   echo "$COMMAND" | grep -qE 'rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME)\s*$'; then
    echo "Blocked: \`rm -rf /\` (or ~ / \$HOME) would delete critical filesystem content." >&2
    exit 1
fi

# git push --force / -f (but not --force-with-lease)
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force($|\s)' && ! echo "$COMMAND" | grep -q '\-\-force-with-lease'; then
    echo "Blocked: \`git push --force\` can overwrite remote history. Use \`git push --force-with-lease\` instead." >&2
    exit 1
fi
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*\s-[a-zA-Z]*f' && ! echo "$COMMAND" | grep -q '\-\-force-with-lease'; then
    echo "Blocked: \`git push -f\` can overwrite remote history. Use \`git push --force-with-lease\` instead." >&2
    exit 1
fi

# git reset --hard
if echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard'; then
    echo "Blocked: \`git reset --hard\` discards all uncommitted changes. Use \`git stash\` to preserve work." >&2
    exit 1
fi

# git clean -fd / -fdx
if echo "$COMMAND" | grep -qE 'git\s+clean\s+-[a-zA-Z]*f[a-zA-Z]*d'; then
    echo "Blocked: \`git clean -fd\` permanently deletes untracked files. Use \`git stash --include-untracked\` instead." >&2
    exit 1
fi

# git checkout -- . (discard all changes)
if echo "$COMMAND" | grep -qE 'git\s+checkout\s+--\s+\.'; then
    echo "Blocked: \`git checkout -- .\` discards all uncommitted changes. Use \`git stash\` to preserve work." >&2
    exit 1
fi

# git restore . (discard all changes)
if echo "$COMMAND" | grep -qE 'git\s+restore\s+\.'; then
    echo "Blocked: \`git restore .\` discards all uncommitted changes. Use \`git stash\` to preserve work." >&2
    exit 1
fi

# chmod -R 777
if echo "$COMMAND" | grep -qE 'chmod\s+(-R\s+777|777\s+-R)'; then
    echo "Blocked: \`chmod -R 777\` sets insecure permissions. Use more restrictive permissions (e.g., 755 for directories, 644 for files)." >&2
    exit 1
fi

exit 0
