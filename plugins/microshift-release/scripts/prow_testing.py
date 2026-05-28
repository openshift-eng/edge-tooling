#!/usr/bin/env python3
"""Prow CI release testing workflow for MicroShift.

Manages the lifecycle of release testing PRs: create PR, trigger CI jobs,
check status, download artifacts. Supports 4.21+ only.

Usage: prow_testing.py <action> <version> [options]
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile

import requests

from lib import git_ops, prow

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def _require_pr(version_info):
    """Find the release testing PR or exit with an error."""
    pr = prow.find_release_testing_pr(version_info["pr_title"])
    if pr is None:
        print(json.dumps({
            "action": "error",
            "message": (
                f"No release testing PR found for {version_info['version']}. "
                f"Run 'create-pr' to create one first."
            ),
        }))
        sys.exit(1)
    if isinstance(pr, list):
        print(json.dumps({
            "action": "error",
            "message": "Multiple matching PRs found.",
            "prs": pr,
        }))
        sys.exit(1)
    return pr


def _format_status_table(version_info, pr, statuses):
    """Format job statuses as a human-readable table."""
    lines = [
        f"Release Testing CI Status for {version_info['version']}",
        f"PR: #{pr['number']} ({pr['url']})",
        "",
    ]

    col_job = max(len(s["short_name"]) for s in statuses)
    col_job = max(col_job, len("Job"))
    status_icons = {
        "SUCCESS": "✅",
        "FAILURE": "❌",
        "ABORTED": "❌",
        "PENDING": "\U0001f7e0",
    }
    col_status = 9

    header = f"{'Job':<{col_job}} | {'Status':<{col_status}} | Prow URL"
    sep = f"{'-' * col_job}-|-{'-' * col_status}-|{'-' * 50}"
    lines.append(header)
    lines.append(sep)

    for s in statuses:
        url_display = s["url"] if s["url"] else "(not started)"
        icon = status_icons.get(s["status"], "")
        display = f"{icon} {s['status']}" if icon else s["status"]
        lines.append(f"{s['short_name']:<{col_job}} | {display:<{col_status}} | {url_display}")

    lines.append("")

    counts = {}
    for s in statuses:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    summary_parts = [
        f"{v} {status_icons.get(k, '')} {k}".strip() for k, v in counts.items()
    ]
    lines.append(f"Summary: {', '.join(summary_parts)}")

    if counts.get("SUCCESS", 0) == len(statuses):
        lines.append("")
        lines.append(
            "All CI jobs passed. Run 'download' to download artifacts."
        )

    return "\n".join(lines)


_BUILD_CACHE_ARCHES = ["x86_64", "aarch64"]


def _check_s3_rpms(v):
    """Check if RPMs exist in the S3 build cache for the target version."""
    if not shutil.which("aws"):
        return {
            "check": "s3_rpms",
            "status": "FAIL",
            "reason": "aws CLI not found. Install and configure AWS CLI.",
        }

    minor = v["minor"]
    version = v["version"]

    failures = []
    details = []
    tar_path_for_version_check = None

    for arch in _BUILD_CACHE_ARCHES:
        base = f"{prow.S3_BUILD_CACHE}/release-{minor}/{arch}"

        last_result = subprocess.run(
            ["aws", "s3", "cp", f"{base}/last", "-"],
            capture_output=True, text=True,
        )
        if last_result.returncode != 0 or not last_result.stdout.strip():
            failures.append(f"{arch}: 'last' marker not found at {base}/last")
            continue

        date = last_result.stdout.strip()
        tar_path = f"{base}/{date}/brew-rpms.tar"

        ls_result = subprocess.run(
            ["aws", "s3", "ls", tar_path],
            capture_output=True, text=True,
        )
        if ls_result.returncode != 0 or not ls_result.stdout.strip():
            failures.append(f"{arch}: brew-rpms.tar not found at {tar_path}")
            continue

        details.append(f"{arch}: brew-rpms.tar found (date={date})")
        if tar_path_for_version_check is None:
            tar_path_for_version_check = tar_path

    if failures:
        return {
            "check": "s3_rpms",
            "status": "FAIL",
            "reason": f"Build cache check failed for {len(failures)} arch(es)",
            "details": failures + details,
        }

    if tar_path_for_version_check:
        brew_version = version.replace("-", "~")
        try:
            proc = subprocess.Popen(
                f"aws s3 cp '{tar_path_for_version_check}' - 2>/dev/null"
                f" | tar tf - 2>/dev/null | grep -m1 '{brew_version}'",
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            stdout, _ = proc.communicate(timeout=300)
            match = stdout.decode().strip()
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                proc.kill()
            match = None
        except OSError:
            match = None

        if match:
            details.append(f"Version match: found {brew_version} RPMs in tar")
        elif match is None:
            details.append(
                f"Version check timed out (tar is large) — "
                f"could not confirm {brew_version} RPMs"
            )
        else:
            return {
                "check": "s3_rpms",
                "status": "FAIL",
                "reason": (
                    f"brew-rpms.tar does not contain RPMs for {version} "
                    f"(searched for '{brew_version}')"
                ),
                "details": details,
            }

    return {
        "check": "s3_rpms",
        "status": "PASS",
        "reason": "brew-rpms.tar found in build cache for both arches",
        "details": details,
    }


_GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results"
_SCENARIO_ROW_RE = re.compile(
    r'<tr class="(status-pass|status-fail|status-skip)">'
)
_SCENARIO_NAME_RE = re.compile(
    r'<a class="scenario-link"[^>]*>([^<]+)</a>'
)
_VERSION_BADGE_RE = re.compile(
    r'<span class="version-badge">([^<]+)</span>'
)


def _parse_scenarios(html):
    """Parse custom-link-tools.html and extract scenario results."""
    scenarios = []
    rows = html.split("<tr ")
    for row in rows:
        status_match = _SCENARIO_ROW_RE.search("<tr " + row)
        if not status_match:
            continue

        status_class = status_match.group(1)
        if status_class == "status-pass":
            status = "pass"
        elif status_class == "status-fail":
            status = "fail"
        else:
            status = "skip"

        name_match = _SCENARIO_NAME_RE.search(row)
        name = name_match.group(1) if name_match else "unknown"

        version_match = _VERSION_BADGE_RE.search(row)
        version = version_match.group(1) if version_match else "-"

        scenarios.append({"name": name, "status": status, "version": version})

    return scenarios


def _custom_link_url(pr_number, full_job_name, build_id, short_name):
    """Construct the custom-link-tools.html URL for a job."""
    return (
        f"{_GCSWEB_BASE}/{prow.GCS_PR_PREFIX}/{pr_number}/"
        f"{full_job_name}/{build_id}/artifacts/{short_name}/"
        "openshift-microshift-e2e-metal-tests/artifacts/custom-link-tools.html"
    )


def cmd_scenarios(args):
    """Validate scenarios in completed jobs: check for skips and version mismatches."""
    v = prow.parse_version(args.version)
    pr = _require_pr(v)
    pr_number = pr["number"]

    gcs_jobs = prow.list_pr_jobs(pr_number)
    matched = prow.match_ci_jobs(gcs_jobs, v["minor"])
    statuses = prow.fetch_all_job_statuses(pr_number, matched, v["minor"])

    brew_version = v["version"].replace("-", "~")
    job_results = []
    has_version_mismatch = False

    for s in statuses:
        if s["status"] not in ("SUCCESS", "FAILURE") or not s.get("build_id"):
            continue

        short = s["short_name"]
        full = matched.get(short)
        if not full:
            continue

        url = _custom_link_url(pr_number, full, s["build_id"], short)

        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                job_results.append({
                    "job": short,
                    "status": "error",
                    "reason": f"HTTP {resp.status_code} fetching custom-link-tools.html",
                })
                continue
        except requests.RequestException as e:
            job_results.append({
                "job": short,
                "status": "error",
                "reason": str(e),
            })
            continue

        scenarios = _parse_scenarios(resp.text)
        skipped = [sc for sc in scenarios if sc["status"] == "skip"]
        failed = [sc for sc in scenarios if sc["status"] == "fail"]
        mismatches = [
            sc for sc in scenarios
            if sc["version"] not in ("-", brew_version)
            and "nightly" not in sc["version"]
        ]

        versions_seen = [
            sc["version"] for sc in scenarios if sc["version"] != "-"
        ]
        if versions_seen:
            release_under_test = max(set(versions_seen), key=versions_seen.count)
        else:
            release_under_test = "-"

        job_entry = {
            "job": short,
            "release_under_test": release_under_test,
            "total": len(scenarios),
            "passed": sum(1 for sc in scenarios if sc["status"] == "pass"),
            "failed": len(failed),
            "skipped": len(skipped),
        }

        if skipped:
            job_entry["skipped_scenarios"] = [sc["name"] for sc in skipped]
        if failed:
            job_entry["failed_scenarios"] = [sc["name"] for sc in failed]
        if mismatches:
            has_version_mismatch = True
            job_entry["version_mismatches"] = [
                {"name": sc["name"], "version": sc["version"]}
                for sc in mismatches
            ]

        job_results.append(job_entry)

    has_failed = any(j.get("failed", 0) > 0 for j in job_results)
    has_wrong_release = any(
        j.get("release_under_test") not in ("-", brew_version)
        for j in job_results
    )

    if has_version_mismatch or has_failed or has_wrong_release:
        overall = "fail"
    else:
        overall = "pass"

    output = {
        "action": "scenarios",
        "status": overall,
        "version": v["version"],
        "jobs": job_results,
    }

    total_jobs = len(job_results)
    total_scenarios = sum(j.get("total", 0) for j in job_results)
    total_passed = sum(j.get("passed", 0) for j in job_results)
    total_failed = sum(j.get("failed", 0) for j in job_results)
    total_skipped = sum(j.get("skipped", 0) for j in job_results)

    parts = [f"{total_jobs} jobs, {total_scenarios} scenarios"]
    parts.append(f"{total_passed} passed")
    if total_failed:
        parts.append(f"{total_failed} failed")
    if total_skipped:
        parts.append(f"{total_skipped} skipped")

    errors = []
    if has_wrong_release:
        wrong = [
            f"{j['job']}={j['release_under_test']}"
            for j in job_results
            if j.get("release_under_test") not in ("-", brew_version)
        ]
        errors.append(f"release mismatch ({', '.join(wrong)})")
    if has_version_mismatch:
        errors.append("scenario version mismatch")

    summary = ", ".join(parts)
    if errors:
        summary += " — " + "; ".join(errors)

    output["message"] = summary

    print(json.dumps(output))

    if overall == "fail":
        sys.exit(1)


def cmd_preflight(args):
    """Run pre-flight checks before creating the release testing PR."""
    v = prow.parse_version(args.version)

    checks = []

    logger.info("Verifying RPMs exist in build cache...")
    checks.append(_check_s3_rpms(v))

    has_fail = any(c["status"] == "FAIL" for c in checks)
    has_warn = any(c["status"] == "WARN" for c in checks)

    if has_fail:
        overall = "fail"
    elif has_warn:
        overall = "warn"
    else:
        overall = "pass"

    output = {
        "action": "preflight",
        "status": overall,
        "version": v["version"],
        "checks": checks,
    }

    if has_fail:
        output["message"] = "Pre-flight checks failed. Fix issues before creating PR."
    elif has_warn:
        output["message"] = "Pre-flight checks passed with warnings. Review before proceeding."
    else:
        output["message"] = "All pre-flight checks passed. Ready to create PR."

    print(json.dumps(output))

    if has_fail:
        sys.exit(1)


def cmd_create_pr(args):
    """Create a draft PR with an empty commit for release testing."""
    v = prow.parse_version(args.version)
    existing = prow.find_release_testing_pr(v["pr_title"])

    if existing and not isinstance(existing, list):
        print(json.dumps({
            "action": "create-pr",
            "status": "exists",
            "pr": existing,
            "message": (
                f"Release testing PR already exists: "
                f"PR #{existing['number']}: {existing['title']} — {existing['url']}"
            ),
        }))
        return

    if isinstance(existing, list):
        print(json.dumps({
            "action": "create-pr",
            "status": "error",
            "message": "Multiple matching PRs found.",
            "prs": existing,
        }))
        sys.exit(1)

    head_branch = f"release-testing-{v['version']}"

    if not args.execute:
        print(json.dumps({
            "action": "create-pr",
            "status": "plan",
            "version": v["version"],
            "repo": prow.GH_REPO,
            "base_branch": v["branch"],
            "head_branch": head_branch,
            "pr_title": v["pr_title"],
            "mode": "draft",
        }))
        return

    logger.info("Ensuring MicroShift repo clone exists...")
    repo_dir = git_ops.ensure_microshift_repo()
    git_ops.fetch_branch(v["branch"])

    logger.info("Creating branch %s from origin/%s...", head_branch, v["branch"])
    subprocess.run(
        ["git", "checkout", "-b", head_branch, f"origin/{v['branch']}"],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    )

    logger.info("Creating empty commit...")
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", f"Release testing for {v['version']}"],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    )

    logger.info("Pushing %s to origin...", head_branch)
    push_result = subprocess.run(
        ["git", "push", "origin", head_branch],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if push_result.returncode != 0:
        print(json.dumps({
            "action": "create-pr",
            "status": "error",
            "message": f"git push failed: {push_result.stderr.strip()}",
        }))
        sys.exit(1)

    logger.info("Creating draft PR...")
    body = (
        f"Release testing PR for MicroShift {v['version']}. Do not merge.\n\n"
        "Created by /microshift-release:automated-testing"
    )
    pr_result = subprocess.run(
        [
            "gh", "pr", "create",
            "--repo", prow.GH_REPO,
            "--base", v["branch"],
            "--head", head_branch,
            "--title", v["pr_title"],
            "--body", body,
            "--draft",
        ],
        capture_output=True, text=True,
    )
    if pr_result.returncode != 0:
        print(json.dumps({
            "action": "create-pr",
            "status": "error",
            "message": f"gh pr create failed: {pr_result.stderr.strip()}",
        }))
        sys.exit(1)

    pr_url = pr_result.stdout.strip()
    print(json.dumps({
        "action": "create-pr",
        "status": "created",
        "url": pr_url,
        "message": f"Draft PR created: {pr_url}",
    }))


def cmd_trigger(args):
    """Post /test comments to trigger failed CI jobs only."""
    v = prow.parse_version(args.version)
    pr = _require_pr(v)

    logger.info("Fetching job statuses for PR #%s...", pr["number"])
    gcs_jobs = prow.list_pr_jobs(pr["number"])
    matched = prow.match_ci_jobs(gcs_jobs, v["minor"])
    statuses = prow.fetch_all_job_statuses(pr["number"], matched, v["minor"])

    failed_statuses = {"FAILURE", "ABORTED", "ERROR"}
    not_started = [s for s in statuses if s["status"] == "--"]
    failed = [s for s in statuses if s["status"] in failed_statuses]
    jobs_to_trigger = not_started + failed

    if not jobs_to_trigger:
        print(json.dumps({
            "action": "trigger",
            "status": "skip",
            "pr_number": pr["number"],
            "pr_url": pr["url"],
            "message": "No failed jobs to retrigger. All jobs are successful or in progress.",
            "jobs": [{"job": s["short_name"], "status": s["status"]} for s in statuses],
        }))
        return

    trigger_names = [s["short_name"] for s in jobs_to_trigger]
    comment_body = "\n".join(f"/test {job}" for job in trigger_names)

    if not args.execute:
        print(json.dumps({
            "action": "trigger",
            "status": "plan",
            "pr_number": pr["number"],
            "pr_url": pr["url"],
            "comment": comment_body,
            "jobs": trigger_names,
            "skipped": [
                {"job": s["short_name"], "status": s["status"]}
                for s in statuses if s not in jobs_to_trigger
            ],
        }))
        return

    logger.info("Posting /test comment on PR #%s...", pr["number"])
    result = subprocess.run(
        [
            "gh", "pr", "comment", str(pr["number"]),
            "--repo", prow.GH_REPO,
            "--body", comment_body,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(json.dumps({
            "action": "trigger",
            "status": "error",
            "message": f"gh pr comment failed: {result.stderr.strip()}",
        }))
        sys.exit(1)

    print(json.dumps({
        "action": "trigger",
        "status": "triggered",
        "pr_number": pr["number"],
        "jobs": trigger_names,
        "message": f"{len(trigger_names)} failed job(s) retriggered on PR #{pr['number']}.",
    }))


def cmd_status(args):
    """Check and display CI job statuses."""
    v = prow.parse_version(args.version)
    pr = _require_pr(v)

    logger.info("Fetching job list from GCS for PR #%s...", pr["number"])
    gcs_jobs = prow.list_pr_jobs(pr["number"])
    matched = prow.match_ci_jobs(gcs_jobs, v["minor"])

    logger.info("Fetching build statuses...")
    statuses = prow.fetch_all_job_statuses(pr["number"], matched, v["minor"])

    if args.json_output:
        print(json.dumps({
            "action": "status",
            "pr": pr,
            "jobs": statuses,
        }))
    else:
        print(_format_status_table(v, pr, statuses))


def cmd_complete(args):
    """Post completion comment, close PR, and delete branch."""
    v = prow.parse_version(args.version)
    pr = _require_pr(v)
    head_branch = f"release-testing-{v['version']}"
    comment_body = (
        f"Automated release testing for {v['version']} completed. "
        "Artifacts uploaded to S3."
    )

    if not args.execute:
        print(json.dumps({
            "action": "complete",
            "status": "plan",
            "pr_number": pr["number"],
            "pr_url": pr["url"],
            "actions": [
                f"Post completion comment on PR #{pr['number']}",
                f"Close PR #{pr['number']}",
                f"Delete remote branch {head_branch}",
            ],
        }))
        return

    logger.info("Posting completion comment on PR #%s...", pr["number"])
    comment_result = subprocess.run(
        [
            "gh", "pr", "comment", str(pr["number"]),
            "--repo", prow.GH_REPO,
            "--body", comment_body,
        ],
        capture_output=True, text=True,
    )
    if comment_result.returncode != 0:
        print(json.dumps({
            "action": "complete",
            "status": "error",
            "message": f"gh pr comment failed: {comment_result.stderr.strip()}",
        }))
        sys.exit(1)

    logger.info("Closing PR #%s and deleting branch %s...", pr["number"], head_branch)
    close_result = subprocess.run(
        [
            "gh", "pr", "close", str(pr["number"]),
            "--repo", prow.GH_REPO,
            "--delete-branch",
        ],
        capture_output=True, text=True,
    )
    if close_result.returncode != 0:
        print(json.dumps({
            "action": "complete",
            "status": "error",
            "message": f"gh pr close failed: {close_result.stderr.strip()}",
        }))
        sys.exit(1)

    print(json.dumps({
        "action": "complete",
        "status": "completed",
        "pr_number": pr["number"],
        "url": pr["url"],
        "message": (
            f"PR #{pr['number']} commented, closed, "
            f"and branch {head_branch} deleted."
        ),
    }))


def cmd_download(args):
    """Download job artifacts from GCS."""
    v = prow.parse_version(args.version)
    pr = _require_pr(v)

    if not shutil.which("gsutil"):
        print(json.dumps({
            "action": "download",
            "status": "error",
            "message": "gsutil not found. Install Google Cloud SDK.",
        }))
        sys.exit(1)

    logger.info("Checking job statuses for PR #%s...", pr["number"])
    gcs_jobs = prow.list_pr_jobs(pr["number"])
    matched = prow.match_ci_jobs(gcs_jobs, v["minor"])
    statuses = prow.fetch_all_job_statuses(pr["number"], matched, v["minor"])

    completed = [
        s for s in statuses
        if s["status"] in ("SUCCESS", "FAILURE") and s["job"]
    ]

    if not completed:
        print(json.dumps({
            "action": "download",
            "status": "error",
            "message": "No completed jobs to download.",
            "jobs": statuses,
        }))
        sys.exit(1)

    download_dir = os.path.join(
        git_ops._get_edge_tooling_root(),
        "_output",
        f"release-testing-{v['version']}",
    )

    if not args.execute:
        print(json.dumps({
            "action": "download",
            "status": "plan",
            "pr_number": pr["number"],
            "pr_url": pr["url"],
            "download_dir": download_dir,
            "jobs": [s["short_name"] for s in completed],
        }))
        return

    os.makedirs(download_dir, exist_ok=True)

    results = []
    for s in completed:
        gcs_path = (
            f"gs://test-platform-results/{prow.GCS_PR_PREFIX}/"
            f"{pr['number']}/{s['job']}/{s['build_id']}/"
        )
        job_dir = os.path.join(download_dir, s["short_name"])
        os.makedirs(job_dir, exist_ok=True)
        logger.info("Downloading %s → %s", s["short_name"], job_dir)

        dl_result = subprocess.run(
            ["gsutil", "-q", "-m", "cp", "-r", gcs_path, job_dir],
            capture_output=True, text=True,
        )
        results.append({
            "job": s["short_name"],
            "status": s["status"],
            "path": job_dir,
            "success": dl_result.returncode == 0,
            "error": dl_result.stderr.strip() if dl_result.returncode != 0 else None,
        })

    all_ok = all(r["success"] for r in results)
    output = {
        "action": "download",
        "status": "completed" if all_ok else "partial",
        "download_dir": download_dir,
        "jobs": results,
    }

    if args.json_output:
        print(json.dumps(output))
    else:
        print(f"Artifacts downloaded to: {download_dir}/")
        print()
        for r in results:
            marker = "OK" if r["success"] else "FAIL"
            print(f"  [{marker}] {r['job']} → {r['path']}")
            if r["error"]:
                print(f"       Error: {r['error']}")
        print()
        print(f"Run 'upload {v['version']}' to compress and upload to S3.")


def cmd_upload(args):
    """Compress artifacts into tar.gz and upload to S3."""
    v = prow.parse_version(args.version)

    if not shutil.which("aws"):
        print(json.dumps({
            "action": "upload",
            "status": "error",
            "message": "aws CLI not found. Install and configure AWS CLI.",
        }))
        sys.exit(1)

    download_dir = os.path.join(
        git_ops._get_edge_tooling_root(),
        "_output",
        f"release-testing-{v['version']}",
    )

    if not os.path.isdir(download_dir) or not os.listdir(download_dir):
        print(json.dumps({
            "action": "upload",
            "status": "error",
            "message": (
                f"No artifacts found at {download_dir}. "
                "Run 'download' first."
            ),
        }))
        sys.exit(1)

    tar_name = f"{v['version']}_prow_ci_release_testing_logs.tar.gz"
    tar_path = os.path.join(
        git_ops._get_edge_tooling_root(), "_output", tar_name,
    )
    s3_dest = f"{prow.S3_BUCKET}/{v['version']}/{tar_name}"

    if not args.execute:
        print(json.dumps({
            "action": "upload",
            "status": "plan",
            "source_dir": download_dir,
            "tar_path": tar_path,
            "s3_destination": s3_dest,
            "actions": [
                f"Compress {download_dir}/ → {tar_name}",
                f"Upload {tar_name} → {s3_dest}",
            ],
        }))
        return

    logger.info("Compressing %s → %s...", download_dir, tar_path)
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(download_dir, arcname=f"release-testing-{v['version']}")

    tar_size_mb = os.path.getsize(tar_path) / (1024 * 1024)
    logger.info("Created %s (%.1f MB)", tar_name, tar_size_mb)

    logger.info("Uploading to %s...", s3_dest)
    upload_result = subprocess.run(
        ["aws", "s3", "cp", tar_path, s3_dest],
        capture_output=True, text=True,
    )
    if upload_result.returncode != 0:
        print(json.dumps({
            "action": "upload",
            "status": "error",
            "message": f"aws s3 cp failed: {upload_result.stderr.strip()}",
        }))
        sys.exit(1)

    public_url = s3_dest.replace(
        "s3://release-testing-results",
        "https://release-testing-results.s3.us-west-2.amazonaws.com",
    )

    print(json.dumps({
        "action": "upload",
        "status": "uploaded",
        "tar_path": tar_path,
        "s3_destination": s3_dest,
        "public_url": public_url,
        "size_mb": round(tar_size_mb, 1),
        "message": f"Uploaded {tar_name} ({tar_size_mb:.1f} MB) to {public_url}",
    }))


def main():
    parser = argparse.ArgumentParser(
        description="MicroShift Prow CI release testing",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    for name, handler, mutating, help_text in [
        ("preflight", cmd_preflight, False, "Run pre-flight checks"),
        ("create-pr", cmd_create_pr, True, "Create draft PR with empty commit"),
        ("trigger", cmd_trigger, True, "Trigger CI jobs via /test comment"),
        ("status", cmd_status, False, "Check CI job statuses"),
        ("scenarios", cmd_scenarios, False, "Validate scenarios and versions in completed jobs"),
        ("download", cmd_download, True, "Download artifacts from GCS"),
        ("upload", cmd_upload, True, "Compress and upload artifacts to S3"),
        ("complete", cmd_complete, True, "Post comment, close PR, delete branch"),
    ]:
        sp = subparsers.add_parser(name, help=help_text)
        sp.add_argument("version", help="MicroShift version (X.Y.Z, X.Y.Z-rc.N, X.Y.Z-ec.N)")
        sp.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
        if mutating:
            sp.add_argument("--execute", action="store_true", help="Execute (default: dry-run)")
        sp.set_defaults(func=handler)

    args = parser.parse_args()

    try:
        args.func(args)
    except (ValueError, RuntimeError) as e:
        print(json.dumps({"action": "error", "message": str(e)}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"action": "error", "message": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
