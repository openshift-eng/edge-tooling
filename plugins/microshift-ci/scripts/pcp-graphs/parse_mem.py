#!/usr/bin/env python3
"""Parse pcp2json output with mem.util.{used,free,cached} and mem.physmem.

pcp2json returns these as kilobyte values. We convert to GB for readability.

Outputs a clean JSON with arrays: timestamps, used_gb, cached_gb, free_gb, total_gb.
"""

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FMT = "%Y-%m-%d %H:%M:%S"

KB_TO_GB = 1.0 / (1024 * 1024)


def get_value(sample, *path):
    """Walk the nested dict to reach a scalar metric value."""
    node = sample
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
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
        description="Parse pcp2json memory output into plot-ready JSON")
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

    result = {"timestamps": [], "used_gb": [], "cached_gb": [],
              "free_gb": [], "total_gb": []}

    for sample in samples:
        ts_str = sample.get("@timestamp", "")
        ts = convert_timestamp(ts_str, target_tz)
        if ts is None:
            continue
        used = get_value(sample, "mem", "util", "used")
        free = get_value(sample, "mem", "util", "free")
        cached = get_value(sample, "mem", "util", "cached")
        total = get_value(sample, "mem", "physmem")
        if used is None or free is None:
            continue
        if cached is None:
            cached = 0
        if total is None:
            total = used + free + cached

        result["timestamps"].append(ts)
        result["used_gb"].append(round(used * KB_TO_GB, 2))
        result["cached_gb"].append(round(cached * KB_TO_GB, 2))
        result["free_gb"].append(round(free * KB_TO_GB, 2))
        result["total_gb"].append(round(total * KB_TO_GB, 2))

    if not result["timestamps"]:
        print("ERROR: No valid memory data points", file=sys.stderr)
        sys.exit(1)

    with open(args.output_json, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
