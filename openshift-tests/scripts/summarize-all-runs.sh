#!/bin/bash
#
# Summarize all test runs
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${SCRATCH_ROOT}/runs"

echo "========================================"
echo "Test Run Summary"
echo "========================================"
echo ""

TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_TESTS=0

for run in $(find "${RUN_DIR}" -maxdepth 1 -type d -name "openshift-two-node-suite-*" 2>/dev/null | sort); do
    run="${run%/}"
    run_name=$(basename "$run")

    if [[ -f "${run}/summary.tsv" ]]; then
        # Count pass/fail from summary
        tests=$(tail -n +2 "${run}/summary.tsv" | wc -l)
        if [[ $tests -eq 0 ]]; then
            continue
        fi

        pass=$(tail -n +2 "${run}/summary.tsv" | awk -F'\t' '$3 == "PASS" || $3 ~ /^PASS\(/' | wc -l)
        fail=$(tail -n +2 "${run}/summary.tsv" | awk -F'\t' '$3 ~ /^FAIL\(/' | wc -l)

        TOTAL_TESTS=$((TOTAL_TESTS + tests))
        TOTAL_PASS=$((TOTAL_PASS + pass))
        TOTAL_FAIL=$((TOTAL_FAIL + fail))

        echo "$run_name:"
        echo "  Tests: $tests, Pass: $pass, Fail: $fail"

        # Show failed tests
        if [[ $fail -gt 0 ]]; then
            echo "  Failed:"
            tail -n +2 "${run}/summary.tsv" | awk -F'\t' '$3 ~ /^FAIL\(/ {
                # Extract just the test name part
                test_name = $5
                gsub(/^\[sig-[^\]]+\]\[apigroup:[^\]]+\]/, "", test_name)
                gsub(/^\[OCPFeatureGate:[^\]]+\]/, "", test_name)
                gsub(/^\[Suite:[^\]]+\]/, "", test_name)
                gsub(/^ +/, "", test_name)
                printf "    - %s (%s)\n", test_name, $3
            }'
        fi
        echo ""
    fi
done

echo "========================================"
echo "Overall Summary"
echo "========================================"
echo "Total test executions: $TOTAL_TESTS"
echo "Passed: $TOTAL_PASS"
echo "Failed: $TOTAL_FAIL"

if [[ $TOTAL_TESTS -gt 0 ]]; then
    pass_rate=$((TOTAL_PASS * 100 / TOTAL_TESTS))
    echo "Pass rate: ${pass_rate}%"
fi
echo ""
