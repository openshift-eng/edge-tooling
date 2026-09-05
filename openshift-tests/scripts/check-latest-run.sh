#!/bin/bash
#
# Check status of latest test run
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${SCRATCH_ROOT}/runs"

# Find latest run directory by directory name (sorted by timestamp in name)
LATEST_RUN=$(find "${RUN_DIR}" -maxdepth 1 -type d -name "openshift-two-node-suite-*" 2>/dev/null | sort | tail -1)

if [[ -z "$LATEST_RUN" ]]; then
    echo "ERROR: No runs found in ${RUN_DIR}"
    exit 1
fi

LATEST_RUN="${LATEST_RUN%/}"  # Remove trailing slash

echo "========================================"
echo "Latest Run: $(basename "$LATEST_RUN")"
echo "========================================"
echo ""

# Check if summary.tsv exists
if [[ -f "${LATEST_RUN}/summary.tsv" ]]; then
    echo "Summary:"
    echo ""
    cat "${LATEST_RUN}/summary.tsv"
    echo ""
    echo "========================================"
    echo "Test Results:"
    echo "========================================"

    # Count pass/fail/skip (column 3 is result)
    TOTAL=$(tail -n +2 "${LATEST_RUN}/summary.tsv" | wc -l)
    PASS=$(tail -n +2 "${LATEST_RUN}/summary.tsv" | awk -F'\t' '$3 == "PASS" || $3 ~ /^PASS\(/' | wc -l)
    FAIL=$(tail -n +2 "${LATEST_RUN}/summary.tsv" | awk -F'\t' '$3 ~ /^FAIL\(/' | wc -l)

    echo ""
    echo "Total tests run: $TOTAL"
    echo "Passed: $PASS"
    echo "Failed: $FAIL"
    echo ""

    if [[ $FAIL -gt 0 ]]; then
        echo "Failed tests:"
        tail -n +2 "${LATEST_RUN}/summary.tsv" | awk -F'\t' '$3 ~ /^FAIL\(/ {print "  - " $5 " (" $3 ")"}'
        echo ""
    fi
else
    echo "No summary.tsv found - checking individual test directories..."
    echo ""

    PASS=0
    FAIL=0

    for test_dir in "${LATEST_RUN}"/iter-*-test-*/; do
        if [[ -d "$test_dir" ]]; then
            test_name=$(basename "$test_dir")

            # Check for junit results
            if [[ -d "${test_dir}/test/junit" ]]; then
                junit_files=$(find "${test_dir}/test/junit" -name "*.xml" 2>/dev/null)
                if [[ -n "$junit_files" ]]; then
                    # Check for failures in junit
                    failures=$(grep -h 'failures="' $junit_files 2>/dev/null | grep -v 'failures="0"' | wc -l)
                    if [[ $failures -gt 0 ]]; then
                        echo "  FAIL: $test_name"
                        FAIL=$((FAIL + 1))
                    else
                        echo "  PASS: $test_name"
                        PASS=$((PASS + 1))
                    fi
                else
                    echo "  UNKNOWN: $test_name (no junit results)"
                fi
            else
                echo "  UNKNOWN: $test_name (no junit directory)"
            fi
        fi
    done

    echo ""
    echo "Passed: $PASS"
    echo "Failed: $FAIL"
fi

echo ""
echo "Run directory: $LATEST_RUN"
