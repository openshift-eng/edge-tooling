#!/usr/bin/env bash
set -euo pipefail

# Direct tests for job number resolution in status.sh's job_num_lookup with
# the jobs/<release>/ layout. Numbers must match launch.sh --list numbering:
# per release directory, regular jobs first, then LVMS, then z-stream, then
# y-stream.

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/scripts" "$test_dir/jobs/4.22" "$test_dir/jobs/5.0" \
    "$test_dir/bin" "$test_dir/runs/demo/tnf"
cp "$source_dir/scripts/status.sh" "$test_dir/scripts/status.sh"

job_422_a='periodic-ci-openshift-release-main-nightly-4.22-e2e-agent-ovn-two-node-fencing'
job_422_b='periodic-ci-openshift-release-main-nightly-4.22-e2e-metal-ovn-two-node-fencing'
job_422_z='periodic-ci-openshift-release-main-nightly-4.22-e2e-metal-ovn-two-node-fencing-upgrade'
job_5_0_lvms='periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests'
job_unknown='periodic-ci-openshift-release-main-nightly-6.2-e2e-metal-ovn-two-node-fencing'
# Neither a nightly-<x.y> nor a release-<x.y> marker: the parser's negative case.
job_unmarked_a='presubmit-ci-openshift-custom-two-node-fencing-lane'
job_unmarked_b='presubmit-ci-openshift-custom-two-node-fencing-extra'

printf '%s\n%s\n' "$job_422_a" "$job_422_b" > "$test_dir/jobs/4.22/tnf.txt"
printf '%s\n' "$job_422_z" > "$test_dir/jobs/4.22/tnf-z-stream.txt"
printf '%s\n' "$job_5_0_lvms" > "$test_dir/jobs/5.0/tnf-lvms.txt"
# Jobs without a release marker are numbered from the flat jobs root.
printf '%s\n%s\n' "$job_unmarked_a" "$job_unmarked_b" > "$test_dir/jobs/tnf.txt"

# The LVMS job name carries its release before "nightly-" and a later version
# segment; the parser must pick 5.0, not the later text.
job_5_0_versioned='periodic-ci-openshift-lvm-operator-release-5.0-nightly-4.19-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests'
printf '%s\n' "$job_5_0_versioned" > "$test_dir/jobs/5.0/tnf-lvms.txt"

make_run() {
    local file="$1" job="$2"
    printf '[{"JobName":"%s","JobURL":"https://prow.ci.openshift.org/view/gs/example"}]\n' "$job" \
        > "$test_dir/runs/demo/tnf/gangway_${file}.json"
}
make_run a "$job_422_a"
make_run b "$job_422_b"
make_run z "$job_422_z"
make_run lvms "$job_5_0_versioned"
make_run unknown "$job_unknown"
make_run unmarked "$job_unmarked_b"

cat > "$test_dir/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
    *'/finished.json') printf '%s\n' '{"result":"FAILURE"}' ;;
    *) exit 22 ;;
esac
EOF
chmod +x "$test_dir/bin/curl"
export PATH="$test_dir/bin:$PATH"

# status.sh exits non-zero when jobs failed; the JSON is what matters here.
output=$("$test_dir/scripts/status.sh" tnf --run demo --json) || true

number_for() {
    local job="$1"
    printf '%s\n' "$output" | jq -r --arg job "$job" \
        '.topologies.tnf.jobs[] | select(.job == $job) | .number'
}

check() {
    local label="$1" job="$2" expected="$3" actual
    actual=$(number_for "$job")
    if [[ "$actual" != "$expected" ]]; then
        echo "$label: expected number $expected, got '$actual'" >&2
        exit 1
    fi
    echo "$label: OK"
}

check regular-first  "$job_422_a"  1
check regular-second "$job_422_b"  2
check z-stream       "$job_422_z"  3
check lvms-release   "$job_5_0_versioned" 1
# Neither marker: numbered from the flat jobs root, position within that list.
check unmarked-root  "$job_unmarked_b" 2
# A marked job whose release has no directory is unknown.
check unknown        "$job_unknown" 0

