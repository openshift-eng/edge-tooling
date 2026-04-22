#!/bin/bash
# Check .coderabbit.yaml configuration quality
# NOT a hook — called by health-check skill
# Usage: check-coderabbit-config.sh [directory]

set -euo pipefail

DIR="${1:-.}"
CONFIG="${DIR}/.coderabbit.yaml"

# Default results
EXISTS=false
AUTO_REVIEW=false
PATH_FILTERS=false
INSTRUCTIONS=false

if [ -f "$CONFIG" ]; then
    EXISTS=true

    # Use yq if available, fall back to grep
    if command -v yq &>/dev/null; then
        yq e '.reviews.auto_review // .auto_review // ""' "$CONFIG" 2>/dev/null | grep -qi "true\|enabled" && AUTO_REVIEW=true
        yq e '.reviews.path_filters // .path_filters // ""' "$CONFIG" 2>/dev/null | grep -q "." && PATH_FILTERS=true
        yq e '.reviews.instructions // .instructions // ""' "$CONFIG" 2>/dev/null | grep -q "." && INSTRUCTIONS=true
    else
        grep -qE '^\s*auto_review\s*:' "$CONFIG" 2>/dev/null && AUTO_REVIEW=true
        grep -qE '^\s*path_filters\s*:' "$CONFIG" 2>/dev/null && PATH_FILTERS=true
        grep -qE '^\s*(instructions|review_instructions)\s*:' "$CONFIG" 2>/dev/null && INSTRUCTIONS=true
    fi
fi

PASS=true
if [ "$EXISTS" = false ] || [ "$AUTO_REVIEW" = false ]; then
    PASS=false
fi

cat <<EOF
{"exists": ${EXISTS}, "auto_review": ${AUTO_REVIEW}, "path_filters": ${PATH_FILTERS}, "instructions": ${INSTRUCTIONS}, "pass": ${PASS}}
EOF
