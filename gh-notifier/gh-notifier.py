#!/usr/bin/env python3
"""List open PRs across repos, filter by authors / drafts / labels, flag stale PRs, notify Slack.

GitHub logins to match on PR authors come from expanding Kubernetes-style **OWNERS** (approvers +
reviewers) using **OWNERS_ALIASES** in the same repository checkout (defaults: repo root adjacent to
``gh-notifier/``). Override paths with OWNERS_FILE and OWNERS_ALIASES_FILE.

Writes a self-contained **HTML dashboard** (path via ``GH_NOTIFIER_HTML_OUTPUT``) with PR details and
Slack **mrkdwn** / webhook **JSON** copy buttons. Optional ``SLACK_WEBHOOK_URL`` still posts automatically.
If ``PROW_JOB_URL`` is set, the **verbose** Slack webhook message includes a Prow job link under the header (not the HTML copy).
By default (``VERBOSE_SLACK_MESSAGE`` unset, empty, or false) the webhook sends a **minimal** message: ``PROW_HTML_URL`` first, optional ``PROW_JOB_URL``. The HTML dashboard still includes full Slack copy helpers (verbose).

Designed to be initiated from openshift/release on a schedule with env vars driving repos, labels,
and notification.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# -----------------------------------------------------------------------------
# Environment (defaults are conservative)
# -----------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int_env(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _truthy_env(name: str) -> bool:
    """True only for explicit affirmative values; unset, empty, or ``false``/``0`` → False."""
    v = _env(name, "").lower()
    return v in ("1", "true", "t", "yes", "y", "on")


STALE_DAYS = _int_env("STALE_DAYS", 7)
GITHUB_TOKEN = _env("GITHUB_TOKEN")
SLACK_WEBHOOK_URL = _env("SLACK_WEBHOOK_URL", "")
# When set, only the Slack webhook payload (not HTML / full mrkdwn) gets a Prow job link at the top.
PROW_JOB_URL = _env("PROW_JOB_URL", "")
PROW_HTML_URL = _env("PROW_HTML_URL", "")
VERBOSE_SLACK_MESSAGE = _truthy_env("VERBOSE_SLACK_MESSAGE")


# Comma-separated org/repo pairs
_REPOS_RAW = _env("GITHUB_REPOS", "openshift-eng/edge-tooling")

# Semicolon or comma separated label tokens (GitHub compares labels names case-sensitively but we normalize)
_EXCLUDE_DEFAULT = "do-not-merge/hold,do-not-merge/work-in-progress,wip,work in progress"


def _label_set(raw: str) -> set[str]:
    if not raw:
        return set()
    toks = re.split(r"[;,]", raw)
    return {t.strip().lower() for t in toks if t.strip()}


_exclude_raw = os.environ.get("EXCLUDE_LABELS", "").strip()
EXCLUDE_LABELS = _label_set(_EXCLUDE_DEFAULT if not _exclude_raw else _exclude_raw)
REQUIRED_LABELS = _label_set(_env("REQUIRED_LABELS", ""))
FORBIDDEN_LABELS = _label_set(_env("FORBIDDEN_LABELS", ""))

MAX_PRS_IN_MESSAGE = 5
CHUNK_MAX_LEN = 2800


def _parse_repos(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for chunk in re.split(r"[,\s]+", raw):
        chunk = chunk.strip()
        if not chunk or "/" not in chunk:
            continue
        org, repo = chunk.split("/", 1)
        org, repo = org.strip(), repo.strip()
        if org and repo:
            out.append((org, repo))
    return out or [("openshift-eng", "edge-tooling")]


REPOS = _parse_repos(_REPOS_RAW)

_OWNERS_SECTIONS = frozenset({"approvers", "reviewers"})


def parse_owners_aliases(text: str) -> dict[str, list[str]]:
    """Parse OWNERS_ALIASES ``aliases:`` block into alias name -> GitHub logins."""
    aliases: dict[str, list[str]] = {}
    in_aliases = False
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "aliases:":
            in_aliases = True
            current_key = None
            continue
        if not in_aliases:
            continue
        # Another top-level YAML key ends the aliases document section we care about.
        if not line.startswith(" ") and stripped.endswith(":"):
            break
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            current_key = m.group(1)
            aliases.setdefault(current_key, [])
            continue
        m = re.match(r"^ +-\s+(\S+)", line)
        if m and current_key is not None:
            aliases[current_key].append(m.group(1).strip())
    return aliases


def parse_owners_raw_entries(text: str) -> list[str]:
    """Collect raw ``- name`` entries from approvers and reviewers sections only."""
    entries: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[a-zA-Z0-9_.-]+:\s*$", stripped) and not stripped.startswith("-"):
            key = stripped[:-1].strip()
            current = key if key in _OWNERS_SECTIONS else None
            continue
        if current and stripped.startswith("- "):
            val = stripped[2:].strip()
            if val:
                entries.append(val)
    return entries


def github_logins_from_owners(owners_text: str, aliases_text: str) -> set[str]:
    """Expand OWNERS approvers/reviewers using OWNERS_ALIASES; unknown names are treated as logins."""
    alias_map = parse_owners_aliases(aliases_text)
    logins: set[str] = set()
    for raw in parse_owners_raw_entries(owners_text):
        key = raw.strip()
        if not key:
            continue
        if key in alias_map:
            for u in alias_map[key]:
                u = u.strip()
                if u:
                    logins.add(u.lower())
        else:
            logins.add(key.lower())
    return logins


def load_github_logins_from_owners_files(owners_path: Path, aliases_path: Path) -> set[str]:
    if not owners_path.is_file():
        raise OSError(f"OWNERS file not found: {owners_path}")
    if not aliases_path.is_file():
        raise OSError(f"OWNERS_ALIASES file not found: {aliases_path}")
    return github_logins_from_owners(
        owners_path.read_text(encoding="utf-8"),
        aliases_path.read_text(encoding="utf-8"),
    )


# -----------------------------------------------------------------------------
# GitHub API (urllib, no extra deps)
# -----------------------------------------------------------------------------


def gh_request(path: str, query: dict[str, str] | None = None) -> object:
    if not GITHUB_TOKEN:
        sys.stderr.write("GITHUB_TOKEN is not set\n")
        sys.exit(1)
    url = "https://api.github.com" + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "User-Agent": "edge-tooling-pr-notifier",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.stderr.write(f"GitHub API error {e.code} {path}: {body}\n")
        raise


def iter_pulls(org: str, repo: str) -> Iterable[dict]:
    page = 1
    while True:
        data = gh_request(
            f"/repos/{urllib.parse.quote(org)}/{urllib.parse.quote(repo)}/pulls",
            {"state": "open", "per_page": "100", "page": str(page), "sort": "updated", "direction": "desc"},
        )
        if not isinstance(data, list) or not data:
            return
        yield from data
        if len(data) < 100:
            return
        page += 1


def parse_github_ts(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def pr_label_names_lower(pr: dict) -> set[str]:
    labels = pr.get("labels") or []
    return {(x.get("name") or "").lower() for x in labels if isinstance(x, dict)}


def forbidden_label_display_names(pr: dict, forbidden_lower: set[str]) -> list[str]:
    out: list[str] = []
    for lb in pr.get("labels") or []:
        if not isinstance(lb, dict):
            continue
        name = lb.get("name") or ""
        if name.lower() in forbidden_lower:
            out.append(name)
    return sorted(out, key=str.lower)


def days_since(older: datetime, newer: datetime) -> int:
    return int((newer - older).total_seconds() // 86400)


@dataclass
class AttentionPR:
    org: str
    repo: str
    number: int
    title: str
    html_url: str
    author: str
    base_branch: str
    age_days: int
    inactive_days: int
    missing_labels: list[str] = field(default_factory=list)
    forbidden_labels: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def repo_full(self) -> str:
        return f"{self.org}/{self.repo}"


def pr_matches_filters(pr: dict, author_allowlist: set[str]) -> bool:
    if pr.get("draft"):
        return False
    login = (pr.get("user") or {}).get("login") or ""
    if not author_allowlist or login.lower() not in author_allowlist:
        return False
    labels = pr_label_names_lower(pr)
    if labels & EXCLUDE_LABELS:
        return False
    return True


def pr_attention_row(pr: dict, org: str, repo: str) -> AttentionPR | None:
    """Return a row only when this PR needs attention (same rules as before)."""
    reasons: list[str] = []
    labels = pr_label_names_lower(pr)
    missing: list[str] = []
    if REQUIRED_LABELS and not REQUIRED_LABELS.issubset(labels):
        missing = sorted(REQUIRED_LABELS - labels)
        reasons.append(f"missing required label(s): {', '.join(missing)}")
    forbidden = forbidden_label_display_names(pr, FORBIDDEN_LABELS)
    if forbidden:
        reasons.append(f"has forbidden label(s): {', '.join(forbidden)}")
    updated = parse_github_ts(pr["updated_at"])
    created = parse_github_ts(pr["created_at"])
    now = datetime.now(timezone.utc)
    stale_cut = now - timedelta(days=STALE_DAYS)
    if updated <= stale_cut:
        reasons.append(f"no activity for >= {STALE_DAYS} days (updated_at)")
    if created <= stale_cut:
        reasons.append(f"open for >= {STALE_DAYS} days (created_at)")
    if not reasons:
        return None
    base_branch = str((pr.get("base") or {}).get("ref") or "")
    return AttentionPR(
        org=org,
        repo=repo,
        number=int(pr["number"]),
        title=str(pr.get("title") or ""),
        html_url=str(pr.get("html_url") or ""),
        author=str((pr.get("user") or {}).get("login") or ""),
        base_branch=base_branch,
        age_days=days_since(created, now),
        inactive_days=days_since(updated, now),
        missing_labels=missing,
        forbidden_labels=forbidden,
        reasons=reasons,
    )


# -----------------------------------------------------------------------------
# Slack Block Kit (aligned with internal/slack/slack.go)
# -----------------------------------------------------------------------------


def slack_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_label_list(labels: list[str]) -> str:
    parts = ["`" + lb.replace("`", "'") + "`" for lb in labels]
    return ", ".join(parts)


def _label_mode_line() -> str:
    if REQUIRED_LABELS:
        joined = ", ".join(sorted(REQUIRED_LABELS))
        return f"Label rules: *fixed list* · {joined}"
    if FORBIDDEN_LABELS:
        joined = ", ".join(sorted(FORBIDDEN_LABELS))
        return f"Label rules: *forbidden set* · {joined}"
    return "Label rules: *none* (no REQUIRED_LABELS / FORBIDDEN_LABELS)"


def _fallback_summary(open_count: int, attention: int, fetched_iso: str) -> str:
    return (
        f"PR dashboard · {open_count} open · {attention} need attention · updated {fetched_iso}"
    )


def _plain_header(text: str) -> dict[str, Any]:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _context_line(mrkdwn: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": mrkdwn}]}


def _section_mrkdwn(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _section_fields(left: str, right: str) -> dict[str, Any]:
    return {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": left},
            {"type": "mrkdwn", "text": right},
        ],
    }


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


def build_slack_payload_minimal() -> dict[str, Any]:
    """Short Slack webhook body: dashboard artifact first, optional Prow job second."""
    blocks: list[dict[str, Any]] = []
    if PROW_HTML_URL:
        blocks.append(_section_mrkdwn(f"Today's PR dashboard: <{PROW_HTML_URL}|Click for full PR list>"))
    if PROW_JOB_URL:
        if PROW_HTML_URL:
            blocks.append(_context_line(f"Prow: <{PROW_JOB_URL}|job logs>"))
        else:
            blocks.append(_section_mrkdwn(f"<{PROW_JOB_URL}|Open in Prow>"))
    if not blocks:
        blocks.append(
            _context_line("PR notifier completed (set `PROW_HTML_URL` and/or `PROW_JOB_URL` for links).")
        )
    preview = "PR dashboard"
    if PROW_HTML_URL:
        preview = "PR dashboard · HTML"
    elif PROW_JOB_URL:
        preview = "PR dashboard · Prow"
    return {"text": preview, "blocks": blocks}


def _chunk_section_blocks(blocks: list[dict[str, Any]], text: str) -> None:
    text = text.strip()
    if not text:
        return
    while text:
        chunk = text
        if len(chunk) > CHUNK_MAX_LEN:
            chunk = text[:CHUNK_MAX_LEN]
            cut = chunk.rfind("\n")
            if cut > CHUNK_MAX_LEN // 2:
                chunk = text[:cut]
        if not chunk:
            chunk = text[: min(len(text), CHUNK_MAX_LEN)]
        blocks.append(_section_mrkdwn(chunk))
        text = text[len(chunk) :].lstrip("\n")


def build_slack_payload(
    *,
    fetched_at: datetime,
    open_pr_count: int,
    attention: list[AttentionPR],
    fetch_errors: list[str],
    slack_pr_cap: int | None = MAX_PRS_IN_MESSAGE,
    include_prow_job_link: bool = False,
) -> dict[str, Any]:
    """Build the same JSON shape as hubhelper's slack.webhookPayload.

    ``slack_pr_cap`` limits how many PR rows appear in the Block Kit list (Slack size limits).
    Pass ``None`` to include every PR (used for the HTML mrkdwn copy block).

    When ``include_prow_job_link`` is true and ``PROW_JOB_URL`` is set, a Prow job link is inserted
    under the header (verbose Slack webhook and HTML mrkdwn copy; minimal webhook uses
    ``build_slack_payload_minimal`` instead).
    """
    att_n = len(attention)
    blocks: list[dict[str, Any]] = [_plain_header("Pull request dashboard")]
    if include_prow_job_link and PROW_JOB_URL:
        blocks.append(
            _section_mrkdwn(
                f"*Prow job:* <{PROW_JOB_URL}|Open in Prow> — full job logs and complete PR list."
            )
        )
    blocks.extend(
        [
            _context_line(
                f":clock3: Data from *{fetched_at.strftime('%Y-%m-%d %H:%M')} UTC* · {_label_mode_line()}"
            ),
            _section_fields(
                f"*Open PRs*\n_{open_pr_count}_ after filters",
                f"*Need attention*\n_{att_n}_",
            ),
        ]
    )
    if fetch_errors:
        blocks.append(_divider())
        err_lines = ["*:warning: Errors*"]
        for e in fetch_errors:
            safe = e.replace("`", "'")
            err_lines.append(f"• `{safe}`")
        blocks.append(_section_mrkdwn("\n".join(err_lines)))
    blocks.append(_divider())
    if not attention:
        blocks.append(_section_mrkdwn("✅ *All clear* — no PRs need attention right now."))
    else:
        if slack_pr_cap is None or len(attention) <= slack_pr_cap:
            to_show = attention
            header = f"*PRs needing attention* — _{len(attention)}_ PR(s)"
        else:
            to_show = attention[:slack_pr_cap]
            header = f"*PRs needing attention* — showing _{slack_pr_cap}_ of _{len(attention)}_"
        blocks.append(_section_mrkdwn(header))
        pr_lines: list[str] = []
        for n, p in enumerate(to_show, start=1):
            title = slack_escape(p.title)
            if len(title) > 120:
                title = title[:117] + "…"
            link_text = f"{p.repo_full}#{p.number} — {title.replace('|', '·')}"
            pr_lines.append(f"*{n}.* <{p.html_url}|{slack_escape(link_text)}>")
            base = p.base_branch or "—"
            line2 = (
                f"   `{slack_escape(p.author)}` · base `{slack_escape(base)}` · "
                f"open *{p.age_days}d* · idle *{p.inactive_days}d*"
            )
            if p.missing_labels:
                line2 += f" · *Missing:* {format_label_list(p.missing_labels)}"
            if p.forbidden_labels:
                line2 += f"  · *Should not have:* {format_label_list(p.forbidden_labels)}"
            pr_lines.append(line2)
            pr_lines.append("")
        if slack_pr_cap is not None and len(attention) > len(to_show):
            rest = len(attention) - len(to_show)
            pr_lines.append(f"\n_…and {rest} more not listed in this Slack message._\n")
        body = "\n".join(pr_lines).rstrip()
        _chunk_section_blocks(blocks, body)
    return {
        "text": _fallback_summary(open_pr_count, att_n, fetched_at.astimezone(timezone.utc).isoformat()),
        "blocks": blocks,
    }


def slack_serialize_payload(payload: dict[str, Any]) -> bytes:
    """Same JSON serialization as the Slack Incoming Webhook POST body."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# Slack mrkdwn ``<https://host/path|label>`` (http(s) only; label must not contain ``>``).
