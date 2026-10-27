const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)];
assert.equal(scripts.length, 2);
assert.equal(scripts[0][1], ' id="report-data" type="application/json"');
const payload = scripts[0][2];
assert(!payload.includes('<'));
const data = JSON.parse(payload);
assert.equal(data.diagnostics_text, 'Synthetic </ScRiPt><script>throw "injected"</script><!-- < & > " \\ \n \u2028 \u2029');
const renderer = scripts[1][2];
const corrupted = payload.replace('0.999999999999999', '0.9XXXXXX999999999');
assert.notEqual(corrupted, payload);

const invalidShapes = [null, false, true, 0, 1, '', 'PAYLOAD_MARKER', [], [data], {},
    {...data, component_title: null}, {...data, component_title: 42},
    {...data, timestamp: null}, {...data, timestamp: []},
    {...data, pr_status: {}}, {...data, pr_status: 'PAYLOAD_MARKER'},
    {...data, diagnostics_text: {}}, {...data, diagnostics_text: 42}];
for (const field of ['releases_data', 'status_data', 'pr_data', 'bugs_tab_data',
    'images_tab_data', 'index_data', 'jira_cfg']) {
    for (const value of [[], 'PAYLOAD_MARKER', 1, true]) {
        invalidShapes.push({...data, [field]: value});
    }
}
for (const invalid of [corrupted, payload.slice(0, -1), '', '{"synthetic":"PAYLOAD_MARKER",}', null,
    ...invalidShapes.map(value => JSON.stringify(value))]) {
    const loading = {style: {}, setAttribute(name, value) { this[name] = value; }};
    const window = {};
    const document = {
        getElementById(id) {
            if (id === 'report-data') return invalid === null ? null : {textContent: invalid};
            assert.equal(id, 'loading', 'Rendering must not start on invalid data');
            return loading;
        }
    };
    vm.runInNewContext(renderer, {document, window});
    assert.equal(window.REPORT_DATA, undefined);
    assert.equal(loading.role, 'alert');
    assert.notEqual(loading.style.display, 'none');
    assert.match(loading.textContent, /Unable to load report.*invalid JSON.*Regenerate/);
    assert(!loading.textContent.includes('PAYLOAD_MARKER'));
    assert(!loading.textContent.includes('0.9XXXXXX999999999'));
}
console.log('PASS malformed numeric token, truncation, empty/missing payload and invalid JSON: visible safe error, no rendering');
console.log(`PASS ${invalidShapes.length} invalid top-level shapes: visible safe error, no rendering or payload leakage`);
console.log('PASS HTML-safe JSON round trip: mixed-case script tags, comments, quotes, backslashes, newline and Unicode separators');
