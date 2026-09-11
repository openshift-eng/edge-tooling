"""Analysis index for Prow-artifact RCA handoff.

Manages a JSON index file (analysis-index-v2.json) that tracks previous
RCA analyses keyed by (component, workflow, build_id, analyzer_fingerprint).
When a predecessor workdir is available, the index enables reusing prior
results instead of re-running Claude — saving cost and wall-clock time.

The index lives at ``<workdir>/analysis-index-v2.json``, scoped per doctor
run workdir.  No GCS cache prefix or new IAM is needed.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("doctor.index")

INDEX_FILENAME = "analysis-index-v2.json"
INDEX_VERSION = 2


def _compute_file_hash(path):
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_validator_version(validator_path=None):
    """Derive a version string from the validator script's content hash.

    Uses the first 12 hex chars of the SHA-256 of validate-rca-output.py.
    """
    if validator_path is None:
        validator_path = Path(__file__).resolve().parent / "validate-rca-output.py"
    else:
        validator_path = Path(validator_path)
    return _compute_file_hash(validator_path)[:12]


def compute_analyzer_fingerprint(model, prompt_content, validator_version):
    """Compute a SHA-256 fingerprint of the analyzer configuration.

    The fingerprint changes when any of these inputs change, invalidating
    predecessor analyses that used a different configuration.
    """
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt_content.encode("utf-8"))
    h.update(b"\x00")
    h.update(validator_version.encode("utf-8"))
    return h.hexdigest()


def make_reuse_key(component, workflow, build_id):
    """Build the index lookup key: ``<component>/<workflow>/<build_id>``."""
    return f"{component}/{workflow}/{build_id}"


# ------------------------------------------------------------------
# Index CRUD (plain-dict API)
# ------------------------------------------------------------------

def new_index():
    """Return a new empty index dict."""
    return {"version": INDEX_VERSION, "entries": {}}


def load_index(workdir):
    """Load an index from ``<workdir>/analysis-index-v2.json``.

    Returns a fresh empty index on any error (missing file, bad JSON,
    wrong version) — never raises.
    """
    path = Path(workdir) / INDEX_FILENAME
    if not path.is_file():
        return new_index()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        log.warning("Could not read predecessor index %s: %s", path, e)
        return new_index()
    if not isinstance(data, dict):
        return new_index()
    if data.get("version") != INDEX_VERSION:
        log.warning("Ignoring index with version %s (expected %d)",
                    data.get("version"), INDEX_VERSION)
        return new_index()
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {"version": INDEX_VERSION, "entries": {}}
    filtered = {k: v for k, v in entries.items() if isinstance(v, dict)}
    if len(filtered) < len(entries):
        log.warning("Discarded %d non-dict entries from index at %s",
                    len(entries) - len(filtered), path)
    return {"version": INDEX_VERSION, "entries": filtered}


def save_index(index_dict, workdir):
    """Write the index to ``<workdir>/analysis-index-v2.json``."""
    path = Path(workdir) / INDEX_FILENAME
    with open(path, "w") as f:
        json.dump(index_dict, f, indent=2)
    return path


def add_entry(index_dict, key, entry_dict):
    """Insert or replace the entry for *key*."""
    index_dict["entries"][key] = entry_dict


def get_entry(index_dict, key):
    """Return the entry dict for *key*, or ``None``."""
    return index_dict["entries"].get(key)


def lookup_predecessor(index_dict, key, current_fingerprint):
    """Look up a predecessor entry and validate its fingerprint.

    Returns ``(entry, True)`` when the entry exists and its
    ``analyzer_fingerprint`` matches *current_fingerprint*.
    Returns ``(entry, False)`` when the entry exists but the
    fingerprint does not match.
    Returns ``(None, False)`` when the key is not in the index.
    """
    entry = index_dict["entries"].get(key)
    if entry is None:
        return None, False
    if entry.get("analyzer_fingerprint") == current_fingerprint:
        return entry, True
    return entry, False


# ------------------------------------------------------------------
# Evidence rebasing
# ------------------------------------------------------------------

def rebase_evidence_paths(rca_output, old_workdir, new_workdir):
    """Rewrite absolute paths in causal_chain evidence entries.

    Replaces ``old_workdir`` prefix with ``new_workdir`` in every
    ``evidence`` field that starts with ``old_workdir``.  If a rebased
    file does not exist, the entry is kept but a warning string is
    returned (to be appended to ``analysis_gaps``).

    Returns ``(rebased_output, warnings)`` where *rebased_output* is a
    deep copy with paths rewritten and *warnings* is a list of strings.
    """
    old_prefix = str(old_workdir).rstrip("/")
    new_prefix = str(new_workdir).rstrip("/")
    warnings = []

    rebased = json.loads(json.dumps(rca_output))

    if not isinstance(rebased, list):
        return rebased, warnings

    for entry in rebased:
        if not isinstance(entry, dict):
            continue
        chain = entry.get("causal_chain")
        if not isinstance(chain, list):
            continue
        for link in chain:
            if not isinstance(link, dict):
                continue
            evidence = link.get("evidence", "")
            if not isinstance(evidence, str):
                continue

            m = re.fullmatch(r"(.+):(\d+)", evidence)
            if not m:
                continue
            path_part, line_no = m.group(1), m.group(2)

            if not path_part.startswith(old_prefix):
                continue

            new_path = new_prefix + path_part[len(old_prefix):]
            link["evidence"] = f"{new_path}:{line_no}"

            if not os.path.isfile(new_path):
                msg = (f"Rebased evidence file missing: {new_path} "
                       f"(original: {path_part})")
                warnings.append(msg)
                log.warning(msg)

    return rebased, warnings


def build_index_entry(component, workflow, build_id, fingerprint,
                      model, prompt_hash, validator_version,
                      workdir, rca_output):
    """Construct a complete index entry dict."""
    return {
        "build_id": build_id,
        "component": component,
        "workflow": workflow,
        "analyzer_fingerprint": fingerprint,
        "model": model,
        "prompt_hash": prompt_hash,
        "validator_version": validator_version,
        "workdir": str(workdir),
        "rca_output": rca_output,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "reused_count": 0,
    }


# ------------------------------------------------------------------
# Auto-predecessor discovery via Prow data.js
# ------------------------------------------------------------------

PROW_DATA_URL = "https://prow.ci.openshift.org/data.js"


def _prow_url_to_gcs(url):
    """Convert a Prow view URL to a GCS path.

    Mirrors ``url_to_gcs()`` in download-jobs.sh.
    """
    url = re.sub(
        r"^https://prow\.ci\.openshift\.org/view/gs/", "gs://", url)
    url = re.sub(
        r"^https://gcsweb-ci\.apps\.ci\.l2s4\.p1\.openshiftapps\.com/gcs/",
        "gs://", url)
    return url


def discover_predecessor_index(doctor_job_pattern, current_build_id=None):
    """Find the most recent successful doctor run and download its index.

    Fetches the Prow ``data.js`` feed, finds jobs matching
    *doctor_job_pattern* (substring match on job name), picks the most
    recent successful run (excluding *current_build_id*), and downloads
    its ``analysis-index-v2.json`` via ``gsutil cp``.

    Returns the path to a temp directory containing the downloaded index
    file, or ``None`` if no suitable predecessor is found or any error
    occurs.  All errors are logged as warnings — this function never
    raises.
    """
    try:
        req = urllib.request.Request(PROW_DATA_URL, headers={
            "Accept-Encoding": "gzip",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("[AUTO] Failed to fetch Prow data.js: %s", exc)
        return None

    if not isinstance(data, list):
        log.warning("[AUTO] Unexpected data.js format (not a list)")
        return None

    # Filter to matching doctor jobs that succeeded
    candidates = []
    for job in data:
        if not isinstance(job, dict):
            continue
        job_name = job.get("job", "")
        if doctor_job_pattern not in job_name:
            continue
        if job.get("state") != "success":
            continue
        build_id = str(job.get("build_id", ""))
        if current_build_id and build_id == str(current_build_id):
            continue
        url = job.get("url", "")
        started = job.get("started", "0")
        if url:
            candidates.append((started, build_id, url))

    if not candidates:
        log.info("[AUTO] No matching successful doctor jobs for pattern '%s'",
                 doctor_job_pattern)
        return None

    # Sort by started timestamp descending, pick most recent
    candidates.sort(key=lambda x: x[0], reverse=True)
    started, build_id, prow_url = candidates[0]

    gcs_path = _prow_url_to_gcs(prow_url)
    if gcs_path == prow_url:
        log.warning("[AUTO] Could not convert Prow URL to GCS path: %s",
                    prow_url)
        return None

    # Try downloading analysis-index-v2.json from GCS
    tmpdir = tempfile.mkdtemp(prefix="doctor-pred-")
    dest = os.path.join(tmpdir, INDEX_FILENAME)

    # Prow CI step layout: artifacts/<step>/<container>/artifacts/
    container = f"openshift-edge-tooling-{doctor_job_pattern}"
    gcs_index_path = (f"{gcs_path}/artifacts/{doctor_job_pattern}/"
                      f"{container}/artifacts/{INDEX_FILENAME}")

    for gcs_file in [gcs_index_path]:
        result = subprocess.run(
            ["gsutil", "-q", "cp", gcs_file, dest],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode == 0 and os.path.isfile(dest):
            log.info("[AUTO] Downloaded predecessor index from build %s",
                     build_id)
            return tmpdir

    log.info("[AUTO] No analysis-index-v2.json found in build %s "
             "(bootstrap case)", build_id)
    return None
