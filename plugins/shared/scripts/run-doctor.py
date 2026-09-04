#!/usr/bin/env python3
"""Deterministic CI doctor pipeline.

Replaces LLM-orchestrated agent fan-out with a Python script that runs
the entire doctor pipeline: prepare -> graphs -> analyze -> bugs -> finalize.

Component is auto-detected from the symlink path (e.g.
plugins/microshift-ci/scripts/run-doctor.py -> component "microshift").
"""

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger("doctor")

_active_children = set()
_children_lock = threading.RLock()


def _register_child(proc):
    with _children_lock:
        _active_children.add(proc)


def _unregister_child(proc):
    with _children_lock:
        _active_children.discard(proc)


def _kill_all_children(signum, frame):
    with _children_lock:
        for proc in _active_children:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
    sys.exit(128 + signum)


COMPONENT_MAP = {
    "microshift-ci": "microshift",
    "lvms-ci": "lvm-operator",
}

ALL_STAGES_BY_COMPONENT = {
    "microshift": ["prepare", "graphs", "analyze", "bugs", "finalize"],
    "lvm-operator": ["prepare", "analyze", "finalize"],
}

STAGE_LIMITS = {
    "analyze": {"max_turns": 100, "timeout": 2700},
    "bugs": {"max_turns": 50, "timeout": 900},
}

DOCTOR_SH_TIMEOUT = {
    "prepare": 1800,
    "graphs": 600,
    "finalize": 300,
}


def detect_component():
    """Auto-detect component from the invocation path of sys.argv[0].

    Uses the unresolved path (before symlink resolution) so that
    plugins/microshift-ci/scripts/run-doctor.py -> component "microshift".
    """
    invoked = Path(sys.argv[0])
    for part in invoked.parts:
        if part in COMPONENT_MAP:
            return COMPONENT_MAP[part]
    abs_invoked = invoked.absolute()
    for part in abs_invoked.parts:
        if part in COMPONENT_MAP:
            return COMPONENT_MAP[part]
    return None


def detect_plugin_dir():
    """Return the plugin directory from the invocation path.

    Uses the unresolved path so symlinks work correctly:
    plugins/microshift-ci/scripts/run-doctor.py -> plugins/microshift-ci
    """
    invoked = Path(sys.argv[0]).absolute()
    for i, part in enumerate(invoked.parts):
        if part in COMPONENT_MAP:
            return str(Path(*invoked.parts[:i + 1]))
    return None


def strip_frontmatter(text):
    """Remove YAML frontmatter from agent markdown files."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def parse_args():
    parser = argparse.ArgumentParser(
        description="Deterministic CI doctor pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Component is auto-detected from the symlink path. Use --component
            to override. Stages default to all stages for the component.

            Available stages:
              microshift:    prepare, graphs, analyze, bugs, finalize
              lvm-operator:  prepare, analyze, finalize

            Examples:
              python3 plugins/microshift-ci/scripts/run-doctor.py \\
                  --releases 4.19,4.20,main --workdir /tmp/workdir
              python3 plugins/lvms-ci/scripts/run-doctor.py \\
                  --releases main --workdir /tmp/workdir --stages analyze,finalize
        """),
    )
    parser.add_argument("--releases", required=True,
                        help="Comma-separated release versions (e.g. 4.19,4.20,main)")
    parser.add_argument("--workdir", required=True,
                        help="Working directory for artifacts and logs")
    parser.add_argument("--model", default="claude-opus-4-6",
                        help="Claude model to use (default: claude-opus-4-6)")
    parser.add_argument("--parallel", type=int, default=64,
                        help="Max parallel claude -p sessions (default: 64)")
    parser.add_argument("--stages",
                        help="Comma-separated stages to run (default: all for component)")
    parser.add_argument("--component",
                        choices=["microshift", "lvm-operator"],
                        help="Override auto-detected component")
    parser.add_argument("--pull-requests", action="store_true",
                        help="Include pull request analysis")
    parser.add_argument("--repo",
                        help="GitHub org/repo for source checkout (e.g. openshift/microshift)")
    return parser.parse_args()


