#!/bin/bash
#
# Check test result from a run directory
#

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <run-dir>"
    echo ""
    echo "Example:"
    echo "  $0 runs/openshift-two-node-suite-20260724-195654/iter-01-test-01-*"
    exit 1
fi

RUN_DIR="$1"

echo "========================================"
echo "Checking: $RUN_DIR"
echo "========================================"
echo ""

if [[ ! -d "$RUN_DIR" ]]; then
    echo "ERROR: Directory not found: $RUN_DIR"
    exit 1
fi

echo "Directory contents:"
ls -lh "$RUN_DIR/"
echo ""

if [[ -d "$RUN_DIR/test" ]]; then
    echo "Test directory contents:"
    ls -lh "$RUN_DIR/test/"
    echo ""

    if [[ -f "$RUN_DIR/test/openshift-tests-timestamped.log" ]]; then
        echo "Last 50 lines of test log:"
        tail -50 "$RUN_DIR/test/openshift-tests-timestamped.log"
    fi
fi

if [[ -d "$RUN_DIR/test/junit" ]]; then
    echo ""
    echo "JUnit results:"
    for xml in "$RUN_DIR/test/junit"/*.xml; do
        if [[ -f "$xml" ]]; then
            echo ""
            echo "File: $(basename "$xml")"
            grep -E 'testsuites|testsuite|testcase|skipped|failure' "$xml" | head -20
        fi
    done
fi
