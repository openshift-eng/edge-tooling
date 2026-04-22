#!/usr/bin/bash
# PreToolUse(Bash) hook — block dangerous commands
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

# rm with recursive+force targeting root or home
# Detect rm, then check for both -r and -f (in any order, combined or separate),
# then check for dangerous target paths
if echo "$COMMAND" | grep -qE '(^|[[:space:]])(sudo[[:space:]]+)?rm[[:space:]]'; then
    HAS_R=false
    HAS_F=false
    echo "$COMMAND" | grep -qE '[[:space:]]+-[a-zA-Z]*r' && HAS_R=true
    echo "$COMMAND" | grep -qE '[[:space:]]+-[a-zA-Z]*f' && HAS_F=true
    echo "$COMMAND" | grep -qE '[[:space:]]+--recursive' && HAS_R=true
    echo "$COMMAND" | grep -qE '[[:space:]]+--force' && HAS_F=true

    if [[ "$HAS_R" = true && "$HAS_F" = true ]]; then
        # Check for dangerous targets: standalone /, ~, ~/, $HOME, $HOME/
        if echo "$COMMAND" | grep -qE '([[:space:]]|/)(/|~|\$HOME)([[:space:]]|$)' || \
           echo "$COMMAND" | grep -qE '[[:space:]](~/|\$HOME/)([[:space:]]|$)'; then
            echo "Blocked: \`rm -rf /\` (or ~ / \$HOME) would delete critical filesystem content." >&2
            exit 1
        fi
    fi
fi

# git push --force / -f (but not --force-with-lease)
if echo "$COMMAND" | grep -qE 'git[[:space:]]+push[[:space:]]+.*--force($|[[:space:]])' && ! echo "$COMMAND" | grep -q '\-\-force-with-lease'; then
    echo "Blocked: \`git push --force\` can overwrite remote history. Use \`git push --force-with-lease\` instead." >&2
    exit 1
fi
if echo "$COMMAND" | grep -qE 'git[[:space:]]+push[[:space:]]+.*[[:space:]]-[a-zA-Z]*f' && ! echo "$COMMAND" | grep -q '\-\-force-with-lease'; then
    echo "Blocked: \`git push -f\` can overwrite remote history. Use \`git push --force-with-lease\` instead." >&2
    exit 1
fi

# git reset --hard
if echo "$COMMAND" | grep -qE 'git[[:space:]]+reset[[:space:]]+--hard'; then
    echo "Blocked: \`git reset --hard\` discards all uncommitted changes. Use \`git stash\` to preserve work." >&2
    exit 1
fi

# git clean with both -f and -d flags (any order, combined or separate)
if echo "$COMMAND" | grep -qE 'git[[:space:]]+clean[[:space:]]'; then
    CLEAN_HAS_F=false
    CLEAN_HAS_D=false
    echo "$COMMAND" | grep -qE '[[:space:]]+-[a-zA-Z]*f' && CLEAN_HAS_F=true
    echo "$COMMAND" | grep -qE '[[:space:]]+-[a-zA-Z]*d' && CLEAN_HAS_D=true
    if [[ "$CLEAN_HAS_F" = true && "$CLEAN_HAS_D" = true ]]; then
        echo "Blocked: \`git clean -fd\` permanently deletes untracked files. Use \`git stash --include-untracked\` instead." >&2
        exit 1
    fi
fi

# git checkout -- . (discard all changes)
if echo "$COMMAND" | grep -qE 'git[[:space:]]+checkout[[:space:]]+--[[:space:]]+\.'; then
    echo "Blocked: \`git checkout -- .\` discards all uncommitted changes. Use \`git stash\` to preserve work." >&2
    exit 1
fi

# git restore . (discard all changes)
if echo "$COMMAND" | grep -qE 'git[[:space:]]+restore[[:space:]]+\.'; then
    echo "Blocked: \`git restore .\` discards all uncommitted changes. Use \`git stash\` to preserve work." >&2
    exit 1
fi

# chmod -R 777
if echo "$COMMAND" | grep -qE 'chmod[[:space:]]+(-R[[:space:]]+777|777[[:space:]]+-R)'; then
    echo "Blocked: \`chmod -R 777\` sets insecure permissions. Use more restrictive permissions (e.g., 755 for directories, 644 for files)." >&2
    exit 1
fi

exit 0
