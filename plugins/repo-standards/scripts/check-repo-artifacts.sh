#!/usr/bin/bash
# Check repository for required agentic development artifacts
# SessionStart hook — outputs hookSpecificOutput JSON if artifacts are missing

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

# Track missing artifacts
declare -a MISSING=()

# Required artifacts
[ ! -f "README.md" ] && MISSING+=("README.md")
[ ! -f "CONTRIBUTING.md" ] && MISSING+=("CONTRIBUTING.md")

# AGENTS.md or CLAUDE.md (at least one must exist)
HAS_AGENTS=false
if [ -f "AGENTS.md" ] || [ -f "CLAUDE.md" ]; then
    HAS_AGENTS=true
fi
[ "$HAS_AGENTS" = false ] && MISSING+=("AGENTS.md")

# .coderabbit.yaml
[ ! -f ".coderabbit.yaml" ] && MISSING+=(".coderabbit.yaml")

# Check CLAUDE.md symlink convention
SYMLINK_WARN=""
if [ -f "AGENTS.md" ] && [ -f "CLAUDE.md" ]; then
    if [ ! -L "CLAUDE.md" ]; then
        SYMLINK_WARN="CLAUDE.md exists but is not a symlink to AGENTS.md. Convention requires: ln -s AGENTS.md CLAUDE.md"
    elif [ "$(readlink CLAUDE.md)" != "AGENTS.md" ]; then
        SYMLINK_WARN="CLAUDE.md is a symlink but does not point to AGENTS.md (points to: $(readlink CLAUDE.md)). Convention requires: ln -sf AGENTS.md CLAUDE.md"
    fi
elif [ -f "CLAUDE.md" ] && [ ! -f "AGENTS.md" ]; then
    SYMLINK_WARN="CLAUDE.md exists without AGENTS.md. Convention requires AGENTS.md as the primary file with CLAUDE.md as a symlink."
fi

# Exit silently if all present and no warnings
if [ ${#MISSING[@]} -eq 0 ] && [ -z "$SYMLINK_WARN" ]; then
    exit 0
fi

# Build message
MSG=""
if [ ${#MISSING[@]} -gt 0 ]; then
    ARTIFACT_LIST=$(printf '%s, ' "${MISSING[@]}" | sed 's/, $//')
    MSG="MISSING REPO ARTIFACTS: The following required files are missing: ${ARTIFACT_LIST}."
fi

if [ -n "$SYMLINK_WARN" ]; then
    [ -n "$MSG" ] && MSG="${MSG} "
    MSG="${MSG}SYMLINK WARNING: ${SYMLINK_WARN}"
fi

MSG="${MSG} Run /repo-standards:scaffold-repo to generate missing artifacts."

# Escape for JSON
MSG_ESCAPED=$(printf '%s' "$MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')

cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${MSG_ESCAPED}"
  }
}
EOF

exit 0
