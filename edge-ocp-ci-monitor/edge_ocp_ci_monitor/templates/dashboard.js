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
    const actions = document.getElementById('finding-drawer-actions');
    actions.querySelector('#finding-drawer-external')?.remove();
    const externalUrl = blocking ? source.dataset.prowUrl : source.querySelector('.sippy-cell-action a')?.getAttribute('href');
    if (externalUrl) {
      const external = document.createElement('a');
      external.id = 'finding-drawer-external';
      external.className = 'dashboard-button';
      external.textContent = blocking ? 'View in Prow' : 'View in Sippy';
      external.href = externalUrl;
      external.target = '_blank';
      external.rel = 'noopener noreferrer';
      actions.appendChild(external);
    }
    document.getElementById('finding-drawer-full').onclick = function() {
      drawer.close();
      openSource(source);
    };
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
  const target = document.getElementById(window.location.hash.slice(1));
  if (target?.closest('.tab-panel')) {
    if (target.matches('.detail-row, .regression-row')) openSource(target);
    else selectView(target.closest('.tab-panel').id.slice(4));
  }
});

// Move interior column boundaries while keeping the table and details column anchored.
document.addEventListener('DOMContentLoaded', function setupFindingColumnResizing() {
  const minWidth = 64;
  const keyStep = 16;
  document.querySelectorAll('.finding-table[data-resize-columns]').forEach(function(table) {
    const headers = Array.from(table.querySelectorAll('thead th'));
    const detailsHeader = headers.pop();
    const columns = Array.from(table.querySelectorAll('colgroup > col')).slice(0, -2);
    const wrapper = table.parentElement;
    const minTableWidth = parseFloat(getComputedStyle(table).minWidth);
    let widthShares;

    function applyWidths() {
      if (!widthShares || !wrapper.clientWidth) return;
      const tableWidth = Math.max(wrapper.clientWidth, minTableWidth);
      const fixedWidth = detailsHeader.getBoundingClientRect().width + minWidth * headers.length;
      const flexibleWidth = tableWidth - fixedWidth;
      columns.forEach((column, index) => {
        column.style.width = minWidth + widthShares[index] * flexibleWidth + 'px';
      });
    }
    new ResizeObserver(applyWidths).observe(wrapper);

    // Recommendation takes the remaining width. Its left edge is the last resize handle.
    headers.slice(0, -1).forEach(function(header, index) {
      const handle = document.createElement('span');
      handle.className = 'col-resizer';
      handle.tabIndex = 0;
      handle.setAttribute('role', 'separator');
      handle.setAttribute('aria-orientation', 'vertical');
      handle.setAttribute('aria-label', 'Resize ' + header.textContent.trim() + ' column');
      handle.setAttribute('aria-valuemin', minWidth);
      header.appendChild(handle);

      const currentWidth = () => header.getBoundingClientRect().width;
      function describeWidth() {
        const pixels = Math.max(minWidth, Math.round(currentWidth()));
        const nextWidth = headers[index + 1].getBoundingClientRect().width;
        handle.setAttribute('aria-valuemax', Math.max(pixels, Math.round(pixels + nextWidth - minWidth)));
        handle.setAttribute('aria-valuenow', pixels);
        handle.setAttribute('aria-valuetext', pixels + ' pixels');
      }
      describeWidth();
      handle.addEventListener('focus', describeWidth);

      function resize(width) {
        const widths = headers.map(cell => cell.getBoundingClientRect().width);
        const pairWidth = widths[index] + widths[index + 1];
        widths[index] = Math.max(minWidth, Math.min(pairWidth - minWidth, width));
        widths[index + 1] = pairWidth - widths[index];
        const flexibleWidth = widths.reduce((total, columnWidth) => total + columnWidth - minWidth, 0);
        widthShares = widths.slice(0, -1).map(columnWidth => (columnWidth - minWidth) / flexibleWidth);
        applyWidths();
        describeWidth();
      }

      handle.addEventListener('pointerdown', function(event) {
        if (!event.isPrimary || event.button !== 0 || handle.classList.contains('resizing')) return;
        event.preventDefault();
        const {pointerId, clientX} = event;
        const startWidth = currentWidth();
        const drag = new AbortController();
        const options = {signal: drag.signal};
        handle.focus({preventScroll: true});
        handle.classList.add('resizing');
        handle.setPointerCapture(pointerId);

        function stopDragging() {
          drag.abort();
          handle.classList.remove('resizing');
        }
        document.addEventListener('pointermove', function(move) {
          if (move.pointerId !== pointerId) return;
          if (move.buttons === 0) {
            stopDragging();
            return;
          }
          const delta = move.clientX - clientX;
          if (delta || widthShares) resize(startWidth + delta);
        }, options);
        function endDrag(end) {
          if (end.pointerId === pointerId) stopDragging();
        }
        document.addEventListener('pointerup', endDrag, options);
        document.addEventListener('pointercancel', endDrag, options);
        window.addEventListener('blur', stopDragging, options);
      });
      handle.addEventListener('keydown', function(event) {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        resize(currentWidth() + (event.key === 'ArrowLeft' ? -keyStep : keyStep));
      });
    });
  });
});

// Render payload details outside the horizontally scrolling health table.
document.addEventListener('DOMContentLoaded', function setupPayloadTooltips() {
  const dots = document.querySelectorAll('.health-timeline .timeline-dot');
  if (!dots.length) return;
  const tip = document.createElement('div');
  tip.id = 'payload-history-tooltip';
  tip.className = 'payload-history-tooltip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);
  let active;
  let closeTimer;
  let tipHovered = false;

  function hide() {
    clearTimeout(closeTimer);
    tip.hidden = true;
    active = null;
    tipHovered = false;
  }
  function position() {
    if (!active) return;
    const rect = active.dot.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const left = rect.left + rect.width / 2 - width / 2;
    const top = rect.top >= height + 16 ? rect.top - height - 8 : rect.bottom + 8;
    tip.style.left = Math.max(8, Math.min(left, window.innerWidth - width - 8)) + 'px';
    tip.style.top = Math.max(8, Math.min(top, window.innerHeight - height - 8)) + 'px';
  }
  function scheduleHide(trigger) {
    if (!trigger || trigger !== active || trigger.hovered || trigger.focused || tipHovered) return;
    clearTimeout(closeTimer);
    closeTimer = setTimeout(hide, 150);
  }
  dots.forEach(function(dot) {
    const source = dot.querySelector('.tooltip');
    if (!source) return;
    dot.classList.add('payload-tooltip-ready');
    dot.removeAttribute('title');
    dot.setAttribute('aria-describedby', tip.id);
    const trigger = {dot, hovered: false, focused: false};
    function show() {
      clearTimeout(closeTimer);
      active = trigger;
      tip.replaceChildren(...source.cloneNode(true).childNodes);
      tip.hidden = false;
      position();
    }
    dot.addEventListener('mouseenter', () => { trigger.hovered = true; show(); });
    dot.addEventListener('mouseleave', () => { trigger.hovered = false; scheduleHide(trigger); });
    dot.addEventListener('focus', () => { trigger.focused = true; show(); });
    dot.addEventListener('blur', () => { trigger.focused = false; scheduleHide(trigger); });
  });
  tip.addEventListener('mouseenter', () => { tipHovered = true; clearTimeout(closeTimer); });
  tip.addEventListener('mouseleave', () => { tipHovered = false; scheduleHide(active); });
  window.addEventListener('resize', position);
  document.addEventListener('scroll', position, true);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
});
