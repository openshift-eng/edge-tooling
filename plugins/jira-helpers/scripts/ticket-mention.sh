#!/bin/bash
set -euo pipefail

command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')

if [[ -z "$PROMPT" ]]; then
    exit 0
fi

KEYS=$(echo "$PROMPT" | grep -oE '[A-Z][A-Z0-9]+-[0-9]+' || true)

if [[ -z "$KEYS" ]]; then
    exit 0
fi

SESSION_FILE="/tmp/jira-helpers-seen-tickets-${PPID}"

SEEN=""
if [[ -f "$SESSION_FILE" ]]; then
    SEEN=$(cat "$SESSION_FILE")
fi

NEW_KEYS=""
while IFS= read -r key; do
    if ! echo "$SEEN" | grep -qxF "$key"; then
        NEW_KEYS="${NEW_KEYS:+${NEW_KEYS}
}${key}"
    fi
done <<< "$KEYS"

if [[ -z "$NEW_KEYS" ]]; then
    exit 0
fi

echo "$NEW_KEYS" >> "$SESSION_FILE"

UNIQUE_KEYS=$(echo "$NEW_KEYS" | sort -u)

JSON_ARRAY=$(echo "$UNIQUE_KEYS" | jq -R . | jq -s .)
TICKET_LIST=$(echo "$UNIQUE_KEYS" | paste -sd ', ')

jq -n \
    --argjson tickets "$JSON_ARRAY" \
    --arg ticketList "$TICKET_LIST" \
    '{
        "hookSpecificOutput": {
            "detectedTickets": $tickets,
            "additionalContext": ("Jira tickets detected in prompt: " + $ticketList + ". For each ticket, fetch it with jira_get_issue and run a quick critical-only validation check: (1) if Bug, SP must be 0, (2) if Story/Spike/Task, must have Epic Link, (3) if Epic, QA Contact and Doc Contact must not be blank, (4) SP must be Fibonacci (0,1,2,3,5,8,13). Report any issues found in one brief line per finding. If all clean, say nothing.")
        }
    }'
