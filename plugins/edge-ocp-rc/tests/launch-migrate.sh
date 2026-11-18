#!/usr/bin/env bash
set -euo pipefail

# One-time migration from the flat jobs/*.txt layout to jobs/<release>/.
# A checkout from before the release-directory layout must keep working
# without any manual step — including a checkout that already carries the
# committed jobs/5.0 LVMS seed, which is what updating to this layout yields.

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/scripts" "$test_dir/jobs"
cp "$source_dir/scripts/launch.sh" "$test_dir/scripts/launch.sh"

job_4_22='periodic-ci-openshift-release-main-nightly-4.22-e2e-baremetalds-two-node-fencing'
job_5_0_lvms='periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests'

image_4_22='registry.example.test/ocp:4.22.0-rc.0-x86_64'

# The actual upgrade layout: this branch commits jobs/5.0/tnf-lvms.txt, so a
# checkout updated from the flat layout has the seed directory plus legacy
# flat files. The seed must not stop the flat files from being migrated.
mkdir -p "$test_dir/jobs/5.0"
printf '%s\n' "$job_5_0_lvms" > "$test_dir/jobs/5.0/tnf-lvms.txt"
printf '%s\n' "$job_4_22" > "$test_dir/jobs/tnf.txt"

output=$("$test_dir/scripts/launch.sh" tnf --list 2>&1)
for expected in 'Migrating flat job files' 'tnf.txt -> jobs/4.22/'; do
    if [[ "$output" != *"$expected"* ]]; then
        echo "migration omitted expected text '$expected': $output" >&2
        exit 1
    fi
done
for f in jobs/4.22/tnf.txt jobs/5.0/tnf-lvms.txt; do
    if [[ ! -f "$test_dir/$f" ]]; then
        echo "migration did not produce $f" >&2
        exit 1
    fi
done
if [[ -e "$test_dir/jobs/tnf.txt" ]]; then
    echo "migration left flat files behind" >&2
    exit 1
fi
echo "flat-migration-alongside-seed: OK"

# After migration the split releases list and launch independently.
if ! output=$("$test_dir/scripts/launch.sh" tnf --list 2>&1) \
    || [[ "$output" != *"release 4.22"* || "$output" != *"release 5.0"* ]]; then
    echo "post-migration list missing releases: $output" >&2
    exit 1
fi
echo "post-migration-list: OK"

output=$("$test_dir/scripts/launch.sh" tnf "$image_4_22" --job all --dry-run --run migrate 2>&1)
if [[ "$output" != *"1 jobs launched"* ]]; then
    echo "post-migration launch failed: $output" >&2
    exit 1
fi
echo "post-migration-launch: OK"

# A flat file whose destination already exists never overwrites it: the
# release-directory file is preserved and the flat file is left in place.
printf '%s\n' "$job_5_0_lvms" > "$test_dir/jobs/tnf-lvms.txt"
output=$("$test_dir/scripts/launch.sh" tnf "$image_4_22" --job all --dry-run --run coexist 2>&1)
if [[ "$output" != *"jobs/5.0/tnf-lvms.txt already exists"* ]]; then
    echo "destination conflict was not reported: $output" >&2
    exit 1
fi
if [[ "$output" != *"1 jobs launched"* || "$output" == *"$job_5_0_lvms"* ]]; then
    echo "conflicting flat file leaked into the launch: $output" >&2
    exit 1
fi
if ! cmp -s <(printf '%s\n' "$job_5_0_lvms") "$test_dir/jobs/5.0/tnf-lvms.txt"; then
    echo "destination file was modified by a conflicting flat file" >&2
    exit 1
fi
echo "destination-preserved: OK"
rm "$test_dir/jobs/tnf-lvms.txt"

# A flat file with no recognizable release is left in place with a warning.
fresh_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir" "$fresh_dir"' EXIT
mkdir -p "$fresh_dir/scripts" "$fresh_dir/jobs"
cp "$source_dir/scripts/launch.sh" "$fresh_dir/scripts/launch.sh"
printf '%s\n' 'some-manual-job-list-entry' > "$fresh_dir/jobs/tnf.txt"
output=$("$fresh_dir/scripts/launch.sh" tnf --list 2>&1)
if [[ "$output" != *"cannot infer a release for tnf.txt"* ]]; then
    echo "uninferable flat file was not reported: $output" >&2
    exit 1
fi
if [[ ! -f "$fresh_dir/jobs/tnf.txt" ]]; then
    echo "uninferable flat file was moved anyway" >&2
    exit 1
fi
echo "uninferable-left-in-place: OK"
