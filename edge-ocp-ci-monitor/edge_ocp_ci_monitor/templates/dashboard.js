// Tab bar scroll affordance for narrow viewports.
document.addEventListener('DOMContentLoaded', function setupTabScrolling() {
  const wrap = document.getElementById('dashboard-tabs-wrap');
  const nav = document.getElementById('dashboard-tabs-nav');
  if (!wrap || !nav) return;
  const leftBtn = wrap.querySelector('.tab-scroll-left');
  const rightBtn = wrap.querySelector('.tab-scroll-right');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function updateScrollEdges() {
    const max = nav.scrollWidth - nav.clientWidth;
    wrap.classList.toggle('can-scroll-left', nav.scrollLeft > 1);
    wrap.classList.toggle('can-scroll-right', nav.scrollLeft < max - 1);
  }
  function scrollTabs(direction) {
    nav.scrollBy({left: direction * nav.clientWidth * 0.6, behavior: reduceMotion ? 'auto' : 'smooth'});
  }
  leftBtn?.addEventListener('click', () => scrollTabs(-1));
  rightBtn?.addEventListener('click', () => scrollTabs(1));
  nav.addEventListener('scroll', updateScrollEdges);
  window.addEventListener('resize', updateScrollEdges);
  updateScrollEdges();
});

