#!/usr/bin/env python3
"""Parse pcp2json output with filesys.{used,capacity}.

Tracks all partitions' usage over time as percentages and GB values.
Values are in KB from PCP, converted to GB.

Outputs JSON with timestamps array and per-partition used_pct/used_gb arrays.
"""

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FMT = "%Y-%m-%d %H:%M:%S"

KB_TO_GB = 1.0 / (1024 * 1024)


def get_instances(sample, *path):
    """Walk the nested dict to reach @instances list for a metric."""
    node = sample
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return []
        node = node[key]
    return node.get("@instances", [])


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
        description="Parse pcp2json filesys output into plot-ready JSON")
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

    # Discover all partitions from the first sample with capacity data
    devices = []
    for sample in samples:
        cap_insts = get_instances(sample, "filesys", "capacity")
        if cap_insts:
            devices = [inst["name"] for inst in cap_insts
                       if float(inst.get("value", 0)) > 0]
            break

    if not devices:
        print("ERROR: No partitions found", file=sys.stderr)
        sys.exit(1)

    # Sort by capacity descending (largest first)
    cap_lookup = {}
    mount_lookup = {}
    for sample in samples:
        cap_insts = get_instances(sample, "filesys", "capacity")
        for inst in cap_insts:
            cap_lookup[inst["name"]] = float(inst.get("value", 0))
        mount_insts = get_instances(sample, "filesys", "mountdir")
        for inst in mount_insts:
            mount_lookup[inst["name"]] = inst.get("value", "")
        if cap_lookup:
            break
    devices.sort(key=lambda d: cap_lookup.get(d, 0), reverse=True)

    # Build per-partition result: timestamps shared, used_pct per device
    result = {"timestamps": [], "partitions": []}
    for dev in devices:
        result["partitions"].append({
            "device": dev,
            "mountdir": mount_lookup.get(dev, ""),
            "capacity_gb": round(cap_lookup.get(dev, 0) * KB_TO_GB, 2),
            "used_pct": [],
            "used_gb": [],
        })

    for sample in samples:
        ts_str = sample.get("@timestamp", "")
        ts = convert_timestamp(ts_str, target_tz)
        if ts is None:
            continue

        used_insts = get_instances(sample, "filesys", "used")
        used_map = {inst["name"]: float(inst["value"]) for inst in used_insts}

        for part in result["partitions"]:
            used_kb = used_map.get(part["device"])
            cap_kb = cap_lookup.get(part["device"], 0)
            if used_kb is not None and cap_kb > 0:
                part["used_pct"].append(round(100.0 * used_kb / cap_kb, 1))
                part["used_gb"].append(round(used_kb * KB_TO_GB, 2))
            else:
                part["used_pct"].append(None)
                part["used_gb"].append(None)

        result["timestamps"].append(ts)

    if not result["timestamps"]:
        print("ERROR: No valid disk usage data points", file=sys.stderr)
        sys.exit(1)

    with open(args.output_json, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
