#!/usr/bin/env python3
"""Extract scenario metadata from junit.xml files in CI artifacts.

Discovers scenario-info/<scenario>/junit.xml files and parses test
counts, timing, and pass/fail status for each scenario.

Outputs scenarios.json grouped by build_id.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET


def find_scenario_dirs(artifacts_root):
    """Yield (build_id, scenario_name, scenario_dir) tuples."""
    if not os.path.isdir(artifacts_root):
        return
    for build_id in os.listdir(artifacts_root):
        build_dir = os.path.join(artifacts_root, build_id)
        if not os.path.isdir(build_dir):
            continue
        for root, dirs, _files in os.walk(build_dir, followlinks=True):
            if os.path.basename(root) != "scenario-info":
                continue
            for scenario in sorted(os.listdir(root)):
                scenario_dir = os.path.join(root, scenario)
                if os.path.isdir(scenario_dir):
                    yield build_id, scenario, scenario_dir
            dirs.clear()


MAX_XML_SIZE = 10 * 1024 * 1024  # 10 MB — reject unreasonably large JUnit files


def parse_junit(junit_path):
    """Parse a junit.xml and return test summary dict."""
    try:
        size = os.path.getsize(junit_path)
        if size > MAX_XML_SIZE:
            return None
        with open(junit_path, "rb") as f:
            content = f.read()
        root = ET.fromstring(content)
    except (ET.ParseError, FileNotFoundError, OSError):
        return None

    suite = root if root.tag == "testsuite" else root.find(".//testsuite")
    if suite is None:
        return None

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def _float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    tests = _int(suite.get("tests", "0"))
    failures = _int(suite.get("failures", "0"))
    errors = _int(suite.get("errors", "0"))
    skipped = _int(suite.get("skipped", "0"))
    time_sec = _float(suite.get("time", "0"))
    timestamp = suite.get("timestamp", "")

    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "duration_sec": round(time_sec, 1),
        "timestamp": timestamp,
    }


def find_vm_hosts(scenario_dir):
    """Return list of VM hostnames that have PCP data."""
    vms_dir = os.path.join(scenario_dir, "vms")
    if not os.path.isdir(vms_dir):
        return []
    hosts = []
    for host in sorted(os.listdir(vms_dir)):
        pcp_tar = os.path.join(vms_dir, host, "pcp", "pcp-archives.tar")
        if os.path.isfile(pcp_tar):
            hosts.append(host)
    return hosts


def main():
    """Parse arguments, scan artifacts for JUnit data, and write scenarios.json."""
    parser = argparse.ArgumentParser(
        description="Extract scenario metadata from junit.xml files")
    parser.add_argument("--workdir", required=True,
                        help="Work directory containing artifacts/")
    parser.add_argument("--output",
                        help="Output JSON file (default: <workdir>/pcp-dashboard/scenarios.json)")
    args = parser.parse_args()

    artifacts_root = os.path.join(args.workdir, "artifacts")
    output = args.output or os.path.join(
        args.workdir, "pcp-dashboard", "scenarios.json")

    output_dir = os.path.dirname(output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    result = {}

    for build_id, scenario, scenario_dir in find_scenario_dirs(artifacts_root):
        junit_path = os.path.join(scenario_dir, "junit.xml")
        junit_data = parse_junit(junit_path)

        vm_hosts = find_vm_hosts(scenario_dir)

        entry = {
            "name": scenario,
            "vm_hosts": vm_hosts,
        }

        if junit_data:
            status = "pass"
            if junit_data["failures"] > 0 or junit_data["errors"] > 0:
                status = "fail"
            entry["status"] = status
            entry["tests"] = junit_data["tests"]
            entry["failures"] = junit_data["failures"]
            entry["errors"] = junit_data["errors"]
            entry["skipped"] = junit_data["skipped"]
            entry["duration_sec"] = junit_data["duration_sec"]
            entry["timestamp"] = junit_data["timestamp"]
        else:
            entry["status"] = "unknown"

        result.setdefault(build_id, {})[scenario] = entry

    with open(output, "w") as f:
        json.dump(result, f, indent=2)

    total = sum(len(v) for v in result.values())
    print(f"Extracted metadata for {total} scenarios across "
          f"{len(result)} builds -> {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
