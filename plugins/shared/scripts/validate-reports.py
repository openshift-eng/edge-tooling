#!/usr/bin/env python3
"""Validate causal-chain citations in per-job analysis reports.

Verifies that every causal-chain link in the STRUCTURED SUMMARY cites a
real file, an in-range line number, and a quote that actually appears at
(or near) the cited location.  Citation paths are resolved against the
workdir, the job's downloaded artifacts (derived from the entry's
job_url build id), and source checkouts under <workdir>/src/.

Prints validation errors per file.  Exits 0 if all files pass, 1 if any fail.

Usage:
    validate-reports.py [--workdir DIR] <report1.txt> [<report2.txt> ...]
    validate-reports.py <workdir>/jobs/release-*-job-*.txt

When --workdir is omitted it is derived from each report's path
(reports live in <workdir>/jobs/).  When no artifact roots can be found
at all (e.g. the workdir was moved), falls back to format-only checks.
"""

import glob as glob_mod
import json
import os
import re
import sys

QUOTE_SEARCH_WINDOW = 5      # lines around the cited line to search for the quote
MIN_NEEDLE_LEN = 8           # normalized quote fragments shorter than this are not checked
BINARY_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".gif")

# Timestamp shapes commonly copied from logs; stripped before comparing.
_TIMESTAMP_RES = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"),
    re.compile(r"[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"),  # journal: Jun 30 03:58:39
    re.compile(r"\d{2}:\d{2}:\d{2}(\.\d+)?"),
    re.compile(r"\[\s*\d+\.\d+\]"),                              # kernel: [ 1234.567890]
]


def _normalize(text):
    """Strip timestamps, collapse whitespace, casefold — for lenient matching."""
    for ts_re in _TIMESTAMP_RES:
        text = ts_re.sub(" ", text)
    return " ".join(text.split()).casefold()


def _quote_needle(quote):
    """Pick the longest verifiable fragment of a quote.

    Agents sometimes elide long lines with '...' — verify the longest
    fragment instead of the full quote.
    """
    fragments = re.split(r"\.{3}|…", quote)
    fragments = [_normalize(f) for f in fragments]
    fragments = [f for f in fragments if f]
    if not fragments:
        return ""
    return max(fragments, key=len)


def _split_line_ref(evidence):
    """Split 'path/to/file.log:4629' → ('path/to/file.log', 4629)."""
    m = re.match(r"^(.*?):(\d+)$", evidence.strip())
    if m:
        return m.group(1), int(m.group(2))
    return evidence.strip(), None


def _build_id_from_url(job_url):
    """The Prow job URL's last path segment is the build id."""
    return job_url.rstrip("/").rsplit("/", 1)[-1] if job_url else ""


def _candidate_roots(workdir, build_id):
    """Directories a citation path may be relative to, most specific first.

    Returns (roots, verifiable).  verifiable is False when the workdir has
    no downloaded artifacts at all (e.g. pruned or moved) — real
    verification is impossible and callers fall back to format-only checks.
    """
    roots = []
    if not workdir:
        return roots, False
    artifacts_root = os.path.join(workdir, "artifacts")
    verifiable = os.path.isdir(artifacts_root)
    build_dir = os.path.join(artifacts_root, build_id) if build_id else ""
    if build_dir and os.path.isdir(build_dir):
        roots.append(build_dir)
    if verifiable:
        # Group reports may cite artifacts of any member job, so all
        # downloaded builds are candidate roots (own build dir first).
        roots.extend(
            d for d in sorted(glob_mod.glob(os.path.join(artifacts_root, "*")))
            if os.path.isdir(d) and d != build_dir
        )
    for src_dir in sorted(glob_mod.glob(os.path.join(workdir, "src", "*"))):
        if os.path.isdir(src_dir):
            roots.append(src_dir)
    if os.path.isdir(workdir):
        roots.append(workdir)
    return roots, verifiable


def _resolve_path(path, roots):
    """Resolve a cited path against candidate roots.  Returns None if not found."""
    if os.path.isabs(path):
        return path if os.path.isfile(path) else None
    rel = path.lstrip("./")
    for root in roots:
        candidate = os.path.join(root, rel)
        if os.path.isfile(candidate):
            return candidate
    return None


