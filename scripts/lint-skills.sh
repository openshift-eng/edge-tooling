#!/usr/bin/env bash
# Lint modified SKILL.md files for content quality
#
# Usage:
#   Claude Code hook:  ./scripts/lint-skills.sh --hook    (reads JSON stdin, outputs JSON)
#   Direct:            ./scripts/lint-skills.sh [OPTIONS] [FILE...]
#   Run with --help for full usage.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: lint-skills.sh [OPTIONS] [FILE...]

Lint SKILL.md files for content quality.

Options:
  --check-all-files      Lint all SKILL.md files in plugins/
  --severity <level>     Minimum severity to report: error, warning (default: warning)
  --hook                 Run in hook mode (read JSON from stdin, output JSON)
  -h, --help             Show this help message

Arguments:
  FILE                   One or more SKILL.md paths (default: changed files vs main)

Examples:
  ./scripts/lint-skills.sh                                  # Lint changed files
  ./scripts/lint-skills.sh --check-all-files                # Lint all skills
  ./scripts/lint-skills.sh --severity error                 # Only show errors
  ./scripts/lint-skills.sh plugins/foo/skills/bar/SKILL.md  # Lint specific file
EOF
}

CHECK_ALL_FILES=false
SEVERITY=""
JSON_MODE=false
FILES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --check-all-files) CHECK_ALL_FILES=true; shift ;;
    --hook) JSON_MODE=true; shift ;;
    --severity)
      if [[ -z "${2:-}" || ( "$2" != "error" && "$2" != "warning" ) ]]; then
        echo "Error: --severity requires 'error' or 'warning'" >&2
        exit 1
      fi
      SEVERITY="$2"; shift 2 ;;
    *SKILL.md) FILES+=("$1"); shift ;;
    *)
      echo "Error: unknown argument '${1}'" >&2
      echo "Run 'lint-skills.sh --help' for usage." >&2
      exit 1
      ;;
  esac
done

# Determine working directory before prerequisite checks,
# so prerequisite failures can emit proper JSON in hook mode.
CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$JSON_MODE" == true ]]; then
    INPUT=$(cat)
    if command -v jq &>/dev/null; then
        JQ_CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
        [[ -n "$JQ_CWD" ]] && CWD="$JQ_CWD"
    fi
fi

die() {
    if [[ "$JSON_MODE" == true ]]; then
        printf '{"decision":"block","reason":"%s"}\n' "$1"
        exit 0
    fi
    echo "Error: $1" >&2
    exit 1
}

if ! command -v python3 &>/dev/null; then
    die "python3 not found — cannot run skill linter"
fi

cd "$CWD"

SCRIPT_PATH="${CWD}/scripts/lint-skills.py"
if [[ ! -f "$SCRIPT_PATH" ]]; then
    die "${SCRIPT_PATH} not found — cannot run skill linter"
fi

LINT_ARGS=()
if [[ "$JSON_MODE" == true ]]; then
    LINT_ARGS+=(--json)
fi
if [[ "$CHECK_ALL_FILES" == true ]]; then
    LINT_ARGS+=(--check-all)
fi
if [[ -n "$SEVERITY" ]]; then
    LINT_ARGS+=(--severity "$SEVERITY")
fi

if [[ "$JSON_MODE" == true ]]; then
    # Fast path: skip Python startup when no SKILL.md files were changed.
    # Only skip if git succeeds AND reports no changes; fall through on git failure
    # so the Python linter's own ref resolution handles edge cases.
    if [[ "$CHECK_ALL_FILES" == false ]]; then
        BASE="${BASE_REF:-main}"
        CHANGED=$(git diff "origin/${BASE}" --name-only --diff-filter=ACM -- '*/SKILL.md' 2>/dev/null) || true
        if [[ -z "$CHANGED" ]] && git rev-parse --verify --quiet "origin/${BASE}" &>/dev/null; then
            exit 0
        fi
    fi
    # Hook mode: capture stderr so crashes are surfaced, not silenced
    STDERR_FILE=$(mktemp)
    LINT_OUTPUT=$(python3 "$SCRIPT_PATH" "${LINT_ARGS[@]}" 2>"$STDERR_FILE") || true
    if [[ -n "$LINT_OUTPUT" && "$LINT_OUTPUT" == "{"* ]]; then
        # Valid JSON output from linter (either blocking or empty)
        rm -f "$STDERR_FILE"
    elif [[ -s "$STDERR_FILE" ]]; then
        # Real crash: stderr has content and no valid JSON on stdout
        STDERR_CONTENT=$(head -5 "$STDERR_FILE" | tr '\n' ' ' | tr '"' "'")
        rm -f "$STDERR_FILE"
        printf '{"decision":"block","reason":"Skill linter crashed: %s"}\n' "$STDERR_CONTENT"
        exit 0
    else
        rm -f "$STDERR_FILE"
    fi
    if [[ -n "$LINT_OUTPUT" && "$LINT_OUTPUT" != "{}" ]]; then
        echo "$LINT_OUTPUT"
    fi
else
    # Direct mode: propagate linter exit code so make targets and CI fail on errors
    python3 "$SCRIPT_PATH" --color "${LINT_ARGS[@]}" "${FILES[@]}"
fi
