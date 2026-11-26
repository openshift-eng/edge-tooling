const assert = require('node:assert/strict');
const vm = require('node:vm');

// Loads REPORT_DATA from a generated report into the harness data before rendering,
// then records the All Jobs tables (duration cells, pass summary, visible rows) while
// the Today only toggle and the search query change.
module.exports = function(data, reportHTML, searchQuery) {
    const sandbox = {window: {}};
    vm.runInNewContext(reportHTML.match(/<script>(window\.REPORT_DATA = [\s\S]*?;)<\/script>/)[1], sandbox);
    Object.keys(data).forEach(key => delete data[key]);
    Object.assign(data, JSON.parse(JSON.stringify(sandbox.window.REPORT_DATA)));
    return async function({document, reportTest}) {
        const versions = Object.keys(data.status_data);
        const section = version => document.getElementById('release-' + version);
        function observe() {
            return Object.fromEntries(versions.map(version => {
                const rate = section(version).querySelector('.jobs-pass-rate');
                const rows = section(version).querySelectorAll('.job-row');
                return [version, {
                    summary: rate.parent.textContent,
                    className: rate.className,
                    visible: rows.filter(row => row.style.display !== 'none').map(row => row.reportJob.job).sort(),
                }];
            }));
        }
        const toggle = document.getElementById('filter-today');
        function today(checked) {
            toggle.checked = checked;
            toggle.dispatch('change');
        }
        async function search(text) {
            const input = document.getElementById('report-filter');
            input.value = text;
            input.dispatch('input');
            const deadline = Date.now() + 5000;
            while (reportTest.pendingRegex) {
                assert(Date.now() < deadline, 'jobs table search did not settle');
                await new Promise(resolve => setTimeout(resolve, 5));
            }
        }
        const durations = Object.fromEntries(versions.flatMap(version =>
            section(version).querySelectorAll('.job-row').map(row => [row.reportJob.job, row.cells[3].textContent])));
        const states = {initial: observe()};
        for (const [name, change] of [
            ['today', () => today(true)], ['reset', () => today(false)], ['todayAgain', () => today(true)],
            ['todaySearch', () => search(searchQuery)], ['search', () => today(false)], ['cleared', () => search('')],
        ]) {
            await change();
            states[name] = observe();
        }
        console.log('JOBS TABLE ' + JSON.stringify({durations, states}));
        assert(!document.getElementById('tab-periodics').textContent.includes('NaN'));
        console.log(`PASS All Jobs table: ${Object.keys(durations).length} duration cells, ${Object.keys(states).length} Today/search states`);
    };
};