# An upgraded checkout that has not run launch.sh yet still holds its legacy
# flat lists in jobs/. Known numbers must survive until migration runs.
flat_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir" "$flat_dir"' EXIT
mkdir -p "$flat_dir/scripts" "$flat_dir/jobs" "$flat_dir/runs/demo/tnf"
cp "$source_dir/scripts/status.sh" "$flat_dir/scripts/status.sh"
printf '%s\n%s\n' "$job_422_a" "$job_422_b" > "$flat_dir/jobs/tnf.txt"
printf '%s\n' "$job_422_z" > "$flat_dir/jobs/tnf-z-stream.txt"
make_flat_run() {
    local file="$1" job="$2"
    printf '[{"JobName":"%s","JobURL":"https://prow.ci.openshift.org/view/gs/example"}]\n' "$job" \
        > "$flat_dir/runs/demo/tnf/gangway_${file}.json"
}
make_flat_run a "$job_422_a"
make_flat_run b "$job_422_b"
make_flat_run z "$job_422_z"

output=$("$flat_dir/scripts/status.sh" tnf --run demo --json) || true
flat_number_for() {
    local job="$1"
    printf '%s\n' "$output" | jq -r --arg job "$job" \
        '.topologies.tnf.jobs[] | select(.job == $job) | .number'
}
for spec in "flat-first:$job_422_a:1" "flat-second:$job_422_b:2" "flat-z:$job_422_z:3"; do
    label=${spec%%:*}; rest=${spec#*:}; job=${rest%%:*}; expected=${rest##*:}
    actual=$(flat_number_for "$job")
    if [[ "$actual" != "$expected" ]]; then
        echo "$label: expected number $expected, got '$actual'" >&2
        exit 1
    fi
    echo "$label: OK"
done

# Coexistence: a checkout upgraded to the release-directory layout ships the
# committed jobs/5.0 LVMS seed while legacy flat lists still hold the regular
# 5.0 jobs. The seed must not cut the regular jobs off from their numbers:
# each list resolves per-file, release-dir file winning over the flat file of
# the same name (launch.sh's migration conflict policy). Numbering follows the
# usual order — regular (from the flat list) first, then LVMS (from the seed).
coexist_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir" "$flat_dir" "$coexist_dir"' EXIT
mkdir -p "$coexist_dir/scripts" "$coexist_dir/jobs/5.0" "$coexist_dir/runs/demo/tnf"
cp "$source_dir/scripts/status.sh" "$coexist_dir/scripts/status.sh"
job_5_0_a='periodic-ci-openshift-release-main-nightly-5.0-e2e-metal-ovn-two-node-fencing'
job_5_0_b='periodic-ci-openshift-release-main-nightly-5.0-e2e-agent-ovn-two-node-fencing'
printf '%s\n%s\n' "$job_5_0_a" "$job_5_0_b" > "$coexist_dir/jobs/tnf.txt"
printf '%s\n' "$job_5_0_lvms" > "$coexist_dir/jobs/5.0/tnf-lvms.txt"
make_coexist_run() {
    local file="$1" job="$2"
    printf '[{"JobName":"%s","JobURL":"https://prow.ci.openshift.org/view/gs/example"}]\n' "$job" \
        > "$coexist_dir/runs/demo/tnf/gangway_${file}.json"
}
make_coexist_run a "$job_5_0_a"
make_coexist_run b "$job_5_0_b"
make_coexist_run lvms "$job_5_0_lvms"

output=$("$coexist_dir/scripts/status.sh" tnf --run demo --json) || true
coexist_number_for() {
    local job="$1"
    printf '%s\n' "$output" | jq -r --arg job "$job" \
        '.topologies.tnf.jobs[] | select(.job == $job) | .number'
}
for spec in "coexist-regular-first:$job_5_0_a:1" \
            "coexist-regular-second:$job_5_0_b:2" \
            "coexist-lvms-seed:$job_5_0_lvms:3"; do
    label=${spec%%:*}; rest=${spec#*:}; job=${rest%%:*}; expected=${rest##*:}
    actual=$(coexist_number_for "$job")
    if [[ "$actual" != "$expected" ]]; then
        echo "$label: expected number $expected, got '$actual'" >&2
        exit 1
    fi
    echo "$label: OK"
done

# Conflict policy: when the release directory also holds tnf.txt, it wins and
# the flat tnf.txt is ignored entirely, exactly like migrate_flat_job_layout.
printf '%s\n' "$job_5_0_b" > "$coexist_dir/jobs/5.0/tnf.txt"
output=$("$coexist_dir/scripts/status.sh" tnf --run demo --json) || true
for spec in "coexist-release-wins:$job_5_0_b:1" \
            "coexist-lvms-after-release:$job_5_0_lvms:2" \
            "coexist-flat-ignored:$job_5_0_a:0"; do
    label=${spec%%:*}; rest=${spec#*:}; job=${rest%%:*}; expected=${rest##*:}
    actual=$(coexist_number_for "$job")
    if [[ "$actual" != "$expected" ]]; then
        echo "$label: expected number $expected, got '$actual'" >&2
        exit 1
    fi
    echo "$label: OK"
done

# Mixed flat list migration: a flat jobs/tnf.txt holding a marked 4.22 job
# followed by an unmarked job migrates wholesale into jobs/4.22/. The unmarked
# job's number is then its position in the 4.22 lists — the same number
# launch.sh --list assigns — not 0.
mixed_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir" "$flat_dir" "$coexist_dir" "$mixed_dir"' EXIT
mkdir -p "$mixed_dir/scripts" "$mixed_dir/jobs/4.22" "$mixed_dir/runs/demo/tnf"
cp "$source_dir/scripts/status.sh" "$mixed_dir/scripts/status.sh"
printf '%s\n%s\n' "$job_422_a" "$job_unmarked_a" > "$mixed_dir/jobs/4.22/tnf.txt"
make_mixed_run() {
    local file="$1" job="$2"
    printf '[{"JobName":"%s","JobURL":"https://prow.ci.openshift.org/view/gs/example"}]\n' "$job" \
        > "$mixed_dir/runs/demo/tnf/gangway_${file}.json"
}
make_mixed_run a "$job_422_a"
make_mixed_run unmarked "$job_unmarked_a"

output=$("$mixed_dir/scripts/status.sh" tnf --run demo --json) || true
mixed_number_for() {
    local job="$1"
    printf '%s\n' "$output" | jq -r --arg job "$job" \
        '.topologies.tnf.jobs[] | select(.job == $job) | .number'
}
for spec in "mixed-marked:$job_422_a:1" "mixed-unmarked-migrated:$job_unmarked_a:2"; do
    label=${spec%%:*}; rest=${spec#*:}; job=${rest%%:*}; expected=${rest##*:}
    actual=$(mixed_number_for "$job")
    if [[ "$actual" != "$expected" ]]; then
        echo "$label: expected number $expected, got '$actual'" >&2
        exit 1
    fi
    echo "$label: OK"
done

# An unmarked job present in several release directories has no defensible
# number; it must resolve to 0 rather than pick an owner arbitrarily.
mkdir -p "$mixed_dir/jobs/5.0"
printf '%s\n' "$job_unmarked_a" > "$mixed_dir/jobs/5.0/tnf.txt"
output=$("$mixed_dir/scripts/status.sh" tnf --run demo --json) || true
actual=$(mixed_number_for "$job_unmarked_a")
if [[ "$actual" != "0" ]]; then
    echo "mixed-unmarked-ambiguous: expected number 0, got '$actual'" >&2
    exit 1
fi
echo "mixed-unmarked-ambiguous: OK"

# Partial migration: the unmarked target sits in the release directory's LVMS
# seed while the marked job still lives in the flat list. Its number must
# count the eligible flat list alongside the release directory's lists — the
# same number a fully migrated checkout assigns.
partial_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir" "$flat_dir" "$coexist_dir" "$mixed_dir" "$partial_dir"' EXIT
mkdir -p "$partial_dir/scripts" "$partial_dir/jobs/4.22" "$partial_dir/runs/demo/tnf"
cp "$source_dir/scripts/status.sh" "$partial_dir/scripts/status.sh"
printf '%s\n' "$job_422_a" > "$partial_dir/jobs/tnf.txt"
printf '%s\n' "$job_unmarked_a" > "$partial_dir/jobs/4.22/tnf-lvms.txt"
printf '[{"JobName":"%s","JobURL":"https://prow.ci.openshift.org/view/gs/example"}]\n' "$job_unmarked_a" \
    > "$partial_dir/runs/demo/tnf/gangway_unmarked.json"

output=$("$partial_dir/scripts/status.sh" tnf --run demo --json) || true
actual=$(printf '%s\n' "$output" | jq -r --arg job "$job_unmarked_a" \
    '.topologies.tnf.jobs[] | select(.job == $job) | .number')
if [[ "$actual" != "2" ]]; then
    echo "partial-migration-unmarked: expected number 2, got '$actual'" >&2
    exit 1
fi
echo "partial-migration-unmarked: OK"