class DoctorPipeline:
    def __init__(self, args):
        self.args = args
        self.component = args.component or detect_component()
        if not self.component:
            log.error("Could not detect component from path. Use --component.")
            sys.exit(1)

        self.plugin_dir = detect_plugin_dir()
        if not self.plugin_dir:
            log.error("Could not detect plugin directory.")
            sys.exit(1)

        self.workdir = Path(args.workdir)
        self.logs_dir = self.workdir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "jobs").mkdir(parents=True, exist_ok=True)

        self.diagnostics_file = self.workdir / "diagnostics.txt"
        self.diagnostics_file.write_text("")

        all_stages = ALL_STAGES_BY_COMPONENT.get(self.component, [])
        if args.stages:
            requested = [s.strip() for s in args.stages.split(",")]
            invalid = [s for s in requested if s not in all_stages]
            if invalid:
                log.error("Invalid stages for %s: %s. Valid: %s",
                          self.component, invalid, all_stages)
                sys.exit(1)
            self.stages = requested
        else:
            self.stages = list(all_stages)

        self.releases = [r.strip() for r in args.releases.split(",")]
        self.model = args.model
        self.max_parallel = args.parallel

        self.agent_prompt_path = Path(self.plugin_dir) / "agents" / "prow-job-analyzer.md"
        if "analyze" in self.stages and not self.agent_prompt_path.is_file():
            log.error("Agent prompt not found: %s", self.agent_prompt_path)
            sys.exit(1)
        self._agent_system_prompt = None

        self.prepare_summary = None
        self.analyze_costs = {}

    @property
    def agent_system_prompt(self):
        if self._agent_system_prompt is None:
            text = self.agent_prompt_path.read_text()
            self._agent_system_prompt = strip_frontmatter(text)
        return self._agent_system_prompt

    def message(self, msg):
        """Append a diagnostic message to diagnostics.txt and log it."""
        log.warning(msg)
        with open(self.diagnostics_file, "a") as f:
            f.write(msg + "\n")

    def run_doctor_sh(self, subcommand, extra_args, log_name):
        """Run a doctor-helper.sh subcommand, streaming output live and to a log file."""
        doctor_sh = Path(self.plugin_dir) / "scripts" / "doctor-helper.sh"
        cmd = [
            "bash", str(doctor_sh), subcommand,
            "--component", self.component,
            "--workdir", str(self.workdir),
        ] + extra_args

        log_path = self.logs_dir / log_name
        timeout = DOCTOR_SH_TIMEOUT.get(subcommand, 600)
        output_lines = []

        log.info("Running: doctor-helper.sh %s", subcommand)
        try:
            with open(log_path, "w") as log_f:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                _register_child(proc)
                timed_out = False
                def _kill():
                    nonlocal timed_out
                    timed_out = True
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except OSError:
                        pass
                timer = threading.Timer(timeout, _kill)
                timer.start()
                try:
                    for line in proc.stdout:
                        log_f.write(line)
                        log.info("[doctor-helper.sh] %s", line.rstrip())
                        output_lines.append(line)
                    proc.wait()
                finally:
                    timer.cancel()
                    if proc.returncode == 0:
                        timed_out = False
                    _unregister_child(proc)

            stdout = "".join(output_lines)
            if timed_out:
                self.message(f"ERROR: doctor-helper.sh {subcommand} timed out after {timeout}s")
                return False, stdout
            if proc.returncode != 0:
                self.message(f"ERROR: doctor-helper.sh {subcommand} exited with code {proc.returncode}")
                return False, stdout
            return True, stdout
        except OSError as e:
            self.message(f"ERROR: doctor-helper.sh {subcommand} failed to start: {e}")
            return False, ""

    def run_claude_session(self, prompt, system_prompt, log_path,
                           max_turns=30, timeout=600,
                           allowed_tools=None, add_dirs=None):
        """Run a claude -p session, writing stream-json to log_path.

        Returns (success, final_text) where final_text is the last assistant
        message extracted from the stream-json log.
        """
        success, final_text = _run_claude_session(
            prompt=prompt,
            system_prompt=system_prompt,
            plugin_dir=self.plugin_dir,
            model=self.model,
            log_path=log_path,
            max_turns=max_turns,
            timeout=timeout,
            allowed_tools=allowed_tools,
            add_dirs=add_dirs,
        )
        if success is None:
            self.message(f"ERROR: claude -p timed out after {timeout}s: {log_path.name}")
            return False, None
        return success, final_text

    def extract_cost(self, log_path):
        """Extract cost_usd and duration_ms from a stream-json result event."""
        try:
            with open(log_path, errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict) and record.get("type") == "result":
                        return {
                            "cost_usd": record.get("total_cost_usd", 0),
                            "duration_ms": record.get("duration_ms", 0),
                        }
        except OSError:
            pass
        return {"cost_usd": 0, "duration_ms": 0}

    def _write_job_diagnostics(self, results):
        """Write per-job cost and stop hook stats to diagnostics.txt."""
        lines = ["Job Diagnostics:"]
        max_turns_limit = STAGE_LIMITS.get("analyze", {}).get("max_turns", 0)
        for label in sorted(results):
            r = results[label]
            stats = r.get("stats", {})
            cost = stats.get("cost_usd", 0)
            hooks = stats.get("stop_hook_count", 0)
            num_turns = stats.get("num_turns", 0)
            perm_denials = stats.get("permission_denials", 0)
            status = "OK" if r["success"] else "FAILED"
            timed_out = any("Timed out" in e for e in r.get("validation_errors", []))
            hit_max_turns = max_turns_limit > 0 and num_turns >= max_turns_limit

            parts = [f"  {label}: {status}, ${cost:.2f}, {num_turns} turns"]
            if timed_out:
                parts.append("TIMED OUT")
            if hit_max_turns:
                parts.append(f"HIT MAX TURNS ({num_turns}/{max_turns_limit})")
            if perm_denials > 0:
                parts.append(f"{perm_denials} PERMISSION DENIALS")
            if stats.get("context_exhausted"):
                parts.append("CONTEXT EXHAUSTED")
            if not r["success"] and not timed_out and not hit_max_turns and hooks == 0:
                parts.append("stop hook did not run")
            elif hooks > 1:
                first_hook = stats.get("first_hook_at_turn", 0)
                wasted = num_turns - first_hook if first_hook > 0 else 0
                if wasted > 0:
                    parts.append(f"stop hook fired {hooks}x ({wasted} turns wasted on retries)")
                else:
                    parts.append(f"stop hook fired {hooks}x")
            lines.append(", ".join(parts))

        with open(self.diagnostics_file, "a") as f:
            f.write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    def prepare(self):
        log.info("=== Stage: prepare ===")
        extra = [",".join(self.releases)]
        if self.args.pull_requests:
            extra.append("--pull-requests")
        if self.args.repo:
            extra.extend(["--repo", self.args.repo])

        ok, stdout = self.run_doctor_sh("prepare", extra, "prepare.log")
        if not ok:
            return False

        summary_path = self.workdir / "prepare-summary.json"

        # doctor-helper.sh prints a JSON summary as its last output (may be multi-line).
        # Find the last top-level '{' (at start of line) to skip nested braces.
        # Use raw_decode to tolerate trailing text after the JSON object.
        decoder = json.JSONDecoder()
        pos = len(stdout)
        while pos > 0:
            pos = stdout.rfind("{", 0, pos)
            if pos == -1:
                break
            if pos == 0 or stdout[pos - 1] == "\n":
                try:
                    summary, _ = decoder.raw_decode(stdout, pos)
                    with open(summary_path, "w") as f:
                        json.dump(summary, f, indent=2)
                    self.prepare_summary = summary
                    return True
                except json.JSONDecodeError:
                    pass

        if summary_path.exists():
            return self._load_prepare_summary(summary_path)

        self.message("ERROR: prepare did not produce a JSON summary")
        return False

    def _load_prepare_summary(self, path):
        """Load prepare-summary.json with error handling."""
        try:
            self.prepare_summary = json.loads(path.read_text())
            return True
        except (json.JSONDecodeError, OSError) as e:
            self.message(f"ERROR: Could not parse {path}: {e}")
            return False

    def graphs(self):
        log.info("=== Stage: graphs ===")
        ok, _ = self.run_doctor_sh("graphs", [], "graphs.log")
        return ok

    def analyze(self):
        log.info("=== Stage: analyze ===")

        if not self.prepare_summary:
            summary_path = self.workdir / "prepare-summary.json"
            if summary_path.exists():
                if not self._load_prepare_summary(summary_path):
                    return False
            else:
                self.message("ERROR: analyze requires prepare-summary.json")
                return False

        jobs = self._collect_jobs_to_analyze()
        if not jobs:
            log.info("No jobs to analyze")
            return True

        log.info("Analyzing %d jobs (max %d parallel)...", len(jobs), self.max_parallel)

        results = {}
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            futures = {}
            for job_info in jobs:
                future = pool.submit(
                    _analyze_single_job,
                    job_info=job_info,
                    plugin_dir=self.plugin_dir,
                    model=self.model,
                    agent_system_prompt=self.agent_system_prompt,
                    logs_dir=str(self.logs_dir),
                    workdir=str(self.workdir),
                )
                futures[future] = job_info

            for future in as_completed(futures):
                job_info = futures[future]
                label = job_info["label"]
                try:
                    success, output_path, validation_errors, stats = future.result()
                    results[label] = {
                        "success": success,
                        "output_path": output_path,
                        "validation_errors": validation_errors,
                        "stats": stats,
                    }
                    self.analyze_costs[job_info["log_name"]] = {
                        "cost_usd": stats.get("cost_usd", 0),
                        "duration_ms": stats.get("duration_ms", 0),
                    }
                    if success:
                        log.info("[OK] %s", label)
                    else:
                        log.warning("[FAILED] %s", label)
                    if validation_errors:
                        self.message(
                            f"WARNING: Post-hoc validation failed for {label}:\n"
                            + "\n".join(f"  - {e}" for e in validation_errors)
                        )
                except Exception as exc:
                    self.message(f"ERROR: {label} raised exception: {exc}")
                    results[label] = {
                        "success": False, "output_path": None, "validation_errors": [],
                        "stats": {"cost_usd": 0, "stop_hook_count": 0},
                    }

        succeeded = sum(1 for r in results.values() if r["success"])
        log.info("Analyze complete: %d/%d succeeded", succeeded, len(results))
        self._write_job_diagnostics(results)
        return succeeded > 0 or not results

    def _collect_jobs_to_analyze(self):
        """Build a list of job dicts to analyze from prepare-summary.json."""
        jobs = []
        summary = self.prepare_summary

        releases_info = summary.get("releases", [])
        for info in releases_info:
            release = info.get("release", "unknown")
            if info.get("error"):
                continue
            jobs_file = info.get("jobs_file")
            if not jobs_file:
                continue
            try:
                job_list = json.loads(Path(jobs_file).read_text())
            except (OSError, json.JSONDecodeError) as e:
                self.message(f"WARNING: Could not read {jobs_file}: {e}")
                continue
            for i, job in enumerate(job_list):
                if not isinstance(job, dict):
                    continue
                build_id = job.get("build_id", f"unknown-{i}")
                log_name = f"prow-job-analyzer-{release}-{build_id}.log"
                output_name = f"release-{release}-job-{i}-{build_id}.json"
                jobs.append({
                    "label": f"{release}/{build_id}",
                    "release": release,
                    "artifacts_dir": job.get("artifacts_dir", ""),
                    "job_url": job.get("url", ""),
                    "job_name": job.get("job", ""),
                    "build_id": build_id,
                    "log_name": log_name,
                    "output_name": output_name,
                    "graphs_dir": self._graphs_dir(build_id),
                    "source_dir": self._source_dir(release),
                    "is_pr": False,
                })

        pr_info = summary.get("prs", {})
        pr_jobs_file = pr_info.get("jobs_file")
        if pr_jobs_file:
            try:
                pr_job_list = json.loads(Path(pr_jobs_file).read_text())
            except (OSError, json.JSONDecodeError) as e:
                self.message(f"WARNING: Could not read {pr_jobs_file}: {e}")
                pr_job_list = []
            for i, job in enumerate(pr_job_list):
                if not isinstance(job, dict):
                    continue
                status = job.get("status", "").upper()
                if status != "FAILURE":
                    continue
                build_id = job.get("build_id", f"unknown-{i}")
                pr_number = job.get("pr_number", "unknown")
                job_name = job.get("job", "")
                log_name = f"prow-job-analyzer-pr{pr_number}-{build_id}.log"
                output_name = f"prs-job-{i}-pr{pr_number}-{build_id}.json"
                jobs.append({
                    "label": f"pr{pr_number}/{build_id}",
                    "release": "prs",
                    "artifacts_dir": job.get("artifacts_dir", ""),
                    "job_url": job.get("url", ""),
                    "job_name": job_name,
                    "build_id": build_id,
                    "log_name": log_name,
                    "output_name": output_name,
                    "graphs_dir": self._graphs_dir(build_id),
                    "source_dir": self._source_dir("main"),
                    "is_pr": True,
                })

        return jobs

    def _graphs_dir(self, build_id):
        d = self.workdir / "graphs" / build_id
        return str(d) if d.is_dir() else None

    def _source_dir(self, release):
        repo_name = self.args.repo.split("/")[-1] if self.args.repo else self.component
        if release == "main":
            d = self.workdir / "src" / repo_name
        else:
            d = self.workdir / "src" / f"{repo_name}-release-{release}"
        if not d.is_dir():
            d = self.workdir / "src" / repo_name
        return str(d) if d.is_dir() else None

    def bugs(self):
        log.info("=== Stage: bugs ===")
        if self.component != "microshift":
            log.info("Bug correlation is microshift-only, skipping")
            return True

        sources = list(self.releases)
        prs_status_path = self.workdir / "jobs" / "prs-status.json"
        if prs_status_path.exists():
            try:
                prs_status = json.loads(prs_status_path.read_text())
                for pr in prs_status:
                    m = re.search(r"rebase-release-([\d.]+)", pr.get("title", ""))
                    if m:
                        rebase_src = f"rebase-release-{m.group(1)}"
                        if rebase_src not in sources:
                            sources.append(rebase_src)
            except (json.JSONDecodeError, OSError):
                pass

        sources_str = ",".join(sources)
        prompt = f"/microshift-ci:create-bugs {sources_str}"
        log_path = self.logs_dir / "create-bugs.log"
        limits = STAGE_LIMITS["bugs"]

        ok, _ = self.run_claude_session(
            prompt=prompt,
            system_prompt="",
            log_path=log_path,
            max_turns=limits["max_turns"],
            timeout=limits["timeout"],
            allowed_tools=["Skill", "Bash", "Read", "Write", "Glob", "Grep",
                           "mcp__jira__jira_search", "mcp__jira__jira_get_issue"],
            add_dirs=[str(self.workdir)],
        )
        if not ok:
            self.message("WARNING: Bug correlation session failed (non-fatal)")
        return True

    def finalize(self):
        log.info("=== Stage: finalize ===")
        extra = [",".join(self.releases)]

        closed_bugs_path = self.workdir / "close-stale-bugs" / "closed-bugs.json"
        if closed_bugs_path.exists():
            try:
                data = json.loads(closed_bugs_path.read_text())
                closed = data.get("closed", [])
                if closed:
                    extra.extend(["--ignore", ",".join(closed)])
                    log.info("Excluding %d closed bugs from report", len(closed))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Could not read %s: %s", closed_bugs_path, e)

        ok, _ = self.run_doctor_sh("finalize", extra, "finalize.log")
        return ok

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------

    def compute_costs(self):
        """Parse all stream-json logs and produce a cost summary."""
        costs = {"stages": {}, "total_cost_usd": 0, "total_duration_ms": 0}

        for log_file in sorted(self.logs_dir.glob("*.log")):
            cached = self.analyze_costs.get(log_file.name)
            if cached:
                cost_info = cached
            else:
                cost_info = self.extract_cost(log_file)
            if cost_info["cost_usd"] > 0:
                stage = "other"
                release = "all"
                name = log_file.stem
                if name.startswith("prow-job-analyzer"):
                    stage = "analyze"
                    # prow-job-analyzer-<release>-<build_id> or
                    # prow-job-analyzer-pr<N>-<suffix>
                    rest = name[len("prow-job-analyzer-"):]
                    if rest.startswith("pr"):
                        release = "PRs"
                    else:
                        release = rest.split("-")[0] if "-" in rest else rest
                elif name.startswith("create-bugs"):
                    stage = "bugs"

                if stage not in costs["stages"]:
                    costs["stages"][stage] = {
                        "releases": {},
                        "total_cost_usd": 0, "total_duration_ms": 0,
                    }
                stage_data = costs["stages"][stage]
                if release not in stage_data["releases"]:
                    stage_data["releases"][release] = {
                        "jobs": [], "cost_usd": 0, "duration_ms": 0,
                    }
                rel_data = stage_data["releases"][release]
                rel_data["jobs"].append({
                    "name": name,
                    "cost_usd": cost_info["cost_usd"],
                    "duration_ms": cost_info["duration_ms"],
                })
                rel_data["cost_usd"] += cost_info["cost_usd"]
                rel_data["duration_ms"] += cost_info["duration_ms"]
                stage_data["total_cost_usd"] += cost_info["cost_usd"]
                stage_data["total_duration_ms"] += cost_info["duration_ms"]
                costs["total_cost_usd"] += cost_info["cost_usd"]
                costs["total_duration_ms"] += cost_info["duration_ms"]

        cost_path = self.workdir / "cost-summary.json"
        with open(cost_path, "w") as f:
            json.dump(costs, f, indent=2)

        self._print_cost_summary(costs)
        self._write_cost_diagnostics(costs)
        return costs

    @staticmethod
    def _format_cost_summary(costs):
        lines = ["Cost Summary:"]
        for stage, data in costs["stages"].items():
            lines.append(f"  {stage.capitalize()}:")
            for release, rel_data in sorted(data["releases"].items()):
                n = len(rel_data["jobs"])
                cost = rel_data["cost_usd"]
                avg = cost / n if n else 0
                lines.append(f"    {release}: {n} jobs, ${cost:.2f} (avg ${avg:.3f}/job)")
            total_jobs = sum(len(r["jobs"]) for r in data["releases"].values())
            lines.append(f"    Total: {total_jobs} jobs, ${data['total_cost_usd']:.2f}")
        lines.append(f"  Grand Total: ${costs['total_cost_usd']:.2f}")
        return lines

    def _print_cost_summary(self, costs):
        for line in self._format_cost_summary(costs):
            log.info(line)

    def _write_cost_diagnostics(self, costs):
        lines = self._format_cost_summary(costs)
        with open(self.diagnostics_file, "a") as f:
            f.write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self):
        stage_funcs = {
            "prepare": self.prepare,
            "graphs": self.graphs,
            "analyze": self.analyze,
            "bugs": self.bugs,
            "finalize": self.finalize,
        }

        had_errors = False
        for stage in self.stages:
            func = stage_funcs.get(stage)
            if not func:
                self.message(f"ERROR: Unknown stage: {stage}")
                return 1

            # Compute costs before finalize so diagnostics.txt is complete
            # when create-report.py reads it for the HTML report.
            if stage == "finalize":
                self.compute_costs()

            ok = func()
            if not ok:
                had_errors = True
                if stage == "prepare":
                    self.message(f"FATAL: Stage {stage} failed, aborting")
                    return 1
                self.message(f"WARNING: Stage {stage} had errors, continuing")

        if self.diagnostics_file.exists():
            content = self.diagnostics_file.read_text().strip()
            if content:
                log.info("Diagnostics written to %s", self.diagnostics_file)

        return 1 if had_errors else 0


