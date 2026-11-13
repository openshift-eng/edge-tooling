// The manual "+ Create Bug in JIRA" action is offered on every rendered
// failure card of a component with a Jira create configuration, whatever
// the matched bugs or categorization decision; it is absent without one.
// Loaded by test_report_filter.js, which supplies the DOM shim and renderer.
const assert = require('node:assert/strict');
const fs = require('node:fs');

const JIRA_CREATE = 'https://issues.redhat.com/secure/CreateIssueDetails!init.jspa?';
const JIRA_BROWSE = 'https://issues.redhat.com/browse/';
const BUTTON_TEXT = '+ Create Bug in JIRA';

function decisionAction(issue) {
    return (issue.bug_match && issue.bug_match.decision && issue.bug_match.decision.action) || 'none';
}
function createButtons(element) {
    return element.querySelectorAll('a.create-bug-btn');
}
function createParams(button) {
    assert(button.href.startsWith(JIRA_CREATE), 'button opens the Jira create form: ' + button.href);
    return new URLSearchParams(button.href.slice(JIRA_CREATE.length));
}
function assertSingleButton(element, cfg, issue, label) {
    const buttons = createButtons(element);
    assert.equal(buttons.length, 1, label + ': exactly one Create Bug button');
    const button = buttons[0];
    assert.equal(button.textContent, BUTTON_TEXT, label);
    assert.equal(button.target, '_blank', label + ': opens a new tab');
    assert.equal(button.className, 'bug-tag create-bug-btn', label);
    const params = createParams(button);
    assert.equal(params.get('pid'), cfg.pid, label);
    assert.equal(params.get('issuetype'), cfg.issuetype, label);
    assert.equal(params.get('components'), cfg.component, label);
    assert.equal(params.get('labels'), cfg.labels, label);
    assert.equal(params.get('reporter'), cfg.reporter || null, label);
    assert.equal(params.get('summary'), ((cfg.summary_prefix || '') + issue.title).substring(0, 100), label);
    return button;
}

function bugInfo(bug) {
    return ' ' + (bug.summary || '') + ' (' + (bug.status || '') + (bug.assignee ? ', ' + bug.assignee : '') + ')';
}
// Matches, evidence and the decision badge render as before, next to the button.
function assertMatchesAndBadge(div, bugMatch, label) {
    const dups = (bugMatch && bugMatch.duplicates) || [];
    const regs = (bugMatch && bugMatch.regressions) || [];
    const openLinks = div.querySelectorAll('a.bug-tag-open');
    for (const dup of dups) {
        assert(openLinks.some(link => link.textContent === dup.key && link.href === JIRA_BROWSE + dup.key), label + ': open match link ' + dup.key);
        assert(div.textContent.includes(dup.key + bugInfo(dup)), label + ': open match details ' + dup.key);
    }
    assert.equal(div.textContent.includes('Bugs:'), dups.length > 0, label + ': open matches heading');
    for (const reg of regs) {
        const regLinks = div.querySelectorAll('a.bug-tag-regression').filter(link => link.textContent === reg.key + ' ⟲');
        assert(regLinks.length === 1 && regLinks[0].href === JIRA_BROWSE + reg.key, label + ': closed match link ' + reg.key);
        const terms = (reg.match_evidence && reg.match_evidence.matched_terms) || [];
        const evidence = terms.length ? ' — matched: ' + terms.join(', ') : ' — match evidence unavailable';
        assert(div.textContent.includes(reg.key + ' ⟲' + bugInfo(reg) + evidence), label + ': closed match evidence ' + reg.key);
    }
    assert.equal(div.textContent.includes('Possibly related closed bugs (fix inclusion unverified):'), regs.length > 0, label);
    assert(!div.textContent.includes('Regressions:'), label + ': closed matches are not labelled regressions');
    assert.equal(div.textContent.includes('No tracked bugs'), !dups.length && !regs.length, label);
    const decision = bugMatch && bugMatch.decision;
    const linkedBadges = openLinks.filter(link => link.textContent.startsWith('Linked → '));
    const skipBadges = div.querySelectorAll('.decision-skip');
    if (decision && decision.action === 'linked' && decision.jira_key) {
        assert.equal(linkedBadges.length, 1, label + ': linked badge');
        assert.equal(linkedBadges[0].textContent, 'Linked → ' + decision.jira_key, label);
        assert.equal(linkedBadges[0].href, JIRA_BROWSE + decision.jira_key, label);
        assert.equal(linkedBadges[0].title, decision.reason || '', label);
    } else assert.equal(linkedBadges.length, 0, label + ': no linked badge');
    if (decision && decision.action === 'skip') {
        assert.equal(skipBadges.length, 1, label + ': skip badge');
        assert.equal(skipBadges[0].textContent, decision.skip_category === 'infrastructure'
            ? 'Skipped — infrastructure failure' : 'Skipped — stale regression', label);
        assert.equal(skipBadges[0].title, decision.reason || '', label);
    } else assert.equal(skipBadges.length, 0, label + ': no skip badge');
}

