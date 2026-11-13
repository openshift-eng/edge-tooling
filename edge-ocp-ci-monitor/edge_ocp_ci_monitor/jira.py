"""Validate Chai's read-only Jira decisions and build reviewable bug drafts."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from payload_monitor.collectors.jira import JIRA_BASE
from payload_monitor.config import Config
from payload_monitor.models import SuggestedBug

EDGE_TOPOLOGIES = {"SNO", "TNA", "TNF"}
# Red Hat Jira Cloud's multi-version picker (see edge-scrum's Jira field reference).
TARGET_VERSION_FIELD = "customfield_10855"
AGENT_ISSUE_LABEL = "issue-for-agent"

JIRA_SCHEMA = {
    "search_status": "checked",
    "search_summary": "Jira queries, results and why an existing bug does or does not cover this failure",
    "existing_bugs": [],
    "new_bug": {
        "title": "concise evidence-based bug title",
        "description": "diagnosis, impact, reproduction/failing tests, evidence URLs and duplicate-search results",
        "target_versions": [{"name": "4.19.0", "id": "verified Jira project version ID"}],
    },
}


def validate_jira(value: object, target: dict) -> dict | None:
    """A missing decision never recommends a bug; malformed decisions fail closed."""
    if value is None:
        return None
    if (not isinstance(value, dict) or not isinstance(value.get("search_status"), str)
            or value["search_status"] not in {"checked", "unavailable", "not_checked"}):
        raise ValueError("Chai returned an invalid Jira search_status.")
    summary = value.get("search_summary")
    existing = value.get("existing_bugs")
    if not isinstance(summary, str) or not isinstance(existing, list) or any(
        not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key) for key in existing
    ):
        raise ValueError("Chai returned invalid Jira search evidence or existing bug keys.")
    if value["search_status"] == "checked" and not summary.strip():
        raise ValueError("Chai must describe the Jira duplicate search it checked.")
    draft = value.get("new_bug")
    if draft is not None:
        if value["search_status"] != "checked" or existing or target.get("known_existing_bugs"):
            raise ValueError("Chai recommended a new bug without ruling out existing bugs.")
        if target.get("topology") not in EDGE_TOPOLOGIES:
            raise ValueError("Chai recommended a new bug outside SNO, TNA and TNF.")
        if not isinstance(draft, dict) or any(
            not isinstance(draft.get(key), str) or not draft[key].strip() for key in ("title", "description")
        ) or len(draft["title"]) > 255:
            raise ValueError("Chai returned an incomplete Jira bug title or description.")
        versions = draft.get("target_versions")
        releases = set(target.get("versions", [target.get("version", "")]))
        if not isinstance(versions, list) or not versions:
            raise ValueError("Chai's bug draft must include target versions and their Jira IDs.")
        for version in versions:
            if (not isinstance(version, dict)
                    or not isinstance(version.get("name"), str)
                    or not re.fullmatch(r"\d+\.\d+\.(?:\d+|z)", version["name"])
                    or version["name"].rsplit(".", 1)[0] not in releases
                    or not isinstance(version.get("id"), str)
                    or not re.fullmatch(r"\d+", version["id"])):
                raise ValueError("Chai returned an invalid or unrelated Jira target version.")
        draft = {"title": draft["title"].strip(), "description": draft["description"].strip(),
                 "target_versions": versions}
    return {"search_status": value["search_status"], "search_summary": summary.strip(),
            "existing_bugs": list(dict.fromkeys(existing)), "new_bug": draft}


def suggested_bugs(targets: dict, data: dict) -> list[SuggestedBug]:
    """Rebuild only explicit recommendations, including cross-batch duplicate checks."""
    decisions = []
    covered_jobs = set()
    for category, group, key in (("blocking_jobs", "by_prow_url", "prow_url"),
                                 ("sippy_jobs", "by_sippy_job", "analysis_key")):
        for target in targets[category]:
            name = target.get("job_name", target.get("name"))
            entry = data.get(group, {}).get(target[key], {})
            if target.get("known_existing_bugs"):
                covered_jobs.add(name)
            try:
                decision = validate_jira(entry.get("jira"), target)
            except ValueError:
                # Imported/older data cannot bypass the live response validator.
                continue
            if decision and decision["existing_bugs"]:
                covered_jobs.add(name)
            if decision and decision["new_bug"] and entry.get("failure_type") != "Analysis error":
                decisions.append((name, target, decision))

    drafts = {}
    config = Config()
    for name, target, decision in decisions:
        if name in covered_jobs:
            continue
        draft = decision["new_bug"]
        topology = target["topology"]
        signature = (topology, " ".join(draft["title"].casefold().split()))
        if signature not in drafts:
            drafts[signature] = (SuggestedBug(
                title=draft["title"], description=draft["description"], full_description=draft["description"],
                job_name=name, topology=topology, prow_url=target.get("prow_url", target.get("triage_url", "")),
            ), {})
        bug, versions = drafts[signature]
        if draft["description"] not in bug.full_description:
            bug.full_description += "\n\nAdditional Chai investigation:\n" + draft["description"]
            bug.description = bug.full_description
        for version in draft["target_versions"]:
            versions[version["name"]] = version["id"]
        bug.versions = sorted(versions)
        params = [("project.key", config.jira_project), ("issuetype.name", "Bug"),
                  ("summary", bug.title), ("description", bug.full_description),
                  ("labels", AGENT_ISSUE_LABEL),
                  ("component", config.jira_component_for(topology))]
        params.extend((TARGET_VERSION_FIELD, versions[version]) for version in bug.versions)
        bug.create_url = f"{JIRA_BASE}/secure/CreateIssue!default.jspa?{urlencode(params)}"
    return [bug for bug, _ in drafts.values()]
