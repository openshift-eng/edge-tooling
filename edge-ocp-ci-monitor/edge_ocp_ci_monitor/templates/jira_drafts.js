document.querySelectorAll('.jira-draft').forEach(function(card) {
  var summary = card.querySelector('.jira-draft-summary');
  var dialog = card.querySelector('.jira-draft-dialog');
  var feedback = dialog.querySelector('.jira-draft-feedback');
  summary.addEventListener('click', function() { dialog.showModal(); });
  dialog.addEventListener('click', function(event) {
    if (event.target !== dialog) return;
    var bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
  dialog.addEventListener('close', function() { summary.focus(); });
  var gather = dialog.querySelector('.gather-jira-details');
  var prompt = dialog.querySelector('.jira-chai-prompt');
  gather.addEventListener('click', function() {
    prompt.hidden = !prompt.hidden;
    gather.setAttribute('aria-expanded', String(!prompt.hidden));
    if (!prompt.hidden) {
      prompt.querySelector('textarea').focus();
      prompt.querySelector('textarea').select();
    }
  });
  dialog.querySelector('.copy-jira-description').addEventListener('click', async function() {
    var description = dialog.querySelector('.jira-draft-description');
    try {
      await navigator.clipboard.writeText(description.value);
      feedback.textContent = 'Full description copied.';
    } catch (error) {
      description.focus();
      description.select();
      feedback.textContent = 'Select and copy the highlighted description.';
    }
  });
});
