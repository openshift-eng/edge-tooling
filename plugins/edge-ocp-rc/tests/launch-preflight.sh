#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/scripts" "$test_dir/jobs/5.0" "$test_dir/bin"
cp "$source_dir/scripts/launch.sh" "$test_dir/scripts/launch.sh"
printf '%s\n' 'periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests' > "$test_dir/jobs/5.0/tnf-lvms.txt"

cat > "$test_dir/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s' "$TEST_HTTP_CODE"
exit "$TEST_CURL_STATUS"
EOF
cat > "$test_dir/bin/gangway-cli" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' submitted >> "$TEST_SUBMISSIONS"
EOF
cat > "$test_dir/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$test_dir/bin/"*
export PATH="$test_dir/bin:$PATH"
export GANGWAY_BIN="$test_dir/bin/gangway-cli"
export MY_APPCI_TOKEN=preflight-test-token
export TEST_SUBMISSIONS="$test_dir/submissions"
export TEST_HTTP_CODE TEST_CURL_STATUS

check_preflight() {
    local label="$1" code="$2" curl_status="$3" expected_status="$4" expected_text="$5"
    local output status=0
    TEST_HTTP_CODE="$code"
    TEST_CURL_STATUS="$curl_status"
    : > "$TEST_SUBMISSIONS"
    output=$(/bin/bash "$test_dir/scripts/launch.sh" tnf registry.example.test/ocp:5.0.0-rc.5 \
        --job lvms --run "$label" 2>&1) || status=$?
    if [[ "$status" -ne "$expected_status" || "$output" != *"$expected_text"* ]]; then
        echo "$label: unexpected status $status or output: $output" >&2
        exit 1
    fi
    if [[ "$code" != 2* && "$output" == *"Token OK"* ]]; then
        echo "$label incorrectly confirmed token validation: $output" >&2
        exit 1
    fi
    if [[ "$expected_status" -eq 0 ]]; then
        if [[ ! -s "$TEST_SUBMISSIONS" || "$output" != *"1 jobs launched, 0 failures"* ]]; then
            echo "$label did not submit the job: $output" >&2
            exit 1
        fi
    elif [[ -s "$TEST_SUBMISSIONS" ]]; then
        echo "$label submitted a job after preflight failure" >&2
        exit 1
    fi
    echo "$label: OK"
}

check_preflight server-error 500 0 0 'token validation is inconclusive'
check_preflight not-found 404 0 0 'token validation is inconclusive'
check_preflight accepted 200 0 0 'Token OK (HTTP 200)'
check_preflight no-content 204 0 0 'Token OK (HTTP 204)'
check_preflight unauthorized 401 0 1 'token rejected by Gangway API (HTTP 401)'
check_preflight forbidden 403 0 1 'token rejected by Gangway API (HTTP 403)'
check_preflight unreachable 000 7 1 'cannot reach Gangway API'
check_preflight no-response 000 0 1 'cannot reach Gangway API'
