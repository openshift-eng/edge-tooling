#!/bin/bash
# Detect out-of-date git submodules and prompt user to update
# This hook runs at session start and reports stale submodules to Claude

set -euo pipefail

# Require jq for JSON parsing
if ! command -v jq &>/dev/null; then
    echo "Error: jq is required but not installed. Install it with your package manager (e.g., 'sudo dnf install jq')." >&2
    exit 1
fi

# Read hook input
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Use CWD from hook input, fall back to script location
if [ -z "$CWD" ]; then
    CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

cd "$CWD"

# Exit silently if no .gitmodules file exists
if [ ! -f ".gitmodules" ]; then
    exit 0
fi

# Silently initialize any uninitialized submodules
git submodule update --init 2>/dev/null || true

# Collect stale submodule info
declare -a STALE_SUBMODULES=()

# Iterate over submodules using process substitution to avoid subshell scoping
while IFS= read -r sm_path; do
    [ -z "$sm_path" ] && continue
    [ -d "$sm_path" ] || continue

    sm_name=$(git submodule--helper name "$sm_path" 2>/dev/null || basename "$sm_path")

    # Resolve tracking branch: .gitmodules config > origin/HEAD > main > master
    branch=$(git config -f .gitmodules "submodule.${sm_name}.branch" 2>/dev/null || true)
    if [ -z "$branch" ]; then
        # Try to resolve origin/HEAD
        origin_head=$(git -C "$sm_path" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null || true)
        if [ -n "$origin_head" ]; then
            branch="${origin_head##refs/remotes/origin/}"
        fi
    fi
    if [ -z "$branch" ]; then
        # Try main, then master
        if git -C "$sm_path" rev-parse --verify "refs/remotes/origin/main" &>/dev/null; then
            branch="main"
        elif git -C "$sm_path" rev-parse --verify "refs/remotes/origin/master" &>/dev/null; then
            branch="master"
        else
            # Cannot determine branch, skip
            continue
        fi
    fi

    # Fetch from remote (suppress all output; skip on failure)
    git -C "$sm_path" fetch origin "$branch" 2>/dev/null || continue

    # Get current pinned commit and remote branch tip
    local_commit=$(git -C "$sm_path" rev-parse HEAD 2>/dev/null || true)
    remote_commit=$(git -C "$sm_path" rev-parse "origin/${branch}" 2>/dev/null || true)

    if [ -z "$local_commit" ] || [ -z "$remote_commit" ]; then
        continue
    fi

    # Skip if already up to date
    if [ "$local_commit" = "$remote_commit" ]; then
        continue
    fi

    # Count commits behind
    behind_count=$(git -C "$sm_path" rev-list --count "HEAD..origin/${branch}" 2>/dev/null || echo "0")

    if [ "$behind_count" -gt 0 ]; then
        local_short=$(git -C "$sm_path" rev-parse --short HEAD 2>/dev/null)
        remote_short=$(git -C "$sm_path" rev-parse --short "origin/${branch}" 2>/dev/null)
        STALE_SUBMODULES+=("${sm_name}|${sm_path}|${branch}|${behind_count}|${local_short}|${remote_short}")
    fi
done < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null | awk '{print $2}')

# Output results if stale submodules found
if [ ${#STALE_SUBMODULES[@]} -gt 0 ]; then
    # Build a human-readable summary for the context message
    details=""
    for entry in "${STALE_SUBMODULES[@]}"; do
        IFS='|' read -r name path branch count old_hash new_hash <<< "$entry"
        details="${details}- ${name} (${path}): ${count} commits behind origin/${branch} (${old_hash} -> ${new_hash})\\n"
    done

    # Escape for JSON
    details_escaped=$(printf '%s' "$details" | sed 's/\\/\\\\/g; s/"/\\"/g')

    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "STALE SUBMODULES DETECTED: The following git submodules are behind their remote tracking branch:\\n${details_escaped}\\nPlease inform the user which submodules are out of date and by how many commits, then ask if they would like to update them. If yes, run 'git submodule update --remote <path>' for each, then 'git add <path>' and commit with a descriptive message including the old and new short hashes and commit count."
  }
}
EOF
fi

exit 0
