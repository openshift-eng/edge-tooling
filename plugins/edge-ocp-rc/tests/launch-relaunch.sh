#!/usr/bin/env bash
set -euo pipefail

# --relaunch-failed selects the latest run for the topology regardless of
# release. A run whose jobs target a different release than the requested
# payload must be rejected explicitly, not silently reported as failure-free.

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT
mkdir -p "$test_dir/scripts" "$test_dir/bin" "$test_dir/jobs/4.22" "$test_dir/runs/2026-11-16/tnf"
cp "$source_dir/scripts/launch.sh" "$test_dir/scripts/launch.sh"

job_4_22='periodic-ci-openshift-release-main-nightly-4.22-e2e-baremetalds-two-node-fencing'
job_5_0_lvms='periodic-ci-openshift-lvm-operator-release-5.0-nightly-e2e-baremetalds-tnf-lvms-mno-qe-integration-tests'
printf '%s\n' "$job_4_22" > "$test_dir/jobs/4.22/tnf.txt"

image_4_22='registry.example.test/ocp:4.22.0-rc.0-x86_64'

gangway_json() {
    local job="$1" url="$2"
    printf '[{"JobName": "%s", "JobURL": "%s"}]' "$job" "$url"
}

# A previous run holding only 5.0 jobs while a 4.22 payload is requested must
# be rejected before the name-to-number lookup silently drops its failures.
gangway_json "$job_5_0_lvms" 'https://prow.ci.openshift.org/view/gs/test-logs/run' \
    > "$test_dir/runs/2026-11-16/tnf/gangway_5.0.json"

output=$("$test_dir/scripts/launch.sh" tnf "$image_4_22" --relaunch-failed --dry-run 2>&1) \
    && { echo "cross-release run was not rejected: $output" >&2; exit 1; }
if [[ "$output" != *"holds no 4.22 jobs for 'tnf'"* ]]; then
    echo "cross-release rejection missing expected text: $output" >&2
    exit 1
fi
echo "cross-release-rejected: OK"

# A run directory is shared by every release launched for the topology on the
# same day. Relaunching one release must map only that release's records.
gangway_json "$job_4_22" 'https://prow.ci.openshift.org/view/gs/test-logs/run-4-22' \
    > "$test_dir/runs/2026-11-16/tnf/gangway_4.22.json"

cat > "$test_dir/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '{"result": "FAILURE"}'
EOF
chmod +x "$test_dir/bin/curl"
export PATH="$test_dir/bin:$PATH"

output=$("$test_dir/scripts/launch.sh" tnf "$image_4_22" --relaunch-failed --dry-run --run mixed 2>&1)
for expected in 'Re-launching failed jobs: 1' '1 jobs launched, 0 failures'; do
    if [[ "$output" != *"$expected"* ]]; then
        echo "mixed-run relaunch omitted expected text '$expected': $output" >&2
        exit 1
    fi
done
echo "mixed-run-scoped: OK"
rm "$test_dir/runs/2026-11-16/tnf/gangway_5.0.json"

# A matching single-release run relaunches its failure as before.
output=$("$test_dir/scripts/launch.sh" tnf "$image_4_22" --relaunch-failed --dry-run --run relaunch 2>&1)
for expected in 'Re-launching failed jobs: 1' '1 jobs launched, 0 failures'; do
    if [[ "$output" != *"$expected"* ]]; then
        echo "relaunch omitted expected text '$expected': $output" >&2
        exit 1
    fi
done
echo "matching-run-relaunched: OK"

# A registry port in the image reference must not be mistaken for the tag
# separator when deriving the requested release: the release scoping and the
# cross-release rejection must behave exactly as with a portless registry.
port_image='registry.example.test:5000/ocp:4.22.0-rc.0-x86_64'

# --relaunch-failed scans the latest run directory; the scenario above created
# runs/relaunch/tnf, so make the 4.22 record's run the latest again.
touch "$test_dir/runs/2026-11-16/tnf"
output=$("$test_dir/scripts/launch.sh" tnf "$port_image" --relaunch-failed --dry-run --run ported 2>&1)
for expected in 'Re-launching failed jobs: 1' '1 jobs launched, 0 failures'; do
    if [[ "$output" != *"$expected"* ]]; then
        echo "ported relaunch omitted expected text '$expected': $output" >&2
        exit 1
    fi
done
echo "ported-image-relaunched: OK"

# Then make a 5.0-only run the latest; the ported image must still be scoped
# to 4.22 and reject it.
mkdir -p "$test_dir/runs/port/tnf"
gangway_json "$job_5_0_lvms" 'https://prow.ci.openshift.org/view/gs/test-logs/run' \
    > "$test_dir/runs/port/tnf/gangway_5.0.json"

output=$("$test_dir/scripts/launch.sh" tnf "$port_image" --relaunch-failed --dry-run --run port 2>&1) \
    && { echo "ported cross-release run was not rejected: $output" >&2; exit 1; }
if [[ "$output" != *"holds no 4.22 jobs for 'tnf'"* ]]; then
    echo "ported cross-release rejection missing expected text: $output" >&2
    exit 1
fi
echo "ported-image-release-scoped: OK"
