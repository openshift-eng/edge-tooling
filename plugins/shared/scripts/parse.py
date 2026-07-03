"""Shared parsing and grouping for CI analysis scripts.

Provides parse_structured_summary() and group_by_signature() used by
aggregate.py and search-bugs.py so that both scripts parse and group
failures identically.
"""

import json
import re


# ---------------------------------------------------------------------------
# Grouping constants
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by",
    "is", "was", "are", "were", "be", "been", "and", "or", "not", "no",
    "but", "from", "that", "this", "all", "has", "have", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "might",
})

SIMILARITY_THRESHOLD = 0.50


def _parse_bool(value):
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def parse_structured_summary(filepath):
    """Extract the STRUCTURED SUMMARY JSON array from a per-job report file.

    Returns a list of dicts, one per failure entry. Returns [] if the file
    has no STRUCTURED SUMMARY block or the JSON is malformed.
    """
    with open(filepath, "r") as f:
        content = f.read()

    m = re.search(
        r"--- STRUCTURED SUMMARY ---\n(.+?)(?:\n--- END STRUCTURED SUMMARY ---|\Z)",
        content, re.DOTALL,
    )
    if not m:
        return []

    json_text = m.group(1)
    # LLM agents sometimes copy raw control characters (tabs, CRs) from
    # build logs into JSON string values.  Sanitize before parsing.
    json_text = json_text.replace('\t', '\\t').replace('\r', '\\r')
    json_text = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]', '', json_text)

    try:
        entries = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if isinstance(entries, dict):
        entries = [entries]
    elif not isinstance(entries, list):
        return []

    results = []
    for data in entries:
        if not isinstance(data, dict):
            continue
        try:
            severity = max(1, min(5, int(data.get("severity", 3))))
        except (ValueError, TypeError):
            severity = 3

        results.append({
            "severity": severity,
            "stack_layer": data.get("stack_layer", ""),
            "step_name": data.get("step_name", ""),
            "error_signature": data.get("error_signature", ""),
            "raw_error": data.get("raw_error", ""),
            "root_cause": data.get("root_cause", ""),
            "infrastructure_failure": _parse_bool(data.get("infrastructure_failure", False)),
            "job_url": data.get("job_url", ""),
            "job_name": data.get("job_name", ""),
            "release": data.get("release", ""),
            "finished": data.get("finished", ""),
            "remediation": data.get("remediation", ""),
            "confidence": data.get("confidence", ""),
            "causal_chain": [
                link for link in (data.get("causal_chain") or [])
                if isinstance(link, dict) and "cause" in link
            ][:10],
            "analysis_gaps": [
                gap for gap in (data.get("analysis_gaps") or [])
                if isinstance(gap, str)
            ],
            "scenarios": [
                s for s in (data.get("scenarios") or [])
                if isinstance(s, str)
            ],
        })

    return results


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def normalize_step_name(step_name):
    """Extract the step ref from a fully-qualified Prow step name.

    Prow step names follow ``<test-variant>-<step-ref>`` where the
    step ref typically starts with ``openshift-microshift-``.  The LLM
    sometimes includes the test-variant prefix, sometimes not, which
    would cause identical steps to land in different buckets.

    The regex harmlessly falls through for components that don't match
    the MicroShift pattern — the original step_name is returned as-is.
    """
    m = re.search(r"(openshift-microshift-\S+)", step_name)
    return m.group(1) if m else step_name


def tokenize(text, stop_words=None):
    if stop_words is None:
        stop_words = STOP_WORDS
    words = re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]", text.lower())
    return {w for w in words if w not in stop_words and len(w) >= 2}


def signature_similarity(sig_a, sig_b):
    tokens_a = tokenize(sig_a)
    tokens_b = tokenize(sig_b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def grouping_text(job):
    """Return the text used for similarity grouping.

    Prefers RAW_ERROR (verbatim log text, deterministic) over
    ERROR_SIGNATURE (LLM-paraphrased, variable across runs).
    Appends ROOT_CAUSE when present to improve cross-release matching
    for failures that share the same underlying mechanism.
    """
    base = job.get("raw_error") or job.get("error_signature", "")
    root_cause = job.get("root_cause", "")
    if root_cause:
        return base + " " + root_cause
    return base


def cluster_by_similarity(items, key_fn):
    """Cluster items whose key texts exceed the similarity threshold.

    A new item is compared against ALL existing members of each cluster.
    If any member exceeds the threshold the item joins that cluster.
    """
    groups = []
    for item in items:
        sig = key_fn(item)
        placed = False
        for group in groups:
            if any(
                signature_similarity(sig, key_fn(member)) >= SIMILARITY_THRESHOLD
                for member in group
            ):
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    return groups


def group_by_signature(jobs):
    """Two-pass grouping: first by step_name, then by signature similarity.

    Grouping by step_name first prevents jobs from different CI steps
    (e.g. conformance vs metal-tests) from being merged together even
    when their error signatures share enough tokens to exceed the
    similarity threshold.
    """
    by_step = {}
    for job in jobs:
        step = normalize_step_name(job.get("step_name", ""))
        by_step.setdefault(step, []).append(job)

    all_groups = []
    for step_jobs in by_step.values():
        all_groups.extend(cluster_by_similarity(step_jobs, grouping_text))
    return all_groups