# ----------------------------------------------------------------------
# Module-level helpers for ThreadPoolExecutor workers
# ----------------------------------------------------------------------

_validate_module = None


def _load_validate_module():
    """Import validate-rca-output.py via importlib (filename contains dashes)."""
    global _validate_module
    if _validate_module is None:
        import importlib.util
        script = Path(__file__).resolve().parent / "validate-rca-output.py"
        spec = importlib.util.spec_from_file_location("validate_rca_output", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _validate_module = mod
    return _validate_module


def _run_validation(text):
    return _load_validate_module().validate_message(text)


def _extract_result_text_standalone(log_path):
    return _load_validate_module()._extract_last_assistant_message_from_transcript(log_path)


def _extract_job_stats(log_path):
    """Extract cost, stop hook count, turn count, and permission denials from a stream-json log."""
    cost_usd = 0
    duration_ms = 0
    stop_hook_count = 0
    num_turns = 0
    permission_denials = 0
    parent_user_msgs = 0
    first_hook_at_turn = 0
    context_exhausted = False
    try:
        with open(log_path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "result":
                    cost_usd = record.get("total_cost_usd", 0)
                    duration_ms = record.get("duration_ms", 0)
                    num_turns = record.get("num_turns", 0)
                    denials = record.get("permission_denials")
                    if isinstance(denials, list):
                        permission_denials = len(denials)
                elif record.get("type") == "assistant" and not record.get("parent_tool_use_id"):
                    msg = record.get("message", {})
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                if block.get("text", "").strip() == "Prompt is too long":
                                    context_exhausted = True
                elif record.get("type") == "user" and not record.get("parent_tool_use_id"):
                    is_hook = False
                    if record.get("isSynthetic"):
                        msg = record.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and "Stop hook feedback:" in block.get("text", ""):
                                        is_hook = True
                                        break
                    if is_hook:
                        stop_hook_count += 1
                        if stop_hook_count == 1:
                            first_hook_at_turn = parent_user_msgs + 1
                    parent_user_msgs += 1
    except OSError:
        pass
    return {"cost_usd": cost_usd, "duration_ms": duration_ms,
            "stop_hook_count": stop_hook_count,
            "num_turns": num_turns, "permission_denials": permission_denials,
            "first_hook_at_turn": first_hook_at_turn,
            "context_exhausted": context_exhausted}


def _run_claude_session(prompt, system_prompt, plugin_dir, model, log_path,
                        max_turns=30, timeout=600, env=None,
                        allowed_tools=None, add_dirs=None):
    """Run a claude -p session, writing stream-json to log_path.

    Returns (success, final_text). Returns (None, None) on timeout.
    """
    cmd = [
        "claude", "-p", prompt,
        "--append-system-prompt", system_prompt,
        "--plugin-dir", plugin_dir,
        "--model", model,
        "--max-turns", str(max_turns),
        "--output-format", "stream-json",
        "--verbose",
    ]
    if allowed_tools:
        cmd.extend(["--allowed-tools", ",".join(allowed_tools)])
    if add_dirs:
        for d in add_dirs:
            cmd.extend(["--add-dir", d])

    try:
        with open(log_path, "w") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
            _register_child(proc)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                proc.wait()
                return None, None
            finally:
                _unregister_child(proc)
        final_text = _extract_result_text_standalone(log_path)
        return proc.returncode == 0, final_text
    except OSError as e:
        log.error("Failed to start claude: %s", e)
        return False, None


def _analyze_single_job(job_info, plugin_dir, model, agent_system_prompt,
                        logs_dir, workdir):
    """Analyze a single prow job via claude -p. Called in a subprocess."""
    prompt_parts = [
        "Analyze this prow job:",
        f"artifacts_dir: {job_info['artifacts_dir']}",
        f"job_url: {job_info['job_url']}",
        f"job_name: {job_info['job_name']}",
    ]
    if job_info.get("graphs_dir"):
        prompt_parts.append(f"graphs_dir: {job_info['graphs_dir']}")
    if job_info.get("source_dir"):
        prompt_parts.append(f"source_dir: {job_info['source_dir']}")

    prompt = "\n".join(prompt_parts)
    log_path = Path(logs_dir) / job_info["log_name"]
    output_path = Path(workdir) / "jobs" / job_info["output_name"]
    limits = STAGE_LIMITS["analyze"]

    env = os.environ.copy()
    env["CI_DOCTOR_RCA_SESSION"] = Path(job_info["output_name"]).stem
    env["CI_DOCTOR_HOOK_LOG"] = str(Path(workdir) / "hook-debug.jsonl")

    add_dirs = [d for d in [
        job_info.get("artifacts_dir"),
        job_info.get("graphs_dir"),
        job_info.get("source_dir"),
    ] if d]

    success, final_text = _run_claude_session(
        prompt=prompt,
        system_prompt=agent_system_prompt,
        plugin_dir=plugin_dir,
        model=model,
        log_path=log_path,
        max_turns=limits["max_turns"],
        timeout=limits["timeout"],
        env=env,
        allowed_tools=["Bash", "Read", "Glob", "Grep"],
        add_dirs=add_dirs,
    )

    timed_out = success is None
    if timed_out:
        final_text = _extract_result_text_standalone(log_path)

    validation_errors = []
    if timed_out:
        validation_errors.append(f"Timed out after {limits['timeout']}s")

    saved = False
    if final_text:
        validation_errors.extend(_run_validation(final_text))
        try:
            data = json.loads(final_text)
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2)
            saved = True
        except json.JSONDecodeError:
            validation_errors.append("Output is not valid JSON")
    else:
        validation_errors.append("No assistant text found in stream-json log")

    stats = _extract_job_stats(log_path)
    return saved, str(output_path) if saved else None, validation_errors, stats


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    signal.signal(signal.SIGTERM, _kill_all_children)
    signal.signal(signal.SIGINT, _kill_all_children)
    args = parse_args()
    pipeline = DoctorPipeline(args)
    sys.exit(pipeline.run())


if __name__ == "__main__":
    main()
