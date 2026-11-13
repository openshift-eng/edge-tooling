"""Exercise draft popup handlers without a live browser or Jira connection."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_popup_actions_keep_multiple_drafts_separate():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise the browser script")
    script = Path(__file__).parents[1] / "edge_ocp_ci_monitor/templates/jira_drafts.js"
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element() {
  return {
    events: {},
    addEventListener(name, handler) { this.events[name] = handler; },
    focus() { this.focused = true; },
    select() { this.selected = true; },
    setAttribute(name, value) { this[name] = value; },
  };
}
function draft(number) {
  const summary = element();
  const dialog = element();
  const gather = element();
  const copy = element();
  const promptText = element();
  const prompt = {hidden: true, querySelector: () => promptText};
  const feedback = {textContent: ''};
  const description = Object.assign(element(), {value: `Description ${number} & literal &amp;`});
  dialog.querySelector = selector => ({
    '.jira-draft-feedback': feedback, '.gather-jira-details': gather,
    '.jira-chai-prompt': prompt, '.copy-jira-description': copy,
    '.jira-draft-description': description,
  })[selector];
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; dialog.events.close(); };
  dialog.getBoundingClientRect = () => ({left: 100, right: 500, top: 100, bottom: 600});
  const card = {querySelector: selector => ({
    '.jira-draft-summary': summary, '.jira-draft-dialog': dialog,
  })[selector]};
  return {card, summary, dialog, gather, copy, prompt, promptText, feedback, description};
}

const first = draft(1);
const second = draft(2);
let copied;
const clipboard = {writeText: async text => { copied = text; }};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document: {querySelectorAll: () => [first.card, second.card]},
  navigator: {clipboard},
});

(async () => {
  first.summary.events.click();
  assert.equal(first.dialog.open, true);
  assert.notEqual(second.dialog.open, true);
  first.gather.events.click();
  assert.equal(first.prompt.hidden, false);
  assert.equal(first.gather['aria-expanded'], 'true');
  assert.equal(first.promptText.selected, true);
  assert.equal(second.prompt.hidden, true);
  first.gather.events.click();
  assert.equal(first.prompt.hidden, true);
  assert.equal(first.gather['aria-expanded'], 'false');

  await first.copy.events.click();
  assert.equal(copied, first.description.value);
  assert.equal(first.feedback.textContent, 'Full description copied.');
  await second.copy.events.click();
  assert.equal(copied, second.description.value);

  clipboard.writeText = async () => { throw new Error('Clipboard unavailable'); };
  await second.copy.events.click();
  assert.equal(second.description.selected, true);
  assert.match(second.feedback.textContent, /highlighted description/);

  first.dialog.events.click({target: first.dialog, clientX: 120, clientY: 120});
  assert.equal(first.dialog.open, true);
  first.dialog.events.click({target: first.copy, clientX: 0, clientY: 0});
  assert.equal(first.dialog.open, true);
  first.dialog.events.click({target: first.dialog, clientX: 10, clientY: 10});
  assert.equal(first.dialog.open, false);
  assert.equal(first.summary.focused, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
