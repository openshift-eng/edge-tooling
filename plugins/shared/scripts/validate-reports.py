#!/usr/bin/env python3
"""Validate causal-chain citations in per-job analysis reports.

Checks that every causal-chain link in the STRUCTURED SUMMARY has:
  - An 'evidence' field pointing to a real file (artifact or source path)
  - A 'quote' field with non-empty text

Prints validation errors per file.  Exits 0 if all files pass, 1 if any fail.

Usage:
    validate-reports.py <report1.txt> [<report2.txt> ...]
    validate-reports.py <workdir>/jobs/release-*-job-*.txt
"""

import json
import os
import re
import sys


def _is_valid_evidence(evidence):
    """Check if evidence looks like a file path, not general knowledge."""
    if not evidence or not evidence.strip():
        return False
    e = evidence.strip()
    # Reject obvious non-file citations
    bad_prefixes = (
        "architectural",
        "design",
        "general knowledge",
        "by definition",
        "well-known",
        "documentation",
    )
    if e.lower().startswith(bad_prefixes):
        return False
    # Must contain a path separator or look like a filename
    return "/" in e or ":" in e or "." in e


def validate_file(filepath):
    """Validate a single report file.  Returns list of error strings."""
    with open(filepath, "r") as f:
        content = f.read()

    m = re.search(
        r"--- STRUCTURED SUMMARY ---\n(.+?)(?:\n--- END STRUCTURED SUMMARY ---|\Z)",
        content,
        re.DOTALL,
    )
    if not m:
        return []  # no summary block — not our problem (parse.py handles this)

    json_text = m.group(1)
    json_text = json_text.replace("\t", "\\t").replace("\r", "\\r")
    json_text = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]", "", json_text)

    try:
        entries = json.loads(json_text)
    except json.JSONDecodeError:
        return []  # malformed JSON — parse.py handles this

    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []

    errors = []
    for ei, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        chain = entry.get("causal_chain") or []
        sig = entry.get("error_signature", "<unknown>")
        for li, link in enumerate(chain):
            if not isinstance(link, dict):
                continue
            cause = link.get("cause", "")
            evidence = link.get("evidence", "").strip()
            quote = link.get("quote", "").strip()

            if not evidence and not quote:
                errors.append(
                    f"  [{sig}] chain link {li+1}: missing both evidence and quote"
                    f" — cause: {cause[:80]}"
                )
            elif not evidence:
                errors.append(
                    f"  [{sig}] chain link {li+1}: missing evidence field"
                    f" — cause: {cause[:80]}"
                )
            elif not quote:
                errors.append(
                    f"  [{sig}] chain link {li+1}: missing quote field"
                    f" — cause: {cause[:80]}"
                )
            elif not _is_valid_evidence(evidence):
                errors.append(
                    f"  [{sig}] chain link {li+1}: evidence is not a file path:"
                    f" '{evidence[:80]}' — cause: {cause[:80]}"
                )

    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: validate-reports.py <report.txt> [...]", file=sys.stderr)
        sys.exit(2)

    files = sys.argv[1:]
    total_errors = 0
    failed_files = []

    for filepath in files:
        if not os.path.isfile(filepath):
            continue
        errors = validate_file(filepath)
        if errors:
            name = os.path.basename(filepath)
            print(f"FAIL {name}:")
            for e in errors:
                print(e)
            print()
            total_errors += len(errors)
            failed_files.append((filepath, errors))
        else:
            name = os.path.basename(filepath)
            print(f"OK   {name}")

    print(f"\n{len(files)} files checked, {len(failed_files)} failed, {total_errors} errors")

    if failed_files:
        # Machine-readable output for the doctor workflow
        print("\n--- VALIDATION FAILURES ---")
        for filepath, errors in failed_files:
            print(f"FILE: {filepath}")
            for e in errors:
                print(e)
            print()
        print("--- END VALIDATION FAILURES ---")
        sys.exit(1)


if __name__ == "__main__":
    main()
