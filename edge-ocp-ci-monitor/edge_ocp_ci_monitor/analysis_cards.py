"""Edge-owned analysis rendering and HTML imports, isolated from payload-monitor."""

from __future__ import annotations

import html as html_mod
import logging
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader
from payload_monitor.report.generator import _load_json

from .security import redact_data, redact_token

logger = logging.getLogger(__name__)
_JINJA_ENV = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True)


def _safe_urls(urls: list[str]) -> list[str]:
    """Filter URLs to only allow the https scheme."""
    safe = []
    for url in urls:
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
            continue
        try:
            parsed = urlparse(url)
            _ = parsed.port
            if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                safe.append(url)
        except ValueError:
            continue
    return safe


def _safe_attempt_analyses(attempts) -> list:
    """Keep original attempt records only when their link is safe to render."""
    return [
        attempt for attempt in (attempts or [])
        if _safe_urls([attempt.get("prow_url") if isinstance(attempt, dict) else getattr(attempt, "prow_url", None)])
    ]


_JINJA_ENV.filters["safe_attempts"] = _safe_attempt_analyses


def _render_analysis_card(da: dict, attempt_count: int = 1, job_name: str = "", *, prow_url: str = "") -> str:
    """Render an AI analysis card as an HTML string."""
    template = _JINJA_ENV.get_template("_analysis_card.html")
    return template.render(
        root_cause=da.get("root_cause", ""),
        failure_type=da.get("failure_type", ""),
        impact=da.get("impact", ""),
        suspect_prs=_safe_urls(da.get("suspect_prs", [])),
        recommendation=da.get("recommendation", ""),
        same_root_cause=da.get("same_root_cause", True),
        attempt_analyses=_safe_attempt_analyses(da.get("attempt_analyses", [])),
        attempt_count=attempt_count,
        job_name=job_name,
        analysis_prow_url=prow_url,
    )


