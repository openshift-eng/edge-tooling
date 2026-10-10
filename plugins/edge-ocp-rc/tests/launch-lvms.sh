#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/scripts" "$test_dir/jobs"
cp "$source_dir/scripts/launch.sh" "$test_dir/scripts/launch.sh"

printf '%s\n' 'periodic-ci-openshift-release-main-nightly-4.22-e2e-baremetalds-two-node-fencing' > "$test_dir/jobs/tnf.txt"
printf '%s\n' 'periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests' > "$test_dir/jobs/tnf-lvms.txt"

run_success() {
    local label="$1" expected="$2"
    shift 2
    local output
    if ! output=$("$test_dir/scripts/launch.sh" tnf "$@" --dry-run --run "$label" 2>&1); then
        echo "$label failed: $output" >&2
        exit 1
    fi
    if [[ "$output" != *"$expected"* ]]; then
        echo "$label omitted expected text: $output" >&2
        exit 1
    fi
    echo "$label: OK"
}

run_failure() {
    local label="$1" expected="$2"
    shift 2
    local output
    if output=$("$test_dir/scripts/launch.sh" tnf "$@" --dry-run --run "$label" 2>&1); then
        echo "$label unexpectedly succeeded: $output" >&2
        exit 1
    fi
    if [[ "$output" != *"$expected"* ]]; then
        echo "$label omitted expected error: $output" >&2
        exit 1
    fi
    echo "$label: OK"
}

image_5_0='registry.example.test/ocp:5.0.0-rc.5-x86_64'
image_4_22='registry.example.test/ocp:4.22.0-rc.0-x86_64'
unknown_image='registry.example.test/ocp@sha256:deadbeef'

run_success pattern-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --job lvms
run_success text-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --job mno
run_success number-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --job 2
run_success all-4-22 '1 jobs launched' "$image_4_22" --job all
run_failure number-wrong-release 'selected job #2 targets 5.0' "$image_4_22" --job 2
run_failure text-wrong-release 'targets 5.0' "$image_4_22" --job mno
run_failure all-mixed-release 'job files are for 4.22' "$image_5_0" --job all
run_failure pattern-unknown-release 'cannot determine the payload release' "$unknown_image" --job lvms
run_failure text-unknown-release 'cannot determine the payload release' "$unknown_image" --job mno
run_failure number-unknown-release 'cannot determine the payload release' "$unknown_image" --job 2

mkdir -p "$test_dir/bin" "$test_dir/runs/relaunch-source/tnf"
cat > "$test_dir/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' '{"result":"FAILURE"}'
EOF
chmod +x "$test_dir/bin/curl"
export PATH="$test_dir/bin:$PATH"
printf '[{"JobName":"%s","JobURL":"https://prow.ci.openshift.org/view/gs/example"}]\n' \
    "$(cat "$test_dir/jobs/tnf-lvms.txt")" > "$test_dir/runs/relaunch-source/tnf/gangway_failed.json"

run_failure relaunch-wrong-release 'selected job #2 targets 5.0' "$image_4_22" --relaunch-failed
run_success relaunch-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --relaunch-failed

# A future release should be discovered from Sippy without changing launch.sh.
cat > "$test_dir/bin/curl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
    *release=5.2*) printf '%s\n' '[]' ;;
    *tnf-lvms-mno-qe-integration-tests*)
        printf '%s\n' '[{"name":"periodic-ci-openshift-lvm-operator-release-5.1-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests","current_runs":0},{"name":"periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests","current_runs":1}]'
        ;;
    *) printf '%s\n' '[{"name":"periodic-ci-openshift-release-main-nightly-5.1-e2e-baremetalds-two-node-fencing","current_runs":1}]' ;;
esac
EOF
chmod +x "$test_dir/bin/curl"

image_5_1='registry.example.test/ocp:5.1.0-rc.0-x86_64'
image_5_2='registry.example.test/ocp:5.2.0-rc.0-x86_64'
run_success refresh-5-1 'Updated TNF LVMS jobs from Sippy for release 5.1' "$image_5_1" --refresh
run_success pattern-5-1 'tnf-lvms-mno-qe-integration-tests' "$image_5_1" --job lvms
run_success all-5-1 '2 jobs launched' "$image_5_1" --job all
run_failure stale-5-0 'targets 5.1, but you requested 5.0' "$image_5_0" --job lvms
run_success refresh-no-5-2 'keeping the tracked entry' "$image_5_2" --refresh
run_failure missing-5-2 'targets 5.1, but you requested 5.2' "$image_5_2" --job lvms
