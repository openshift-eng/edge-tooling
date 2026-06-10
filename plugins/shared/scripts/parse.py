"""Shared STRUCTURED SUMMARY parser for CI analysis scripts.

Provides parse_structured_summary() used by aggregate.py and search-bugs.py
so that both scripts parse the JSON format identically.
"""

import json
import re


def parse_structured_summary(filepath):
    """Extract the STRUCTURED SUMMARY JSON array from a per-job report file.

    Returns a list of dicts, one per failure entry. Returns [] if the file
    has no STRUCTURED SUMMARY block or the JSON is malformed.
    """
    with open(filepath, "r") as f:
        content = f.read()

    m = re.search(
        r"--- STRUCTURED SUMMARY ---\n(.+?)\n--- END STRUCTURED SUMMARY ---",
        content, re.DOTALL,
    )
    if not m:
        return []

    try:
        entries = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    if isinstance(entries, dict):
        entries = [entries]

    results = []
    for data in entries:
        try:
            severity = int(data.get("severity", 3))
        except (ValueError, TypeError):
            severity = 3

        results.append({
            "severity": severity,
            "stack_layer": data.get("stack_layer", ""),
            "step_name": data.get("step_name", ""),
            "error_signature": data.get("error_signature", ""),
            "raw_error": data.get("raw_error", ""),
            "root_cause": data.get("root_cause", ""),
            "infrastructure_failure": bool(data.get("infrastructure_failure", False)),
            "job_url": data.get("job_url", ""),
            "job_name": data.get("job_name", ""),
            "release": data.get("release", ""),
            "finished": data.get("finished", ""),
        })

    return results
