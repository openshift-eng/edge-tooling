#!/usr/bin/env bash
# Validates the Edge Scrum Laws directory structure post-restructure.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAWS_DIR="$SCRIPT_DIR/laws"
PLUGIN_DIR="$(dirname "$SCRIPT_DIR")"
ERRORS=0

pass() { echo "OK:   $1"; }
fail() { echo "FAIL: $1"; ERRORS=$((ERRORS + 1)); }

check_file()     { [ -f "$1" ] && pass "exists: $1" || fail "missing: $1"; }
check_preamble() { [ -f "$1" ] && grep -q "RFC 2119" "$1" && pass "RFC 2119 preamble: $(basename "$1")" || fail "missing RFC 2119 preamble: $(basename "$1")"; }
check_index()    { grep -qF "$1" "$SCRIPT_DIR/Edge-Scrum-Laws.md" 2>/dev/null && pass "index links: $1" || fail "index missing link to: $1"; }
check_no_old()   { [ -f "$1" ] || { fail "file missing: $(basename "$1")"; return; }; ! grep -q "edge-scrum/Edge-Scrum-Laws\.md" "$1" && pass "no old path: $(basename "$1")" || fail "old path still in: $(basename "$1")"; }
check_new()      { grep -q "references/Edge-Scrum-Laws\.md\|references/laws/" "$1" 2>/dev/null && pass "new path in: $(basename "$1")" || fail "new path missing from: $(basename "$1")"; }

echo "=== Index ==="
check_file "$SCRIPT_DIR/Edge-Scrum-Laws.md"

echo "=== Normative files ==="
LAWS=(
    "00-team-roster.md"
    "01-jira-projects.md"
    "02-jira-stories.md"
    "03-jira-bugs.md"
    "04-jira-epics.md"
    "05-jira-features.md"
    "06-jira-fields.md"
    "07-workflow-states.md"
    "08-ceremonies.md"
    "09-sprint-policies.md"
    "10-bug-triage.md"
    "11-work-prioritization.md"
    "12-epic-feature-refinement.md"
    "13-roles.md"
    "14-agent-conventions.md"
)
for f in "${LAWS[@]}"; do
    check_file "$LAWS_DIR/$f"
    check_preamble "$LAWS_DIR/$f"
    check_index "$f"
done

echo "=== Old file removed ==="
[ ! -f "$PLUGIN_DIR/Edge-Scrum-Laws.md" ] && pass "old Edge-Scrum-Laws.md removed" || fail "old Edge-Scrum-Laws.md still exists at plugin root"

echo "=== Skill/agent path updates ==="
for f in \
    "$PLUGIN_DIR/README.md" \
    "$PLUGIN_DIR/skills/create-epic/SKILL.md" \
    "$PLUGIN_DIR/skills/release-health/SKILL.md" \
    "$PLUGIN_DIR/skills/release-health-analysis/SKILL.md"; do
    check_no_old "$f"
    check_new "$f"
done

echo ""
[ "$ERRORS" -eq 0 ] && echo "All checks passed!" && exit 0
echo "$ERRORS check(s) failed." && exit 1
