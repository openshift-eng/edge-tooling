#!/usr/bin/env bash
# Lint modified or all markdown files with markdownlint-cli2
#
# Usage:
#   Claude Code Stop hook:  piped JSON on stdin, outputs hookSpecificOutput
#   Git pre-commit hook:    called directly, exits non-zero on lint failure
#   Direct:                 ./scripts/lint-markdown.sh [--pre-commit] [--fix]
#   --fix                    pass through to markdownlint-cli2 (auto-fix where supported)
#   --check-all-files        lint all markdown files in the repo
#   BASE_REF                 optional; default main — used for git diff file lists
#   ARTIFACT_DIR             CI only (provided by runner); junit/json copied here before cleanup

set -euo pipefail

MARKDOWNLINT_CLI2_VERSION="${MARKDOWNLINT_CLI2_VERSION:-0.22.1}"

is_ci() {
  case "${OPENSHIFT_CI:-}" in
    true | 1 | yes | Yes | TRUE) return 0 ;;
  esac
  return 1
}

PRE_COMMIT=false
FIX=false
CHECK_ALL_FILES=false
for _arg in "$@"; do
  case "$_arg" in
    --pre-commit) PRE_COMMIT=true ;;
    --fix) FIX=true ;;
    --check-all-files) CHECK_ALL_FILES=true ;;
  esac
done

if ! command -v npx &>/dev/null; then
    exit 0
fi

# Determine working directory
if [ "$PRE_COMMIT" = true ]; then
    CWD="$(git rev-parse --show-toplevel)"
elif [ -t 0 ]; then
    CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
else
    INPUT=$(cat)
    if command -v jq &>/dev/null; then
        CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
    fi
    if [ -z "${CWD:-}" ]; then
        CWD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    fi
fi

cd "$CWD"

# CI often runs with a non-writable HOME (e.g. "/"), so pin npm cache to /tmp.
# Run markdownlint from /tmp in CI so formatter outputs are writable; pass
# --config "${CWD}/.markdownlint-cli2.jsonc" so rules/formatters load from the repo.
RUN_CWD="$CWD"
if is_ci; then
    export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-${TMPDIR:-/tmp}/npm-cache-${UID:-ci}}"
    mkdir -p "$NPM_CONFIG_CACHE"
    RUN_CWD="$(mktemp -d "${TMPDIR:-/tmp}/markdownlint-ci.XXXXXX")"
fi

# shellcheck disable=SC2329 # Invoked via trap.
cleanup() {
    rm -f "${CWD}/markdownlint-cli2-junit.xml" "${CWD}/markdownlint-cli2-results.json"
    if is_ci; then
        if [[ -f "${RUN_CWD}/markdownlint-cli2-junit.xml" ]]; then
            cp -f "${RUN_CWD}/markdownlint-cli2-junit.xml" "${ARTIFACT_DIR}/"
        else
            echo "Artifact not produced: ${RUN_CWD}/markdownlint-cli2-junit.xml" >&2
        fi

        if [[ -f "${RUN_CWD}/markdownlint-cli2-results.json" ]]; then
            cp -f "${RUN_CWD}/markdownlint-cli2-results.json" "${ARTIFACT_DIR}/"
        else
            echo "Artifact not produced: ${RUN_CWD}/markdownlint-cli2-results.json" >&2
        fi

        rm -rf "$RUN_CWD"
    fi
}
trap cleanup EXIT

# Resolve BASE_REF (default: main) to an existing ref for git diff.
resolve_base_ref() {
    local preferred="${BASE_REF:-main}"
    local candidate
    for candidate in "$preferred" "origin/${preferred}" "origin/main" "main" "HEAD"; do
        if git rev-parse --verify --quiet "${candidate}^{commit}" &>/dev/null; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    printf 'HEAD'
}

# Resolve best diff base for CI merge jobs.
resolve_ci_diff_base() {
    if [[ -n "${PULL_BASE_SHA:-}" ]] && git rev-parse --verify --quiet "${PULL_BASE_SHA}^{commit}" &>/dev/null; then
        printf '%s' "${PULL_BASE_SHA}"
        return 0
    fi
    if git rev-parse --verify --quiet "HEAD^1" &>/dev/null; then
        printf 'HEAD^1'
        return 0
    fi
    resolve_base_ref
}

# Find .md files to lint based on context
ALL_FILES=""
if [[ "$CHECK_ALL_FILES" == true ]]; then
    ALL_FILES="**/*.md"
elif [[ "$FIX" == true ]]; then
    # Include unstaged + staged vs BASE_REF (no --cached).
    ALL_FILES=$(git diff "$(resolve_base_ref)" --name-only --diff-filter=ACM -- '*.md' 2>/dev/null || true)
elif is_ci; then
    # Direct CI run without --pre-commit.
    ALL_FILES=$(git diff "$(resolve_ci_diff_base)" --name-only --diff-filter=ACM -- '*.md' 2>/dev/null || true)
elif [[ "$PRE_COMMIT" == true ]]; then
    # Local hook: index-only (staged).
    ALL_FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.md' 2>/dev/null || true)
fi

if [ -z "$ALL_FILES" ]; then
    echo "No files to lint"
    exit 0
fi

# Filter to files that exist (use array for safe handling of special characters)
FILES_TO_LINT=()
while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    if is_ci && [[ "$file" != /* ]]; then
        file="${CWD}/${file}"
    fi
    FILES_TO_LINT+=("$file")
done <<< "$ALL_FILES"

echo "Linting files"

LINT_EXIT=0
markdownlint_cli2_args=()
[[ "$FIX" == true ]] && markdownlint_cli2_args+=(--fix)
if is_ci && [[ -f "${CWD}/.markdownlint-cli2.jsonc" ]]; then
    markdownlint_cli2_args+=(--config "${CWD}/.markdownlint-cli2.jsonc")
fi
markdownlint_cli2_args+=("${FILES_TO_LINT[@]}")
LINT_OUTPUT=$(cd "$RUN_CWD" && npx --yes \
    -p "markdownlint-cli2@${MARKDOWNLINT_CLI2_VERSION}" \
    -p markdownlint-cli2-formatter-pretty \
    -p markdownlint-cli2-formatter-junit \
    -p markdownlint-cli2-formatter-json \
    markdownlint-cli2 "${markdownlint_cli2_args[@]}" 2>&1) || LINT_EXIT=$?

if [ "$LINT_EXIT" -eq 0 ]; then
    exit 0
fi

# Lint failed: pre-commit always prints to stderr for the human / CI log.
if [[ "$PRE_COMMIT" == true ]] || is_ci; then
    echo "$LINT_OUTPUT" >&2
    echo "" >&2
    echo "Fix markdownlint errors before committing." >&2
    exit 1
fi

# Claude Code Stop hook — structured JSON; errors = parsed markdownlint-cli2-results.json.
# The output is truncated to limit the context message length.
RESULTS_JSON="${CWD}/markdownlint-cli2-results.json"
CONTEXT_MSG='MARKDOWN LINT ERRORS FOUND in modified .md files. Please fix these issues'
if command -v jq &>/dev/null; then
    if [[ -f "$RESULTS_JSON" ]]; then
        jq -cn --slurpfile err "$RESULTS_JSON" --arg msg "$CONTEXT_MSG" '{hookSpecificOutput:{hookEventName:"Stop",errors:$err[0],additionalContext:$msg}}'
    else
        jq -cn --arg lint "$LINT_OUTPUT" --arg msg "$CONTEXT_MSG" '{hookSpecificOutput:{hookEventName:"Stop",errors:{cliOutput:$lint},additionalContext:$msg}}'
    fi
fi

exit 0