module.exports = function runCreateBugTests({reportTest, Element, document, renderGroupFixture, prTab, reportFiles}) {
    const saved = {};
    for (const key of ['jira_cfg', 'releases_data', 'status_data', 'pr_data', 'pr_status']) saved[key] = reportTest.data[key];

    // --- renderBugLinks across every decision and match state ---
    const cfg = {pid: '12345', issuetype: '10100', component: '20001', labels: 'synthetic-ci-label', summary_prefix: 'Synth CI: '};
    const cardIssue = {title: 'greenboot timeout <b>&</b>', severity: 'HIGH', failure_type: 'test', root_cause: 'node failed to boot {x}',
        affected_jobs: [{name: 'periodic-synth', url: 'https://example.invalid/run/1', date: '2026-10-28'}], job_count: 1};
    function linksWithCfg(jiraCfg, bugMatch, issue = cardIssue) {
        reportTest.data.jira_cfg = jiraCfg;
        const parent = new Element('div');
        reportTest.renderBugLinks(parent, bugMatch, issue, 'Release 4.20');
        const div = parent.querySelector('.bug-links');
        assert(div, 'bug links rendered');
        return div;
    }
    const links = bugMatch => linksWithCfg(cfg, bugMatch);
    const openDup = {key: 'SYNTH-101', summary: 'tracks the failure', status: 'In Progress', assignee: 'dev'};
    const closedMatch = {key: 'SYNTH-202', summary: 'earlier occurrence', status: 'Closed', assignee: '',
        match_evidence: {matched_terms: ['greenboot', 'timeout'], matched_fields: ['summary'], ignored_terms: []}};
    const legacyClosed = {key: 'SYNTH-303', summary: 'legacy closed entry', status: 'Verified', assignee: '', updated: '2026-01-01'};
    const suggestRelated = {action: 'suggest', jira_key: '', skip_category: '',
        reason: 'Possibly related to closed SYNTH-202; fix inclusion in the tested code is not established — review before filing a new bug'};
    const cases = {
        'no decision, no matches': {duplicates: [], regressions: []},
        'no decision, open matched bug': {duplicates: [openDup], regressions: []},
        'no decision, closed match': {duplicates: [], regressions: [closedMatch]},
        'suggest, no matches': {duplicates: [], regressions: [],
            decision: {action: 'suggest', jira_key: '', skip_category: '', reason: 'No matching existing bugs found — suggest filing a new bug'}},
        'suggest, possibly related closed match': {duplicates: [], regressions: [closedMatch], decision: suggestRelated},
        'suggest, closed matches with and without evidence': {duplicates: [], regressions: [closedMatch, legacyClosed], decision: suggestRelated},
        'linked to open matched bug': {duplicates: [openDup], regressions: [],
            decision: {action: 'linked', jira_key: 'SYNTH-101', skip_category: '', reason: 'Linked to existing bug SYNTH-101'}},
        'linked to created bug': {duplicates: [], regressions: [],
            decision: {action: 'linked', jira_key: 'SYNTH-404', skip_category: '', reason: 'Linked to existing bug SYNTH-404'}},
        'skipped infrastructure': {duplicates: [], regressions: [],
            decision: {action: 'skip', jira_key: '', skip_category: 'infrastructure', reason: 'Infrastructure failure — not a product bug'}},
        'legacy skipped stale regression': {duplicates: [], regressions: [legacyClosed],
            decision: {action: 'skip', jira_key: '', skip_category: 'stale_regression', reason: 'Stale failure predating fix for SYNTH-303'}},
        'legacy unknown action': {duplicates: [openDup], regressions: [legacyClosed],
            decision: {action: 'retired-action', jira_key: '', skip_category: '', reason: ''}},
        'legacy data without bug_match': undefined,
        'legacy data without match lists': {error_signature: 'greenboot timeout'},
    };
    const reference = assertSingleButton(links(cases['no decision, no matches']), cfg, cardIssue, 'reference').href;
    for (const [label, bugMatch] of Object.entries(cases)) {
        const div = links(bugMatch);
        const button = assertSingleButton(div, cfg, cardIssue, label);
        assert.equal(button.href, reference, label + ': prefill does not depend on matches or decision');
        assert.equal(div.children[0], button, label + ': button leads the bug links');
        assertMatchesAndBadge(div, bugMatch, label);
    }
    assert.deepEqual(links(cases['linked to created bug']).children.map(child => child.tag), ['a', 'a', 'br', 'span']);
    // the summary prefill is truncated exactly as before
    const longDiv = linksWithCfg(cfg, cases['linked to open matched bug'], {...cardIssue, title: 'x'.repeat(150)});
    assert.equal(createParams(createButtons(longDiv)[0]).get('summary'), ('Synth CI: ' + 'x'.repeat(150)).substring(0, 100));
    // without a Jira create configuration no destination is invented
    for (const [label, bugMatch] of Object.entries(cases)) {
        for (const missing of [null, undefined]) {
            const div = linksWithCfg(missing, bugMatch);
            assert.equal(createButtons(div).length, 0, label + ': no button without Jira create configuration');
            assert(!div.textContent.includes(BUTTON_TEXT), label);
            assert(div.children.length === 0 || div.children[0].tag !== 'br', label + ': no leading line break');
            assertMatchesAndBadge(div, bugMatch, label + ' (no Jira create configuration)');
        }
    }
    assert.deepEqual(linksWithCfg(null, cases['no decision, no matches']).children.map(child => child.textContent), ['No tracked bugs']);
    console.log(`PASS Create Bug button: ${Object.keys(cases).length} match/decision states keep one identical button with badges and matches; none without Jira create configuration`);

    // --- full report cards produced by create-report.py for each component ---
    for (const file of reportFiles) {
        const html = fs.readFileSync(file, 'utf8');
        const payload = JSON.parse(html.split('window.REPORT_DATA = ', 2)[1].split(';</script>', 1)[0]);
        Object.assign(reportTest.data, {jira_cfg: payload.jira_cfg, pr_data: payload.pr_data, pr_status: payload.pr_status});
        renderGroupFixture(payload.releases_data, payload.status_data || {});
        prTab.textContent = '';
        reportTest.renderPRs(prTab);
        const cards = [];
        for (const version of Object.keys(payload.releases_data)) {
            (payload.releases_data[version].issues || []).forEach(entry => cards.push({entry, anchor: 'release-' + version, label: 'Release ' + version}));
        }
        for (const pr of (payload.pr_data || {}).prs || []) {
            (pr.issues || []).forEach(entry => cards.push({entry, anchor: 'pr-' + pr.number, label: 'PR #' + pr.number}));
        }
        assert(cards.length > 0, file + ': report has failure cards');
        let buttons = 0;
        for (const {entry, anchor, label} of cards) {
            const row = document.getElementById(anchor + '-' + entry.number);
            assert(row && row.classList.contains('issue-row'), `${label} #${entry.number}: issue row rendered`);
            const detail = row.nextElementSibling;
            assert(detail && detail.classList.contains('detail-row'), `${label} #${entry.number}: detail row rendered`);
            const name = `${label} ${entry.title} [${decisionAction(entry)}]`;
            // the card keeps its original issue details around the bug links
            assert.equal(row.querySelector('.col-title').textContent, entry.title, name + ': title');
            const rootCause = detail.querySelector('.root-cause');
            if (entry.root_cause) assert(rootCause && rootCause.textContent.endsWith(entry.root_cause), name + ': root cause');
            for (const job of entry.affected_jobs || []) {
                assert(detail.querySelectorAll('li a').some(link => link.href === job.url && link.textContent === job.name && link.target === '_blank'), name + ': affected job ' + job.name);
            }
            assertMatchesAndBadge(detail.querySelector('.bug-links'), entry.bug_match, name);
            if (payload.jira_cfg) {
                const button = assertSingleButton(detail, payload.jira_cfg, entry, name);
                assert(decodeURIComponent(button.href).includes('CI job failures detected: ' + label), name + ': source label prefilled');
                buttons++;
            } else {
                assert.equal(createButtons(detail).length, 0, name + ': no button without Jira create configuration');
            }
        }
        const allButtons = createButtons(document.getElementById('tab-periodics')).length + createButtons(prTab).length;
        assert.equal(allButtons, buttons, file + ': no extra Create Bug buttons outside cards');
        console.log(`PASS Create Bug report ${payload.component_title}: ${cards.length} cards, ${buttons} buttons; decisions ${cards.map(card => decisionAction(card.entry)).join(',')}`);
    }

    Object.assign(reportTest.data, saved);
    if (reportFiles.length) {
        renderGroupFixture(saved.releases_data || {}, saved.status_data || {});
        prTab.textContent = '';
        reportTest.renderPRs(prTab);
    }
};
