#!/usr/bin/bash

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${TEST_DIR}/../request_art_builds.sh"
FIXTURES="${TEST_DIR}/fixtures"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/request_art_builds_test.XXXXXX")"
trap 'rm -rf "${TEST_ROOT}"' EXIT
SYNTHETIC_VALUE="synthetic-${RANDOM}-${RANDOM}"
test -x "${FIXTURES}/curl"

run_case() {
  local scenario="$1"
  shift
  CASE_DIR="$(mktemp -d "${TEST_ROOT}/${scenario}.XXXXXX")"
  mkdir -p "${CASE_DIR}/tmp" "${CASE_DIR}/state"
  : > "${CASE_DIR}/requests.jsonl"
  : > "${CASE_DIR}/slack.jsonl"
  printf 'Running: %s %s\n' "${scenario}" "$*"
  env -i \
    PATH="${FIXTURES}:/usr/bin:/bin" \
    HOME="${CASE_DIR}" TMPDIR="${CASE_DIR}/tmp" LC_ALL=C \
    JIRA_BASE_URL="${CASE_JIRA_BASE_URL:-https://jira.example.test}" \
    JIRA_USER_EMAIL="operator@example.test" \
    JIRA_API_TOKEN="${SYNTHETIC_VALUE}" \
    SLACK_WEBHOOK_URL="https://hooks.example.test/mock" \
    MOCK_SCENARIO="${scenario}" MOCK_ART_KEY="ART-55" MOCK_QE_KEY="USHIFT-66" \
    MOCK_REQUEST_LOG="${CASE_DIR}/requests.jsonl" \
    MOCK_SLACK_LOG="${CASE_DIR}/slack.jsonl" \
    MOCK_SLACK_URL="https://hooks.example.test/mock" \
    MOCK_STATE_DIR="${CASE_DIR}/state" \
    "${SCRIPT}" 4.20.7 2026-11-18 2026-11-20 --reason test "$@" \
    > "${CASE_DIR}/stdout" 2> "${CASE_DIR}/stderr"
}

run_case new_success
jq -e -s '
  [.[] | select(.method == "POST" and (.url | endswith("/issue")))
    | .data | fromjson | .fields.project.key] | sort == ["ART", "USHIFT"]
' "${CASE_DIR}/requests.jsonl" >/dev/null
jq -e -s '
  [.[] | select(.method == "POST" and (.url | endswith("/issueLink")))
    | .data | fromjson] == [{type: {id: "500"},
      inwardIssue: {key: "USHIFT-202"}, outwardIssue: {key: "ART-101"}}]
' "${CASE_DIR}/requests.jsonl" >/dev/null
jq -e -s 'length == 1 and (.[0].text | contains("ART-101"))' \
  "${CASE_DIR}/slack.jsonl" >/dev/null

run_case reuse_desired
test "$(cat "${CASE_DIR}/stdout")" = "ART-55 USHIFT-66"
jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null
test ! -s "${CASE_DIR}/slack.jsonl"

run_case dry_run --dry-run
jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null
test ! -s "${CASE_DIR}/slack.jsonl"

run_case reuse_desired --resend-slack
jq -e -s '
  all(.[]; .method == "GET" or .url == "https://hooks.example.test/mock")
' "${CASE_DIR}/requests.jsonl" >/dev/null
jq -e -s 'length == 1 and (.[0].text | contains("ART-55"))' \
  "${CASE_DIR}/slack.jsonl" >/dev/null

SYNTHETIC_USER="synthetic-${RANDOM}"
SYNTHETIC_PASSWORD="synthetic-${RANDOM}"
CASE_JIRA_BASE_URL="https://${SYNTHETIC_USER}:${SYNTHETIC_PASSWORD}@jira.example.test"
if run_case validation; then
  printf 'FAIL: credential-bearing Jira URL was accepted\n' >&2
  exit 1
fi
grep -Fq 'JIRA_BASE_URL must be an HTTPS URL without embedded credentials' "${CASE_DIR}/stderr"
test ! -s "${CASE_DIR}/requests.jsonl"
if grep -Fq "${CASE_JIRA_BASE_URL#https://}" "${CASE_DIR}/stderr"; then
  printf 'FAIL: credential-bearing Jira URL was logged\n' >&2
  exit 1
fi

printf 'PASS: all 5 offline cases\n'
