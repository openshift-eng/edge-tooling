#!/bin/bash
set -euo pipefail

cat <<'EOF'
{
  "hookSpecificOutput": {
    "additionalContext": "A plan was just approved. Ask the user: 'Would you like to create or update Jira tickets for the items in this plan?' If yes, read the plan file, extract actionable work items, and for each one ask whether to create a new ticket or update an existing one. Enforce Edge Scrum Laws for any tickets created (required fields, valid components, SP rules, version format)."
  }
}
EOF
