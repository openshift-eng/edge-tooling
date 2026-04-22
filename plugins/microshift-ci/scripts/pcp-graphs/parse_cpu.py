#!/usr/bin/env python3
"""Parse pcp2json output with kernel.all.cpu.{user,sys,idle,wait.total}.

pcp2json returns rate-converted values (ms/s). We normalize each sample
to percentages by dividing each metric by the total (user+sys+iowait+idle).

Outputs a clean JSON with arrays: timestamps, user, sys, iowait, idle (all in %).
"""

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FMT = "%Y-%m-%d %H:%M:%S"


def get_value(sample, *path):
    """Walk the nested dict to reach a scalar metric value."""
    node = sample
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    # kernel.all.cpu metrics are scalars, not per-instance
    if isinstance(node, dict) and "@instances" in node:
        instances = node["@instances"]
        if instances:
            try:
                return float(instances[0]["value"])
            except (KeyError, ValueError, TypeError):
                return None
    if isinstance(node, dict) and "value" in node:
        try:
            return float(node["value"])
        except (ValueError, TypeError):
            return None
    try:
        return float(node)
    except (ValueError, TypeError):
        return None


def convert_timestamp(ts_str, target_tz):
    """Parse a pcp2json timestamp and convert to the target timezone."""
    utc = ZoneInfo("UTC")
    for fmt in (FMT, "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z"):
        try:
            ts = datetime.strptime(ts_str, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=utc)
    return ts.astimezone(target_tz).strftime(FMT)


def main():
    parser = argparse.ArgumentParser(
        description="Parse pcp2json CPU output into plot-ready JSON")
    parser.add_argument("input_json", help="Raw pcp2json output file")
    parser.add_argument("output_json", help="Output JSON file")
    parser.add_argument("--timezone", default="UTC",
                        help="Target timezone for timestamps (default: UTC)")
    args = parser.parse_args()

    try:
        target_tz = ZoneInfo(args.timezone)
    except (KeyError, ZoneInfoNotFoundError):
        print(f"ERROR: Unknown timezone '{args.timezone}'", file=sys.stderr)
        sys.exit(1)

    with open(args.input_json) as f:
        raw = f.read()

    # Strip pcp2json comment lines
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith('{ "//":') and stripped.endswith("}"):
            continue
        lines.append(line)
    raw = "\n".join(lines)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse pcp2json output: {e}", file=sys.stderr)
        sys.exit(1)

    hosts = data.get("@pcp", {}).get("@hosts", [])
    if not hosts:
        print("ERROR: No hosts found in pcp2json output", file=sys.stderr)
        sys.exit(1)

    samples = hosts[0].get("@metrics", [])
    if not samples:
        print("ERROR: No metric samples found", file=sys.stderr)
        sys.exit(1)

    # pcp2json returns rate-converted values (ms/s per CPU).
    # Normalize to percentages by dividing each metric by the total.
    result = {"timestamps": [], "user": [], "sys": [], "iowait": [], "idle": []}

    for sample in samples:
        ts_str = sample.get("@timestamp", "")
        ts = convert_timestamp(ts_str, target_tz)
        if ts is None:
            continue
        user = get_value(sample, "kernel", "all", "cpu", "user")
        sys_val = get_value(sample, "kernel", "all", "cpu", "sys")
        idle = get_value(sample, "kernel", "all", "cpu", "idle")
        iowait = get_value(sample, "kernel", "all", "cpu", "wait", "total")
        if user is None or sys_val is None or idle is None:
            continue
        if iowait is None:
            iowait = 0
        total = user + sys_val + iowait + idle
        if total <= 0:
            continue
        result["timestamps"].append(ts)
        result["user"].append(round(100.0 * user / total, 1))
        result["sys"].append(round(100.0 * sys_val / total, 1))
        result["iowait"].append(round(100.0 * iowait / total, 1))
        result["idle"].append(round(100.0 * idle / total, 1))

    if not result["timestamps"]:
        print("ERROR: No valid CPU data points", file=sys.stderr)
        sys.exit(1)

    with open(args.output_json, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