class _AnalysisCardSpans(HTMLParser):
    """Locate keyed cards without reserializing unrelated HTML or nested divs."""

    def __init__(self, content: str):
        super().__init__(convert_charrefs=True)
        self.content = content
        self.offsets = [0, *(m.end() for m in re.finditer("\n", content))]
        self.details = []
        self.depth = 0
        self.card = None
        self.spans = []
        self.feed(content)

    def _offset(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.details.append(attrs.get("data-prow-url"))
        if tag == "div":
            self.depth += 1
            if self.card is None and "deep-analysis-card" in attrs.get("class", "").split():
                key = attrs.get("data-prow-url") or next((key for key in reversed(self.details) if key), None)
                if key:
                    self.card = (self._offset(), self.depth, key, attrs.get("data-attempt-count"))

    def handle_endtag(self, tag):
        if tag == "div":
            if self.card is not None and self.card[1] == self.depth:
                start, _, key, count = self.card
                end = self.content.index(">", self._offset()) + 1
                if count is None:
                    scope = self.content[start:end].split("<h3", 1)[0]
                    match = re.search(r"Analysis covers all (\d+) attempts", scope)
                    count = match[1] if match else "1"
                self.spans.append((start, end, key, int(count) if count.isdigit() else 1))
                self.card = None
            self.depth -= 1
        if tag == "details" and self.details:
            self.details.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


def patch_analysis_cards(html_path: Path, analysis_path: Path) -> None:
    """Patch AI analysis cards directly into an existing HTML report.

    Uses data attributes placed in the template for reliable injection:
    - ``data-prow-url`` on ``.claude-suggestion`` divs marks where cards go
    - ``data-attempt-count`` carries the retry count for scope notes
    - ``data-prow-url`` on ``<details>`` elements identifies where to add badges
    - ``data-sippy-key`` on ``.sippy-analysis`` divs identifies job/release findings
    - ``data-enrichment-status="data-only"`` marks the header status div
    """
    token = os.environ.get("CHAI_MCP_TOKEN", "").strip()
    data = redact_data(_load_json(analysis_path, "Analysis JSON"), token)

    by_url = data.get("by_prow_url", {})
    by_sippy_job = data.get("by_sippy_job", {})
    if not by_url and not by_sippy_job:
        logger.info("No analysis entries to patch")
        return

    content = redact_token(html_path.read_text(), token)
    patched = 0
    badge_html = '<span class="badge ai-analyzed">AI Analyzed</span>'

    _suggestion_re = re.compile(
        r'<div class="claude-suggestion" data-prow-url="([^"]*)"'
        r' data-attempt-count="(\d+)">'
        r'.*?<!-- /claude-suggestion -->',
        re.DOTALL,
    )
    _details_re = re.compile(
        r'(<details [^>]*data-(?:prow-url|sippy-key)="([^"]*)"[^>]*>)\s*<summary>'
        r'(.*?)'
        r'(</summary>)',
        re.DOTALL,
    )

    patched_urls: set[str] = set()
    patched_sippy_keys: set[str] = set()

    # Older generated cards inherit the key from their surrounding details;
    # cards inserted by this patcher also carry it for subsequent imports.
    for start, end, key, count in reversed(_AnalysisCardSpans(content).spans):
        da = by_url.get(key)
        if da is not None:
            card = _render_analysis_card(da, attempt_count=count, job_name=da.get("job_name", ""), prow_url=key)
            content = content[:start] + card + content[end:]
            patched += 1
            patched_urls.add(key)

    def _replace_suggestion(m: re.Match) -> str:
        nonlocal patched
        prow_url = html_mod.unescape(m.group(1))
        da = by_url.get(prow_url)
        if da is None:
            return m.group(0)
        attempt_count = int(m.group(2))
        patched += 1
        patched_urls.add(prow_url)
        return _render_analysis_card(da, attempt_count=attempt_count, job_name=da.get("job_name", ""), prow_url=prow_url)

    content = _suggestion_re.sub(_replace_suggestion, content)

    def _replace_sippy(m: re.Match) -> str:
        nonlocal patched
        key = html_mod.unescape(m.group(2))
        da = by_sippy_job.get(key)
        if da is None:
            return m.group(0)
        patched += 1
        patched_sippy_keys.add(key)
        card = _render_analysis_card(da, job_name=da.get("job_name", ""))
        return f'{m.group(1)}{card}</div><!-- /sippy-analysis -->'

    content = re.sub(
        r'(<div class="sippy-analysis" data-sippy-key="([^"]*)">)'
        r'.*?</div><!-- /sippy-analysis -->',
        _replace_sippy, content, flags=re.DOTALL,
    )
    def _enable_sippy_row(m: re.Match) -> str:
        if html_mod.unescape(m.group(2)) not in patched_sippy_keys:
            return m.group(0)
        row = m.group(0)
        row = row.replace('<div ', '<details ', 1)
        row = row.replace('<div class="sippy-job-summary">', '<summary>', 1)
        row = row.replace('</div><!-- /sippy-job-summary -->', '</summary><!-- /sippy-job-summary -->', 1)
        row = row.replace('<div class="content sippy-job-analysis" hidden>', '<div class="content sippy-job-analysis">', 1)
        return row.removesuffix('</div><!-- /sippy-job -->') + '</details><!-- /sippy-job -->'

    content = re.sub(
        r'(<div id="sippy-row-\d+"[^>]*data-sippy-key="([^"]*)"[^>]*>)'
        r'.*?</div><!-- /sippy-job -->',
        _enable_sippy_row, content, flags=re.DOTALL,
    )

    def _insert_badge(m: re.Match) -> str:
        if html_mod.unescape(m.group(2)) not in patched_urls | patched_sippy_keys:
            return m.group(0)

        def _mark_analyzed(status: re.Match) -> str:
            values = status.group(1).split()
            if "ai-analyzed" not in values:
                values.append("ai-analyzed")
            return f'data-status="{" ".join(values)}"'

        details_tag = re.sub(r'data-status="([^"]*)"', _mark_analyzed, m.group(1))
        details_tag = re.sub(r' hidden>$', '>', details_tag)
        summary_content = m.group(3)
        if "ai-analyzed" in summary_content:
            return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'
        sippy_name_end = '</span><!-- /sippy-job-name -->'
        if sippy_name_end in summary_content:
            summary_content = summary_content.replace(sippy_name_end, sippy_name_end + badge_html, 1)
            return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'
        # Grid layout: insert badge inside the job cell before its closing </span>
        job_anchor = 'class="detail-cell-job"'
        job_pos = summary_content.find(job_anchor)
        if job_pos >= 0:
            open_gt = summary_content.find('>', job_pos)
            if open_gt >= 0:
                close_pos = summary_content.find('</span>', open_gt)
                if close_pos >= 0:
                    summary_content = (
                        summary_content[:close_pos]
                        + badge_html
                        + summary_content[close_pos:]
                    )
                    return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'
        # Legacy flat layout fallback
        for anchor in ('class="detail-date"', 'class="detail-version"'):
            anchor_pos = summary_content.find(anchor)
            if anchor_pos < 0:
                continue
            span_start = summary_content.rfind('<span', 0, anchor_pos)
            if span_start < 0:
                continue
            last_close = summary_content.rfind('>', 0, span_start)
            if last_close >= 0:
                ins = last_close + 1
                while ins < span_start and summary_content[ins] in ' \t\n':
                    ins += 1
                summary_content = (
                    summary_content[:ins]
                    + badge_html + '\n    '
                    + summary_content[ins:]
                )
            else:
                summary_content = badge_html + ' ' + summary_content
            break
        else:
            summary_content = summary_content + ' ' + badge_html
        return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'

    content = _details_re.sub(_insert_badge, content)

    # Update header: replace "Data only" status using data attribute
    if patched > 0:
        new_status = (
            '<div class="meta" style="margin-top:4px">'
            '<span style="color:var(--green)">● AI-enriched via Chai</span>'
            '</div>'
        )
        content = re.sub(
            r'<div class="meta"[^>]*data-enrichment-status="data-only"[^>]*>.*?</div>',
            new_status,
            content,
            count=1,
            flags=re.DOTALL,
        )

    html_path.write_text(content)
    logger.info(f"Patched {patched} analysis card(s) into {html_path}")