def _find_quote(filepath, line_num, needle):
    """Check whether needle appears near line_num in filepath.

    Returns (ok, error_message).  On mismatch the message includes where
    the quote actually is (if anywhere), so fix agents can re-ground it.
    """
    try:
        with open(filepath, errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return False, f"cannot read file: {e}"

    total = len(lines)
    if line_num is not None and line_num > total:
        return False, f"line {line_num} out of range (file has {total} lines)"

    if line_num is not None:
        lo = max(0, line_num - 1 - QUOTE_SEARCH_WINDOW)
        hi = min(total, line_num + QUOTE_SEARCH_WINDOW)
        window = _normalize("".join(lines[lo:hi]))
        if needle in window:
            return True, ""

    for i, line in enumerate(lines, 1):
        if needle in _normalize(line):
            if line_num is None:
                return True, ""
            return False, (
                f"quote not found at cited line {line_num}"
                f" (found at line {i})"
            )

    # Multi-line quotes may straddle line boundaries; try the whole file.
    if needle in _normalize("".join(lines)):
        if line_num is None:
            return True, ""
        return False, f"quote not found near cited line {line_num}"

    return False, "quote not found anywhere in cited file"


def _is_plausible_path(evidence):
    """Legacy format-only heuristic, used when no artifact roots exist."""
    e = evidence.strip()
    if not e:
        return False
    bad_prefixes = (
        "architectural", "design", "general knowledge",
        "by definition", "well-known", "documentation",
    )
    if e.lower().startswith(bad_prefixes):
        return False
    return "/" in e or ":" in e or "." in e


def _load_entries(filepath):
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
    return [e for e in entries if isinstance(e, dict)]


def validate_file(filepath, workdir=None):
    """Validate a single report file.  Returns list of error strings."""
    entries = _load_entries(filepath)
    if not entries:
        return []

    if workdir is None:
        # Reports live in <workdir>/jobs/ — derive the workdir.
        workdir = os.path.dirname(os.path.dirname(os.path.abspath(filepath)))

    errors = []
    for entry in entries:
        chain = entry.get("causal_chain") or []
        sig = entry.get("error_signature", "<unknown>")
        build_id = _build_id_from_url(entry.get("job_url", ""))
        roots, verifiable = _candidate_roots(workdir, build_id)

        for li, link in enumerate(chain):
            if not isinstance(link, dict):
                continue
            cause = link.get("cause", "")
            evidence = (link.get("evidence") or "").strip()
            quote = (link.get("quote") or "").strip()

            def err(msg):
                errors.append(f"  [{sig}] chain link {li+1}: {msg} — cause: {cause[:80]}")

            if not evidence and not quote:
                err("missing both evidence and quote")
                continue
            if not evidence:
                err("missing evidence field")
                continue
            if not quote:
                err("missing quote field")
                continue

            path, line_num = _split_line_ref(evidence)
            resolved = _resolve_path(path, roots)

            if not verifiable and resolved is None:
                # Workdir moved or artifacts pruned — format check only.
                if not _is_plausible_path(evidence):
                    err(f"evidence is not a file path: '{evidence[:80]}'")
                continue

            if resolved is None:
                if not _is_plausible_path(evidence):
                    err(f"evidence is not a file path: '{evidence[:80]}'")
                else:
                    err(f"cited file not found: '{path[:120]}'")
                continue

            if resolved.lower().endswith(BINARY_EXTENSIONS):
                continue  # graphs etc. — existence is all we can check

            needle = _quote_needle(quote)
            if len(needle) < MIN_NEEDLE_LEN:
                continue  # too short to verify meaningfully

            ok, msg = _find_quote(resolved, line_num, needle)
            if not ok:
                err(f"{msg} ('{os.path.basename(resolved)}')")

    return errors


def main():
    args = sys.argv[1:]
    workdir = None
    files = []
    i = 0
    while i < len(args):
        if args[i] == "--workdir":
            if i + 1 >= len(args):
                print("Error: --workdir requires an argument", file=sys.stderr)
                sys.exit(2)
            workdir = args[i + 1]
            i += 2
        else:
            files.append(args[i])
            i += 1

    if not files:
        print("Usage: validate-reports.py [--workdir DIR] <report.txt> [...]", file=sys.stderr)
        sys.exit(2)

    total_errors = 0
    failed_files = []
    checked = 0

    for filepath in files:
        if not os.path.isfile(filepath):
            continue
        checked += 1
        errors = validate_file(filepath, workdir)
        name = os.path.basename(filepath)
        if errors:
            print(f"FAIL {name}:")
            for e in errors:
                print(e)
            print()
            total_errors += len(errors)
            failed_files.append((filepath, errors))
        else:
            print(f"OK   {name}")

    print(f"\n{checked} files checked, {len(failed_files)} failed, {total_errors} errors")

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