// The shared script retains filtering, sorting, imports, timing, theme and copy actions.
document.addEventListener('DOMContentLoaded', function() {
  const viewTabs = Array.from(document.querySelectorAll('.dashboard-tab'));
  const drawer = document.getElementById('finding-drawer');
  const drawerTabs = Array.from(drawer.querySelectorAll('[data-drawer-view]'));
  let lastTrigger;
  let draftCard;

  function selectView(name) {
    viewTabs.forEach(function(tab) {
      const active = tab.dataset.tab === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).classList.toggle('active', active);
    });
  }
  window.showDashboardView = function(name) {
    const tab = viewTabs.find(tab => tab.dataset.tab === name);
    if (!tab) return;
    selectView(name);
    tab.scrollIntoView({block: 'nearest'});
  };

  function keyboardTabs(tabs, choose) {
    tabs.forEach(function(tab) {
      tab.addEventListener('keydown', function(event) {
        const visible = tabs.filter(item => !item.hidden);
        const index = visible.indexOf(tab);
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % visible.length;
        if (event.key === 'ArrowLeft') next = (index + visible.length - 1) % visible.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = visible.length - 1;
        if (next !== undefined) {
          event.preventDefault();
          choose(visible[next]);
          visible[next].focus();
        }
      });
    });
  }
  viewTabs.forEach(tab => tab.addEventListener('click', () => selectView(tab.dataset.tab)));
  keyboardTabs(viewTabs, tab => selectView(tab.dataset.tab));
  selectView('payload-health');

  const jumpToFailures = window.jumpToFailures;
  window.jumpToFailures = function(version, jobtype) {
    selectView('failure-analysis');
    jumpToFailures(version, jobtype);
  };
  const scrollToJob = window.scrollToJob;
  window.scrollToJob = function(name) {
    selectView('failure-analysis');
    scrollToJob(name);
  };
  document.querySelectorAll('[data-dashboard-view]').forEach(function(link) {
    link.addEventListener('click', () => selectView(link.dataset.dashboardView));
  });

  function selectDrawer(name) {
    drawerTabs.forEach(function(tab) {
      const active = tab.dataset.drawerView === name;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
    });
  }
  drawerTabs.forEach(tab => tab.addEventListener('click', () => selectDrawer(tab.dataset.drawerView)));
  keyboardTabs(drawerTabs, tab => selectDrawer(tab.dataset.drawerView));

  function clone(source) {
    const copy = source.cloneNode(true);
    copy.removeAttribute('id');
    copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
    return copy;
  }
  function value(source, selector) {
    return source.querySelector(selector)?.textContent.trim() || '—';
  }
  function fact(label, text) {
    if (!text || text === '—') return;
    const wrapper = document.createElement('div');
    const term = document.createElement('dt');
    const definition = document.createElement('dd');
    term.textContent = label;
    definition.textContent = text;
    wrapper.append(term, definition);
    document.getElementById('finding-drawer-facts').appendChild(wrapper);
  }
  function openSource(source) {
    const panel = source.closest('.tab-panel');
    selectView(panel.id.slice(4));
    panel.querySelectorAll('.filter-btn[data-value="all"]').forEach(button => button.click());
    if (source.classList.contains('collapsed-row')) panel.querySelector('.expand-btn')?.click();
    if (source.tagName === 'DETAILS') source.open = true;
    source.scrollIntoView({block: 'start'});
    source.querySelector('summary, .sippy-cell-action a')?.focus({preventScroll: true});
  }
  function openFinding(source, trigger) {
    lastTrigger = trigger;
    const blocking = source.classList.contains('detail-row');
    const analysis = source.querySelector('.deep-analysis-card');
    const recommendation = source.querySelector('.da-recommendation');
    document.getElementById('finding-drawer-title').textContent = source.dataset.jobName;
    document.getElementById('finding-drawer-source').textContent = blocking ? 'Nightly Payload - Failure Analysis' : 'Sippy Job Health Status';
    const labels = document.getElementById('finding-drawer-labels');
    labels.replaceChildren();
    ['.detail-cell-topology .badge', '.detail-cell-type .badge', '.detail-cell-job .ai-analyzed'].forEach(function(selector) {
      const label = source.querySelector(selector);
      if (label) labels.appendChild(clone(label));
    });
    document.getElementById('finding-drawer-facts').replaceChildren();
    fact('Version', value(source, '.detail-cell-version'));
    fact('Topology', source.dataset.topology || source.dataset.regtopology);
    if (blocking) {
      fact('Type', source.dataset.jobtype);
      fact('Payload', value(source, '.detail-cell-payload a'));
      fact('Date', value(source, '.detail-cell-date'));
      fact('Recurrence', value(source, '.detail-cell-recurrence'));
    } else {
      fact('Runs', value(source, '.sippy-cell-runs'));
      fact('Current Pass Rate', value(source, '.sippy-cell-current'));
      fact('Previous Pass Rate', value(source, '.sippy-cell-previous'));
      fact('Relative change', value(source, '.sippy-cell-change'));
    }
    const rec = document.getElementById('finding-drawer-recommendation');
    rec.replaceChildren();
    rec.hidden = !recommendation;
    if (recommendation) rec.appendChild(clone(recommendation));
    const evidence = document.getElementById('drawer-analysis');
    evidence.replaceChildren();
    if (analysis) evidence.appendChild(clone(analysis));
    else {
      const prompt = source.querySelector('.claude-suggestion, .sippy-cell-action .cmd-copy-row');
      if (blocking) {
        if (prompt) evidence.appendChild(clone(prompt));
      } else {
        const card = document.createElement('div');
        card.className = 'manual-analysis-card';
        const heading = document.createElement('h3');
        heading.textContent = 'AI Root Cause Analysis';
        const message = document.createElement('p');
        message.textContent = 'Chai AI analysis was not available for this job. Copy the prompt below and paste it into Chai to request an analysis manually.';
        card.append(heading, message);
        if (prompt) card.appendChild(clone(prompt));
        evidence.appendChild(card);
      }
    }
    const attempts = document.getElementById('drawer-attempts');
    attempts.replaceChildren();
    const previous = source.querySelector('.previous-attempts');
    const breakdown = Array.from(source.querySelectorAll('.da-field')).find(field =>
      field.querySelector('.da-label')?.textContent.trim() === 'Per-Attempt Breakdown:');
    if (previous) attempts.appendChild(clone(previous));
    if (breakdown) attempts.appendChild(clone(breakdown));
    document.getElementById('drawer-tab-attempts').hidden = !previous && !breakdown;
    const links = document.getElementById('finding-drawer-links');
    links.replaceChildren();
    if (blocking) {
      const link = document.createElement('a');
      link.href = source.dataset.prowUrl;
      link.textContent = 'View in Prow';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      links.appendChild(link);
    } else {
      const link = source.querySelector('.sippy-cell-action a');
      if (link) links.appendChild(clone(link));
    }
    const full = document.createElement('button');
    full.type = 'button';
    full.className = 'dashboard-button';
    full.textContent = 'Open full report view →';
    full.addEventListener('click', function() {
      drawer.close();
      openSource(source);
    });
    links.appendChild(full);
    // Drafts remain in their original cards so their form styling and controls survive.
    draftCard = Array.from(document.querySelectorAll('.jira-draft')).find(card =>
      JSON.parse(card.dataset.analysisKeys).includes(source.dataset.analysisKey));
    document.getElementById('finding-drawer-bug').hidden = !draftCard;
    selectDrawer('summary');
    drawer.showModal();
    drawer.scrollTop = 0;
  }
  document.getElementById('finding-drawer-close').addEventListener('click', () => drawer.close());
  drawer.addEventListener('close', () => lastTrigger?.focus());
  drawer.addEventListener('click', function(event) {
    if (event.target !== drawer) return;
    const bounds = drawer.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) drawer.close();
  });
  document.getElementById('finding-drawer-analysis').addEventListener('click', function() {
    selectDrawer('analysis');
    document.getElementById('drawer-tab-analysis').focus();
  });
  document.getElementById('finding-drawer-bug').addEventListener('click', function() {
    const card = draftCard;
    drawer.close();
    card.querySelector('.jira-draft-summary').click();
  });

  const rows = Array.from(document.querySelectorAll('[data-finding-source]'));
  // Refresh from source cards, including analyses inserted by a local HTML import.
  rows.forEach(function(row) {
    const source = document.getElementById(row.dataset.findingSource);
    const button = row.querySelector('.finding-job');
    const recommendation = source.querySelector('.da-recommendation > span:last-child');
    row.querySelector('.finding-recommendation').replaceChildren(
      recommendation ? clone(recommendation) : document.createTextNode('—'));
    const recurrence = source.querySelector('.detail-cell-recurrence');
    if (recurrence) row.querySelector('.finding-recurrence').replaceChildren(clone(recurrence));
    if (source.querySelector('.deep-analysis-card')) {
      const badge = document.createElement('span');
      badge.className = 'badge ai-analyzed';
      badge.textContent = 'AI Analyzed';
      row.querySelector('.finding-analysis-status').appendChild(badge);
    }
    row.addEventListener('click', function(event) {
      if (!event.target.closest('a')) openFinding(source, button);
    });
  });
  document.querySelectorAll('[data-findings-section]').forEach(function(section) {
    const sectionRows = Array.from(section.querySelectorAll('[data-finding-source]'));
    const search = section.querySelector('[data-finding-search]');
    const expand = section.querySelector('[data-finding-expand]');
    if (!sectionRows.length) return;
    let filter = 'all';
    let expanded = false;
    function filterFindings() {
      const query = search.value.trim().toLowerCase();
      let count = 0;
      let shown = 0;
      sectionRows.forEach(function(row) {
        const source = document.getElementById(row.dataset.findingSource);
        const fields = [source.dataset.jobName, source.dataset.topology || source.dataset.regtopology,
          source.dataset.version || source.dataset.regversion].join(' ').toLowerCase();
        const match = (filter === 'all' || filter === row.dataset.findingKind) && fields.includes(query);
        if (match) count++;
        row.hidden = !match || (!expanded && count > 5);
        if (!row.hidden) shown++;
      });
      section.querySelector('[data-finding-count]').textContent = 'Showing ' + shown + ' of ' + count + ' jobs';
      section.querySelector('[data-finding-no-results]').hidden = count !== 0;
      expand.hidden = count <= 5;
      expand.textContent = expanded ? 'Show top 5 only' : 'Show all ' + count + ' jobs';
    }
    section.querySelectorAll('[data-finding-filter]').forEach(function(button) {
      button.addEventListener('click', function() {
        filter = button.dataset.findingFilter;
        section.querySelectorAll('[data-finding-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        filterFindings();
      });
    });
    search.addEventListener('input', filterFindings);
    expand.addEventListener('click', function() {
      expanded = !expanded;
      filterFindings();
    });
    filterFindings();
  });

  const target = document.getElementById(window.location.hash.slice(1));
  if (target?.closest('.tab-panel')) {
    if (target.matches('.detail-row, .regression-row')) openSource(target);
    else selectView(target.closest('.tab-panel').id.slice(4));
  }
});
