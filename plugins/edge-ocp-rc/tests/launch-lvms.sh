#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/scripts" "$test_dir/jobs/4.22" "$test_dir/jobs/5.0"
cp "$source_dir/scripts/launch.sh" "$test_dir/scripts/launch.sh"

printf '%s\n' 'periodic-ci-openshift-release-main-nightly-4.22-e2e-baremetalds-two-node-fencing' > "$test_dir/jobs/4.22/tnf.txt"
printf '%s\n' 'periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests' > "$test_dir/jobs/5.0/tnf-lvms.txt"

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
image_5_1='registry.example.test/ocp:5.1.0-rc.0-x86_64'

# A directory holding only the LVMS seed must not silently choose a release
# on a fresh clone.
mv "$test_dir/jobs/4.22/tnf.txt" "$test_dir/tnf.txt.saved"
rmdir "$test_dir/jobs/4.22"
run_failure refresh-only-seed 'cannot detect OCP release version' --refresh
mkdir -p "$test_dir/jobs/4.22"
mv "$test_dir/tnf.txt.saved" "$test_dir/jobs/4.22/tnf.txt"

# A version pins the release directory.
run_failure missing-release-dir 'no job files for topology' "$image_5_1" --job all

run_success pattern-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --job lvms
run_success text-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --job mno
run_success number-5-0 'tnf-lvms-mno-qe-integration-tests' "$image_5_0" --job 1
run_success all-4-22 '1 jobs launched' "$image_4_22" --job all
run_success broad-baremetalds '1 jobs launched' "$image_4_22" --job baremetalds

# A stale LVMS seed (copied forward before its lane exists) is reported as
# skipped rather than launched against the wrong payload.
mkdir -p "$test_dir/jobs/5.1"
printf '%s\n' 'periodic-ci-openshift-release-main-nightly-5.1-e2e-baremetalds-two-node-fencing' > "$test_dir/jobs/5.1/tnf.txt"
printf '%s\n' 'periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests' > "$test_dir/jobs/5.1/tnf-lvms.txt"
run_success stale-seed-skip 'Skipped 1 TNF LVMS lane(s) (targets 5.0, payload 5.1)' "$image_5_1" --job e2e
run_failure stale-seed-only 'the TNF LVMS lane targets 5.0, but you requested 5.1' "$image_5_1" --job lvms
rm -rf "$test_dir/jobs/5.1"

# Defense in depth: a job whose name targets another release is rejected even
# though it sits inside the requested release's directory.
printf '%s\n' 'periodic-ci-openshift-release-main-nightly-4.23-e2e-baremetalds-two-node-fencing' >> "$test_dir/jobs/5.0/tnf.txt"
run_failure number-wrong-release 'targets 4.23, but you requested 5.0' "$image_5_0" --job 1
run_failure text-wrong-release 'targets 4.23, but you requested 5.0' "$image_5_0" --job 4.23
run_failure all-wrong-release 'targets 4.23, but you requested 5.0' "$image_5_0" --job all
