#!/usr/bin/bash
# Validates daily report format for Slack posting
# Reports should use emoji format and follow formatting guidelines from CLAUDE.md

set -euo pipefail

ERRORS=0
WARNINGS=0

pass() { echo "✓ $1"; }
warn() { echo "⚠ WARNING: $1"; WARNINGS=$((WARNINGS + 1)); }
fail() { echo "✗ FAIL: $1"; ERRORS=$((ERRORS + 1)); }
info() { echo "ℹ $1"; }

usage() {
    local exit_code="${1:-0}"
    cat <<EOF
Usage: $(basename "$0") <report-file>

Validates daily report format:
  - Emoji usage (:done-circle-check:, :in-progress:, :jira-blocker:)
  - Header line present
  - Jira ticket format (TICKET-ID: Description (URL))
  - Plain text (no markdown code blocks)
  - Consolidation (warnings only)

Exit codes:
  0 - All checks passed
  1 - Validation errors
  2 - Warnings only (set STRICT=1 to treat as errors)

Examples:
  $(basename "$0") report.txt
  STRICT=1 $(basename "$0") report.txt
EOF
    exit "$exit_code"
}

# Parse arguments
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
fi

if [ $# -ne 1 ]; then
    echo "Error: One argument required" >&2
    echo "" >&2
    usage 1
fi

REPORT_FILE="$1"
STRICT="${STRICT:-0}"

# Check file exists
if [ ! -f "$REPORT_FILE" ]; then
    fail "Report file not found: $REPORT_FILE"
    exit 1
fi

info "Validating: $REPORT_FILE"
echo ""

# Check 1: Header line present
echo "=== Header Line ==="
if head -n 1 "$REPORT_FILE" | grep -qE '^(Daily Report|Status Update|Today|Update):'; then
    pass "Header line present"
else
    fail "Missing header line (should start with 'Daily Report:', 'Status Update:', etc.)"
fi
echo ""

# Check 2: Emoji format
echo "=== Emoji Format ==="
VALID_EMOJIS=":done-circle-check:|:in-progress:|:jira-blocker:"
INVALID_EMOJI_PATTERNS='^- \[x\]|^- \[ \]|✓|✗|🔴|🟢|🟡'

if grep -qE "$VALID_EMOJIS" "$REPORT_FILE"; then
    pass "Valid emoji format found"
else
    fail "No valid emojis found (expected: :done-circle-check:, :in-progress:, :jira-blocker:)"
fi

if grep -qE "$INVALID_EMOJI_PATTERNS" "$REPORT_FILE"; then
    fail "Invalid emoji format found (markdown checkboxes or unicode emojis)"
    info "  Replace '- [x]' with ':done-circle-check:'"
    info "  Replace '- [ ]' with ':in-progress:'"
fi
echo ""

# Check 3: Jira ticket format
echo "=== Jira Ticket Format ==="
# Match any Jira project format: PROJECT-123 (uppercase letters/numbers, hyphen, numbers)
JIRA_PATTERN="[A-Z][A-Z0-9]+-[0-9]+"

JIRA_REFS=$(grep -oE "$JIRA_PATTERN" "$REPORT_FILE" | sort -u || true)
if [ -n "$JIRA_REFS" ]; then
    PROPER_FORMAT=0
    while IFS= read -r ticket; do
        # Check if the ticket appears on a line with its corresponding URL
        # Supports formats:
        #   - TICKET-123: Description (URL)
        #   - TICKET-123, TICKET-456: Description (URL1, URL2)
        #   - :emoji: TICKET-123: Description (URL)
        if grep -F "$ticket" "$REPORT_FILE" | grep -qF "https://redhat.atlassian.net/browse/$ticket"; then
            PROPER_FORMAT=$((PROPER_FORMAT + 1))
        else
            warn "Jira ticket $ticket missing URL or wrong format (expected: $ticket: Description (URL) or $ticket, ...: Description (URL, ...))"
        fi
    done <<< "$JIRA_REFS"

    if [ "$PROPER_FORMAT" -gt 0 ]; then
        pass "$PROPER_FORMAT Jira tickets in proper format"
    fi
else
    info "No Jira tickets found"
fi
echo ""

# Check 4: No markdown code blocks
echo "=== Plain Text Format ==="
if grep -qE "^\`\`\`" "$REPORT_FILE"; then
    fail "Markdown code blocks found (report should be plain text)"
    info "  Remove code fences (\`\`\`) for Slack compatibility"
else
    pass "No markdown code blocks found"
fi
echo ""

# Check 5: Consolidation warnings (soft check)
echo "=== Consolidation Check ==="
BULLETS=$(grep -E "^($VALID_EMOJIS)" "$REPORT_FILE" | sed -E "s/^($VALID_EMOJIS) //" || true)
if [ -n "$BULLETS" ]; then
    BULLET_COUNT=$(printf '%s\n' "$BULLETS" | awk 'END{print NR}')

    if [ "$BULLET_COUNT" -gt 20 ]; then
        warn "Report has $BULLET_COUNT bullets - consider consolidating"
        info "  Group related items: multiple PRs/tickets for same work → one bullet"
    else
        pass "Bullet count reasonable ($BULLET_COUNT bullets)"
    fi
else
    warn "No emoji bullets found"
fi
echo ""

# Summary
echo "=== Summary ==="
if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo "✅ All checks passed!"
    exit 0
elif [ "$ERRORS" -eq 0 ]; then
    echo "⚠️  $WARNINGS warning(s) found"
    if [ "$STRICT" = "1" ]; then
        echo "STRICT mode enabled - treating warnings as errors"
        exit 1
    fi
    exit 2
else
    echo "❌ $ERRORS error(s), $WARNINGS warning(s) found"
    exit 1
fi
