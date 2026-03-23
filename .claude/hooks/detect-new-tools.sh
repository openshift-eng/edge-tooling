#!/bin/bash
# Detect new tool directories in edge-tooling repository
# This hook runs at session start and notifies if new tools need documentation

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

# Documented tools in CLAUDE.md (update this list when adding new tools)
DOCUMENTED_TOOLS=(
    "two-node-toolbox"
    "ec2-deploy"
    "sno-deploy"
    "environments/lvm-operator"
    "plugins"
)

# Function to check if a directory looks like a tool
is_tool_directory() {
    local dir="$1"
    # A tool directory typically has a Makefile or README.md and substantive content
    if [ -f "$dir/Makefile" ] || [ -f "$dir/README.md" ]; then
        # Exclude common non-tool directories
        local basename
        basename=$(basename "$dir")
        case "$basename" in
            .git|.github|.claude|docs|hack|scripts|vendor|node_modules)
                return 1
                ;;
            *)
                return 0
                ;;
        esac
    fi
    return 1
}

# Find potential tool directories
declare -a FOUND_TOOLS=()

# Check top-level directories
for dir in "$CWD"/*/; do
    [ -d "$dir" ] || continue
    dirname=$(basename "$dir")
    if is_tool_directory "$dir"; then
        FOUND_TOOLS+=("$dirname")
    fi
done

# Check environments subdirectories
if [ -d "$CWD/environments" ]; then
    for dir in "$CWD/environments"/*/; do
        [ -d "$dir" ] || continue
        dirname=$(basename "$dir")
        if is_tool_directory "$dir"; then
            FOUND_TOOLS+=("environments/$dirname")
        fi
    done
fi

# Compare found tools against documented tools
declare -a NEW_TOOLS=()
for found in "${FOUND_TOOLS[@]}"; do
    documented=false
    for doc in "${DOCUMENTED_TOOLS[@]}"; do
        if [ "$found" = "$doc" ]; then
            documented=true
            break
        fi
    done
    if [ "$documented" = false ]; then
        NEW_TOOLS+=("$found")
    fi
done

# Output results if new tools found
if [ ${#NEW_TOOLS[@]} -gt 0 ]; then
    TOOL_LIST=$(printf '%s, ' "${NEW_TOOLS[@]}" | sed 's/, $//')

    # Display notification to user via stderr
    echo "⚠️  New tool directories detected: ${TOOL_LIST}" >&2
    echo "   Not documented in CLAUDE.md - ask Claude to update it" >&2

    # Return context for Claude to see and act on
    cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "NEW TOOLS DETECTED: The following tool directories exist but are not documented in the root CLAUDE.md file: ${TOOL_LIST}. Please notify the user and offer to update the CLAUDE.md file to include documentation for these new tools."
  }
}
EOF
fi

exit 0
