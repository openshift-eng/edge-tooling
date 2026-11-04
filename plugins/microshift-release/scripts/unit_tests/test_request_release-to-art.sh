#!/usr/bin/bash

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${TEST_DIR}/../request_release-to-art.sh"
FIXTURES="${TEST_DIR}/fixtures"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/request_release-to-art_test.XXXXXX")"
trap 'rm -rf "${TEST_ROOT}"' EXIT
SYNTHETIC_VALUE="synthetic-${RANDOM}-${RANDOM}"
SYNTHETIC_WEBHOOK="https://hooks.example.test/synthetic-${RANDOM}-${RANDOM}"
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
    JIRA_USERNAME="operator@example.test" \
    JIRA_API_TOKEN="${SYNTHETIC_VALUE}" \
    SLACK_WEBHOOK_URL="${CASE_WEBHOOK_URL-${SYNTHETIC_WEBHOOK}}" \
    MOCK_SCENARIO="${scenario}" MOCK_ART_KEY="ART-55" MOCK_QE_KEY="USHIFT-66" \
    MOCK_REQUEST_LOG="${CASE_DIR}/requests.jsonl" \
    MOCK_SLACK_LOG="${CASE_DIR}/slack.jsonl" \
    MOCK_SLACK_URL="${SYNTHETIC_WEBHOOK}" \
    MOCK_STATE_DIR="${CASE_DIR}/state" \
    /usr/bin/bash "${CASE_SHELL_OPTION:-+x}" "${SCRIPT}" \
    4.20.7 2026-11-18 2026-11-20 --reason "${CASE_REASON:-test}" "$@" \
    > "${CASE_DIR}/stdout" 2> "${CASE_DIR}/stderr"
}

run_case new_success
jq -e -s '
  [.[] | select(.method == "POST" and (.url | endswith("/issue")))
    | .data | fromjson | .fields.project.key] | sort == ["ART", "USHIFT"]
' "${CASE_DIR}/requests.jsonl" >/dev/null
jq -e -s '
  [.[] | select(.method == "POST" and (.url | endswith("/issueLink")))
    | .data | fromjson] == [
      {type: {id: "500"}, inwardIssue: {key: "USHIFT-202"}, outwardIssue: {key: "ART-101"}},
      {type: {id: "501"}, inwardIssue: {key: "USHIFT-202"}, outwardIssue: {key: "USHIFT-6945"}}]
' "${CASE_DIR}/requests.jsonl" >/dev/null
jq -e -s 'length == 1 and (.[0].text | contains("ART-101"))' \
  "${CASE_DIR}/slack.jsonl" >/dev/null

# Related is non-directional: Jira may report the ART key as inwardIssue.
run_case inward_link
jq -e -s '[.[] | select(.method == "POST" and (.url | endswith("/issueLink")))] | length == 2' \
  "${CASE_DIR}/requests.jsonl" >/dev/null

run_case reuse_desired
test "$(cat "${CASE_DIR}/stdout")" = "ART-55 USHIFT-66"
jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null
test ! -s "${CASE_DIR}/slack.jsonl"

run_case dry_run --dry-run
jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null
test ! -s "${CASE_DIR}/slack.jsonl"

run_case reuse_desired --resend-slack
jq -e -s '
  all(.[]; .method == "GET" or (.url | startswith("https://hooks.example.test/")))
' "${CASE_DIR}/requests.jsonl" >/dev/null
jq -e -s 'length == 1 and (.[0].text | contains("ART-55"))' \
  "${CASE_DIR}/slack.jsonl" >/dev/null

if run_case readback_missing; then
  printf 'FAIL: link without readback verification was accepted\n' >&2
  exit 1
fi
grep -Fq 'readback verification failed' "${CASE_DIR}/stderr"

if run_case missing_cloners; then
  printf 'FAIL: missing Cloners link type was accepted\n' >&2
  exit 1
fi
grep -Fq "Link type 'Cloners'" "${CASE_DIR}/stderr"
jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null

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