_SLACK_HTTP_LINK_RE = re.compile(r"<(https?://[^|\s<>]+)\|([^>]+)>")


def slack_mrkdwn_http_links_to_markdown(text: str) -> str:
    """Convert Slack-style ``<url|title>`` links to CommonMark ``[title](url)`` for generic paste targets."""

    def repl(m: re.Match[str]) -> str:
        url, title = m.group(1), m.group(2)
        title_esc = title.replace("\\", "\\\\").replace("]", "\\]")
        return f"[{title_esc}]({url})"

    return _SLACK_HTTP_LINK_RE.sub(repl, text)


def slack_blocks_to_mrkdwn_for_paste(payload: dict[str, Any]) -> str:
    """Flatten Block Kit payload to mrkdwn-ish text Slack's composer understands (links, bold, etc.)."""
    lines: list[str] = []
    for block in payload.get("blocks") or []:
        bt = block.get("type")
        if bt == "header":
            pt = block.get("text") or {}
            if isinstance(pt, dict) and pt.get("type") == "plain_text":
                lines.append(f"*{pt.get('text', '')}*")
        elif bt == "context":
            for el in block.get("elements") or []:
                if isinstance(el, dict) and el.get("type") == "mrkdwn":
                    lines.append(str(el.get("text", "")))
        elif bt == "section":
            st = block.get("text")
            if isinstance(st, dict) and st.get("type") == "mrkdwn":
                lines.append(str(st.get("text", "")))
            for fld in block.get("fields") or []:
                if isinstance(fld, dict) and fld.get("type") == "mrkdwn":
                    lines.append(str(fld.get("text", "")))
        elif bt == "divider":
            lines.append("────────")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _b64_utf8(s: str) -> str:
    return base64.standard_b64encode(s.encode("utf-8")).decode("ascii")