unset CASE_JIRA_BASE_URL
printf 'PASS: all 8 offline cases\n'

CASE_SHELL_OPTION=-x
run_case new_success
if grep -Fq -e "${SYNTHETIC_WEBHOOK}" -e "${SYNTHETIC_VALUE}" "${CASE_DIR}/stdout" "${CASE_DIR}/stderr"; then
  printf 'FAIL: synthetic secret appeared in traced output\n' >&2
  exit 1
fi
unset CASE_SHELL_OPTION

for CASE_WEBHOOK_URL in \
  "http://${SYNTHETIC_WEBHOOK#https://}" \
  "https://${SYNTHETIC_USER}:${SYNTHETIC_PASSWORD}@hooks.example.test/mock" \
  "${SYNTHETIC_WEBHOOK}?credential=${SYNTHETIC_VALUE}" "${SYNTHETIC_WEBHOOK}#fragment" \
  "${SYNTHETIC_WEBHOOK} space" "${SYNTHETIC_WEBHOOK}"$'\t' "${SYNTHETIC_WEBHOOK}"$'\n' \
  "${SYNTHETIC_WEBHOOK}"$'\r' "${SYNTHETIC_WEBHOOK}"$'\001' \
  "${SYNTHETIC_WEBHOOK}\"" "${SYNTHETIC_WEBHOOK}\\" ""; do
  if run_case validation; then
    printf 'FAIL: unsafe webhook URL was accepted\n' >&2
    exit 1
  fi
  grep -Fq 'SLACK_WEBHOOK_URL' "${CASE_DIR}/stderr"
  test ! -s "${CASE_DIR}/requests.jsonl"
  if [[ -n "${CASE_WEBHOOK_URL}" && "$(cat "${CASE_DIR}/stdout" "${CASE_DIR}/stderr")" == *"${CASE_WEBHOOK_URL}"* ]]; then
    printf 'FAIL: unsafe webhook URL was logged\n' >&2
    exit 1
  fi
done
unset CASE_WEBHOOK_URL

for scenario in malformed_response tainted_response; do
  if run_case "${scenario}"; then
    printf 'FAIL: unsafe Jira response was accepted\n' >&2
    exit 1
  fi
  jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null
  if grep -Fq -e "${SYNTHETIC_WEBHOOK}" -e 'parse error' "${CASE_DIR}/stdout" "${CASE_DIR}/stderr"; then
    printf 'FAIL: Jira parser disclosed response details\n' >&2
    exit 1
  fi
done

if run_case slack_failure; then
  printf 'FAIL: Slack delivery failure was ignored\n' >&2
  exit 1
fi
grep -Fq 'Slack delivery failed: outcome unknown.' "${CASE_DIR}/stderr"
if grep -Fq "${SYNTHETIC_WEBHOOK}" "${CASE_DIR}/stdout" "${CASE_DIR}/stderr"; then
  printf 'FAIL: Slack response disclosed webhook URL\n' >&2
  exit 1
fi

CASE_WEBHOOK_URL=""
run_case dry_run --dry-run
jq -e -s 'all(.[]; .method == "GET")' "${CASE_DIR}/requests.jsonl" >/dev/null
run_case new_success --skip-slack
test ! -s "${CASE_DIR}/slack.jsonl"
unset CASE_WEBHOOK_URL

CASE_REASON="${SYNTHETIC_WEBHOOK}"
for mode in --force --dry-run --skip-slack; do
  if run_case validation "${mode}"; then
    printf 'FAIL: webhook URL was accepted in reason text\n' >&2
    exit 1
  fi
  test ! -s "${CASE_DIR}/requests.jsonl"
  grep -Fq 'Slack webhook URL must not appear' "${CASE_DIR}/stderr"
  if grep -Fq "${SYNTHETIC_WEBHOOK}" "${CASE_DIR}/stdout" "${CASE_DIR}/stderr"; then
    printf 'FAIL: reason text disclosed webhook URL\n' >&2
    exit 1
  fi
done
printf 'PASS: webhook security checks\n'