def write_pr_dashboard_html(
    path: Path,
    *,
    fetched_at: datetime,
    repos: list[tuple[str, str]],
    open_count: int,
    attention: list[AttentionPR],
    fetch_errors: list[str],
    slack_mrkdwn: str,
    slack_json: str,
    stale_days: int,
) -> None:
    """Write a self-contained HTML report with PR cards and Slack copy helpers."""
    repos_label = html.escape(", ".join(f"{o}/{r}" for o, r in repos))
    gen_ts = html.escape(fetched_at.strftime("%Y-%m-%d %H:%M UTC"))
    label_line = html.escape(_label_mode_line())
    mrkdwn_b64 = _b64_utf8(slack_mrkdwn)
    json_b64 = _b64_utf8(slack_json)
    att_n = len(attention)

    rows: list[str] = []
    for p in attention:
        reasons_html = "".join(f'<li class="reason">{html.escape(r)}</li>' for r in p.reasons)
        miss = ", ".join(html.escape(x) for x in p.missing_labels) or "—"
        forbid = ", ".join(html.escape(x) for x in p.forbidden_labels) or "—"
        title_e = html.escape(p.title)
        rows.append(
            "<tr>"
            f'<td class="mono"><a href="{html.escape(p.html_url, quote=True)}">{html.escape(p.repo_full)}#{p.number}</a></td>'
            f'<td class="title"><a href="{html.escape(p.html_url, quote=True)}">{title_e}</a></td>'
            f'<td class="mono">{html.escape(p.author)}</td>'
            f'<td class="mono">{html.escape(p.base_branch or "—")}</td>'
            f'<td class="num">{p.age_days}d</td>'
            f'<td class="num">{p.inactive_days}d</td>'
            f"<td><ul class=\"reasons\">{reasons_html}</ul></td>"
            f"<td class=\"labels\">{miss}</td>"
            f"<td class=\"labels\">{forbid}</td>"
            "</tr>"
        )
    table_body = "\n".join(rows) if rows else ""

    errors_block = ""
    if fetch_errors:
        errs = "".join(f"<li>{html.escape(e)}</li>" for e in fetch_errors)
        errors_block = f'<div class="banner error"><strong>Errors</strong><ul>{errs}</ul></div>'

    all_clear = ""
    table_section = ""
    if not attention:
        all_clear = '<div class="all-clear"><p>All clear</p><p class="sub">No PRs need attention right now.</p></div>'
    else:
        table_section = f"""<section class="prs">
<h2>Pull requests needing attention <span class="badge">{att_n}</span></h2>
<div class="table-wrap">
<table>
<thead><tr>
<th>PR</th><th>Title</th><th>Author</th><th>Base</th><th>Open</th><th>Idle</th><th>Why</th><th>Missing labels</th><th>Forbidden labels</th>
</tr></thead>
<tbody>
{table_body}
</tbody>
</table>
</div>
<p class="hint">Sorted by <strong>longest open</strong> first (then idle time). Stale / age threshold: <strong>{stale_days}</strong> days · Repos watched: {repos_label}</p>
</section>"""

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR attention — {gen_ts}</title>
<style>
:root {{
  --bg: #0f1419;
  --panel: #1a2332;
  --text: #e7ecf3;
  --muted: #8b9cb3;
  --accent: #3d8bfd;
  --ok: #3fb950;
  --warn: #d29922;
  --border: #30363d;
  --link: #58a6ff;
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  margin: 0;
  line-height: 1.5;
  padding: 1.5rem clamp(1rem, 4vw, 2.5rem);
}}
header {{
  margin-bottom: 1.75rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 1rem;
}}
h1 {{ font-size: 1.5rem; font-weight: 600; margin: 0 0 0.35rem 0; }}
.subtitle {{ color: var(--muted); font-size: 0.9rem; margin: 0; }}
.metrics {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 1rem;
  margin-bottom: 1.75rem;
}}
.metric {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.25rem;
}}
.metric .val {{ font-size: 1.75rem; font-weight: 700; color: var(--accent); }}
.metric .lbl {{ font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
.all-clear {{
  background: linear-gradient(135deg, #1a2f22, var(--panel));
  border: 1px solid #23863644;
  border-radius: 12px;
  padding: 2rem;
  text-align: center;
  margin-bottom: 2rem;
}}
.all-clear p {{ margin: 0; font-size: 1.25rem; color: var(--ok); }}
.all-clear .sub {{ margin-top: 0.5rem; font-size: 0.95rem; color: var(--muted); }}
.banner.error {{ background: #3d1f1f; border: 1px solid #f8514966; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; color: #ffb1b1; }}
.banner.error ul {{ margin: 0.5rem 0 0 1.1rem; }}
section {{ margin-bottom: 2.25rem; }}
h2 {{ font-size: 1.1rem; margin: 0 0 1rem 0; display: flex; align-items: center; gap: 0.5rem; }}
.badge {{
  background: var(--warn);
  color: #0d1117;
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
}}
.table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
th, td {{ padding: 0.65rem 0.85rem; text-align: left; border-bottom: 1px solid var(--border); vertical-align: top; }}
th {{ background: var(--panel); color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; }}
tr:last-child td {{ border-bottom: none; }}
td.mono {{ font-family: ui-monospace, monospace; font-size: 0.8rem; }}
td.title {{ max-width: 22rem; }}
td.title a {{ color: var(--link); text-decoration: none; }}
td.title a:hover {{ text-decoration: underline; }}
td.num {{ text-align: right; white-space: nowrap; }}
ul.reasons {{ margin: 0; padding-left: 1.1rem; color: var(--muted); font-size: 0.82rem; }}
li.reason {{ margin: 0.2rem 0; }}
td.labels {{ font-size: 0.8rem; color: var(--muted); max-width: 10rem; }}
.hint {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.75rem; }}
.slack-panel {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.25rem 1.5rem;
}}
.slack-panel h2 {{ margin-top: 0; }}
.slack-panel p {{ color: var(--muted); font-size: 0.9rem; margin: 0 0 1rem 0; }}
textarea.slack-field {{
  width: 100%;
  min-height: 12rem;
  font-family: ui-monospace, monospace;
  font-size: 0.8rem;
  background: #0d1117;
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem;
  resize: vertical;
}}
textarea.json-field {{ min-height: 8rem; }}
.actions {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.75rem 0 1rem 0; }}
button {{
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}}
button:hover {{ filter: brightness(1.08); }}
button.secondary {{ background: #30363d; color: var(--text); }}
button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
.toast {{
  display: none;
  margin-left: 0.5rem;
  font-size: 0.85rem;
  color: var(--ok);
}}
.toast.show {{ display: inline; }}
details {{ margin-top: 1rem; color: var(--muted); font-size: 0.85rem; }}
details summary {{ cursor: pointer; color: var(--text); }}
</style>
</head>
<body>
<header>
  <h1>Pull request attention</h1>
  <p class="subtitle">Generated {gen_ts} · {label_line}</p>
  <p class="subtitle">Repos: {repos_label}</p>
</header>
{errors_block}
<div class="metrics">
  <div class="metric"><div class="val">{open_count}</div><div class="lbl">Open after filters</div></div>
  <div class="metric"><div class="val">{att_n}</div><div class="lbl">Need attention</div></div>
</div>
{all_clear}
{table_section}
<section class="slack-panel">
  <h2>Post to Slack manually</h2>
  <p><strong>Copy text</strong> below uses Slack-style bold/context and CommonMark links <code>[title](url)</code> (converted from Slack <code>&lt;url|title&gt;</code>). Every attention PR is listed. The automatic Slack webhook only includes the first <strong>{MAX_PRS_IN_MESSAGE}</strong> rows. <strong>Webhook JSON</strong> matches that capped payload (replay / debugging).</p>
  <h3 style="font-size:0.95rem;margin:1rem 0 0.5rem 0;">Copy / Markdown</h3>
  <div class="actions">
    <button type="button" id="btn-copy-mrkdwn">Copy text</button>
    <span class="toast" id="toast-mrkdwn">Copied</span>
  </div>
  <textarea id="slack-mrkdwn" class="slack-field" readonly spellcheck="false"></textarea>
  <details>
    <summary>Webhook JSON (exact POST body)</summary>
    <div class="actions" style="margin-top:0.75rem">
      <button type="button" class="secondary" id="btn-copy-json">Copy JSON</button>
      <span class="toast" id="toast-json">Copied</span>
    </div>
    <textarea id="slack-json" class="slack-field json-field" readonly spellcheck="false"></textarea>
  </details>
</section>
<div id="b64-m" style="display:none">{mrkdwn_b64}</div>
<div id="b64-j" style="display:none">{json_b64}</div>
<script>
(function() {{
  function b64ToUtf8(b64) {{
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder("utf-8").decode(bytes);
  }}
  const mEl = document.getElementById("slack-mrkdwn");
  const jEl = document.getElementById("slack-json");
  mEl.value = b64ToUtf8(document.getElementById("b64-m").textContent.trim());
  jEl.value = b64ToUtf8(document.getElementById("b64-j").textContent.trim());
  function flash(id) {{
    const t = document.getElementById(id);
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 2000);
  }}
  function copyTa(el, toastId) {{
    el.focus();
    el.select();
    const ok = document.execCommand("copy");
    if (!ok && navigator.clipboard) {{
      navigator.clipboard.writeText(el.value).then(() => flash(toastId)).catch(() => {{}});
    }} else {{
      flash(toastId);
    }}
  }}
  document.getElementById("btn-copy-mrkdwn").addEventListener("click", () => copyTa(mEl, "toast-mrkdwn"));
  document.getElementById("btn-copy-json").addEventListener("click", () => copyTa(jEl, "toast-json"));
}})();
</script>
</body>
</html>"""
    path.write_text(doc, encoding="utf-8")


def slack_post_payload(payload: dict[str, Any]) -> None:
    webhook = SLACK_WEBHOOK_URL
    if not webhook:
        sys.stderr.write("SLACK_WEBHOOK_URL is not set\n")
        sys.exit(1)
    body = slack_serialize_payload(payload)
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "edge-tooling-pr-notifier"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"Slack HTTP error {e.code}: {e.read().decode(errors='replace')}\n")
        raise


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    owners_path = Path(_env("OWNERS_FILE", str(repo_root / "OWNERS")))
    aliases_path = Path(_env("OWNERS_ALIASES_FILE", str(repo_root / "OWNERS_ALIASES")))
    try:
        author_allowlist = load_github_logins_from_owners_files(owners_path, aliases_path)
    except OSError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    if not author_allowlist:
        sys.stderr.write(
            "No GitHub logins after expanding OWNERS with OWNERS_ALIASES "
            f"(owners={owners_path}, aliases={aliases_path})\n"
        )
        return 1
    fetched_at = datetime.now(timezone.utc)
    open_count = 0
    items: list[AttentionPR] = []
    for org, repo in REPOS:
        for pr in iter_pulls(org, repo):
            if not isinstance(pr, dict):
                continue
            if not pr_matches_filters(pr, author_allowlist):
                continue
            open_count += 1
            row = pr_attention_row(pr, org, repo)
            if row:
                items.append(row)

    # Longest-open first (age since creation), then most idle, then stable tie-breakers.
    items.sort(key=lambda p: (-p.age_days, -p.inactive_days, p.repo_full.lower(), p.number))

    fetch_errors: list[str] = []

    payload_full_mrkdwn = build_slack_payload(
        fetched_at=fetched_at,
        open_pr_count=open_count,
        attention=items,
        fetch_errors=fetch_errors,
        slack_pr_cap=None,
        include_prow_job_link=True,
    )
    if VERBOSE_SLACK_MESSAGE:
        payload_slack = build_slack_payload(
            fetched_at=fetched_at,
            open_pr_count=open_count,
            attention=items,
            fetch_errors=fetch_errors,
            slack_pr_cap=MAX_PRS_IN_MESSAGE,
            include_prow_job_link=True,
        )
    else:
        payload_slack = build_slack_payload_minimal()
    mrkdwn = slack_mrkdwn_http_links_to_markdown(slack_blocks_to_mrkdwn_for_paste(payload_full_mrkdwn))
    json_raw = slack_serialize_payload(payload_slack).decode("utf-8")

    html_out = Path(_env("GH_NOTIFIER_HTML_OUTPUT", str(repo_root / "gh-notifier" / "pr-dashboard.html")))
    html_out.parent.mkdir(parents=True, exist_ok=True)
    write_pr_dashboard_html(
        html_out,
        fetched_at=fetched_at,
        repos=REPOS,
        open_count=open_count,
        attention=items,
        fetch_errors=fetch_errors,
        slack_mrkdwn=mrkdwn,
        slack_json=json_raw,
        stale_days=STALE_DAYS,
    )

    if not items:
        print(
            f"No PRs need attention across {len(REPOS)} repo(s) ({open_count} open after filters). "
            f"Dashboard: {html_out.resolve()}",
            flush=True,
        )
    else:
        print(
            f"{len(items)} open PR(s) need attention (of {open_count} after filters). "
            f"Dashboard: {html_out.resolve()}",
            flush=True,
        )

    if SLACK_WEBHOOK_URL:
        slack_post_payload(payload_slack)
        print("Slack notification sent.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
