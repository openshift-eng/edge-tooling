// Theme toggle (light/dark). The initial theme is resolved before first paint
// by the bootstrap script in <head>; this handles explicit user toggles and
// persists the choice so it wins over system preference on the next visit.
document.addEventListener('DOMContentLoaded', function() {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', function() {
    var current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    var next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
  });
});

// Tab switching
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.tab-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
      btn.classList.add('active');
      var panel = document.getElementById('tab-' + btn.dataset.tab);
      if (panel) panel.classList.add('active');
    });
  });
});

// Filter functionality
document.addEventListener('DOMContentLoaded', function() {
  const filterBtns = document.querySelectorAll('.filter-btn');
  const detailRows = document.querySelectorAll('.detail-row');
  const crRows = document.querySelectorAll('.cr-row');
  const regressionRows = document.querySelectorAll('.regression-row');
  const timingRows = document.querySelectorAll('.timing-row');
  const anomalyRows = document.querySelectorAll('.anomaly-row');

  // Track active filters per group
  const activeFilters = {};

  filterBtns.forEach(btn => {
    btn.addEventListener('click', function() {
      const group = this.dataset.group;
      const value = this.dataset.value;

      if (value === 'all') {
        // Deactivate all filters in this group
        document.querySelectorAll(`.filter-btn[data-group="${group}"]`).forEach(b => {
          b.classList.remove('active');
        });
        this.classList.add('active');
        delete activeFilters[group];
      } else {
        // Deactivate "all" button
        document.querySelector(`.filter-btn[data-group="${group}"][data-value="all"]`)?.classList.remove('active');

        // Toggle this filter
        if (this.classList.contains('active')) {
          this.classList.remove('active');
          if (activeFilters[group]) {
            activeFilters[group].delete(value);
            if (activeFilters[group].size === 0) {
              delete activeFilters[group];
              document.querySelector(`.filter-btn[data-group="${group}"][data-value="all"]`)?.classList.add('active');
            }
          }
        } else {
          this.classList.add('active');
          if (!activeFilters[group]) activeFilters[group] = new Set();
          activeFilters[group].add(value);
        }
      }

      applyFilters();
    });
  });

  const INITIAL_VISIBLE = 5;
  const sectionVisibleLimit = { anomalies: 10 };
  const sectionsCollapsed = { details: true, regressions: true, cr: true, anomalies: true };
  const sectionSelectors = { details: '.detail-row', regressions: '.regression-row', cr: '.cr-row', anomalies: '.anomaly-row' };
  const sectionLabels = { details: ' failure details', regressions: ' job findings', cr: ' component regressions', anomalies: ' anomalies' };

  // Count rows that pass the current filter (inline style.display is set by filters,
  // collapsed-row class is separate - so style.display !== 'none' means "passes filter")
  function countFiltered(selector) {
    let count = 0;
    document.querySelectorAll(selector).forEach(row => {
      if (row.style.display !== 'none') count++;
    });
    return count;
  }

  function applyCollapse() {
    Object.entries(sectionSelectors).forEach(([section, selector]) => {
      let visibleIdx = 0;
      document.querySelectorAll(selector).forEach(row => {
        // Skip rows hidden by filters - don't count them toward the visible limit
        if (row.style.display === 'none') {
          row.classList.remove('collapsed-row');
          return;
        }
        visibleIdx++;
        const limit = sectionVisibleLimit[section] || INITIAL_VISIBLE;
        if (sectionsCollapsed[section] && visibleIdx > limit) {
          row.classList.add('collapsed-row');
        } else {
          row.classList.remove('collapsed-row');
        }
      });
    });
  }

  function updateExpandButtons() {
    Object.entries(sectionSelectors).forEach(([section, selector]) => {
      const btn = document.getElementById('expand-' + section + '-btn');
      if (!btn) return;
      const limit = sectionVisibleLimit[section] || INITIAL_VISIBLE;
      const filtered = countFiltered(selector);
      if (filtered <= limit) {
        btn.style.display = 'none';
        return;
      }
      btn.style.display = '';
      btn.textContent = sectionsCollapsed[section]
        ? 'Show all ' + filtered + (sectionLabels[section] || '')
        : 'Show top ' + limit + ' only';
    });
  }

  function applyFilters() {
    function isVisible(row) {
      for (const [group, values] of Object.entries(activeFilters)) {
        if (!(group in row.dataset)) continue;
        const rowValue = row.dataset[group];
        const rowValues = rowValue ? rowValue.split(' ') : [];
        if (!rowValues.some(v => values.has(v))) {
          return false;
        }
      }
      return true;
    }

    detailRows.forEach(row => {
      row.style.display = isVisible(row) ? '' : 'none';
    });

    regressionRows.forEach(row => {
      row.style.display = isVisible(row) ? '' : 'none';
    });

    crRows.forEach(row => {
      row.style.display = isVisible(row) ? '' : 'none';
    });

    const hasVersionFilter = activeFilters.hasOwnProperty('tversion');
    timingRows.forEach(row => {
      if (row.classList.contains('timing-aggregate')) {
        row.style.display = hasVersionFilter ? 'none' : (isVisible(row) ? '' : 'none');
      } else if (row.classList.contains('timing-version-detail')) {
        row.style.display = hasVersionFilter ? (isVisible(row) ? '' : 'none') : 'none';
      } else {
        row.style.display = isVisible(row) ? '' : 'none';
      }
    });

    anomalyRows.forEach(row => {
      row.style.display = isVisible(row) ? '' : 'none';
    });

    applyCollapse();
    updateExpandButtons();

    // Update header count
    const visibleCount = countFiltered('.detail-row');
    const countEl = document.getElementById('visible-count');
    if (countEl) countEl.textContent = visibleCount;
  }

  // Apply initial collapse
  applyCollapse();
  updateExpandButtons();

  // Toggle section expand/collapse (called from onclick)
  window.expandSection = function(section) {
    sectionsCollapsed[section] = !sectionsCollapsed[section];
    applyCollapse();
    updateExpandButtons();
  };

  // Column sorting for any table with sortable headers
  const sortableHeaders = document.querySelectorAll('th.sortable');
  sortableHeaders.forEach(th => {
    th.addEventListener('click', function() {
      const table = this.closest('table');
      if (!table) return;
      const tbody = table.querySelector('tbody');
      if (!tbody) return;
      const col = parseInt(this.dataset.col);
      const rows = Array.from(tbody.querySelectorAll('tr'));

      // Toggle sort direction - only clear siblings in the same table
      const isAsc = this.classList.contains('asc');
      table.querySelectorAll('th.sortable').forEach(h => { h.classList.remove('asc', 'desc'); });
      this.classList.add(isAsc ? 'desc' : 'asc');
      const dir = isAsc ? -1 : 1;

      const numericCell = /^[-+]?\d+(\.\d+)?%?$/;
      rows.sort((a, b) => {
        const aText = (a.children[col]?.textContent || '').trim().toLowerCase();
        const bText = (b.children[col]?.textContent || '').trim().toLowerCase();
        if (numericCell.test(aText) && numericCell.test(bText)) {
          return (parseFloat(aText) - parseFloat(bText)) * dir;
        }
        return aText.localeCompare(bText) * dir;
      });

      rows.forEach((row, index) => {
        row.dataset.rowIndex = String(index + 1);
        tbody.appendChild(row);
      });
      applyCollapse();
      updateExpandButtons();
    });
  });

  // Copy command to clipboard
  window.copyCommand = function(btn) {
    var code = btn.closest('.cmd-copy-row').querySelector('code');
    if (!code) return;
    navigator.clipboard.writeText(code.textContent.trim()).then(function() {
      btn.textContent = 'Copied!';
      btn.classList.add('copied');
      setTimeout(function() {
        btn.textContent = 'Copy';
        btn.classList.remove('copied');
      }, 2000);
    });
  };

  // Click-to-copy on action cell and claude-suggestion code elements
  document.querySelectorAll('.action-cell code, .sippy-cell-action code, .claude-suggestion code, .claude-cmd-popup code').forEach(function(code) {
    code.style.cursor = 'pointer';
    code.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      var text = code.textContent.trim();
      navigator.clipboard.writeText(text).then(function() {
        var tooltip = document.createElement('span');
        tooltip.className = 'copy-tooltip';
        tooltip.textContent = 'Copied!';
        tooltip.style.left = e.clientX + 'px';
        tooltip.style.top = e.clientY + 'px';
        document.body.appendChild(tooltip);
        setTimeout(function() { tooltip.remove(); }, 1500);
      });
    });
  });

  // Scroll to a job row and open it. Matches by job name.
  window.scrollToJob = function(jobName) {
    var rows = document.querySelectorAll('details.detail-row');
    var row = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].dataset.jobName === jobName) { row = rows[i]; break; }
    }
    if (row) {
      if (row.classList.contains('collapsed-row')) {
        sectionsCollapsed.details = false;
        applyCollapse();
        updateExpandButtons();
      }
      row.open = true;
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      row.querySelector('summary').style.background = 'rgba(210,153,34,0.15)';
      setTimeout(function() { row.querySelector('summary').style.background = ''; }, 2000);
    }
  };

  // Draggable column resizers
  document.querySelectorAll('table').forEach(table => {
    const headers = table.querySelectorAll('th');

    // Freeze column widths to current auto-computed sizes, then switch to fixed layout
    function freezeLayout() {
      if (table.dataset.frozen) return;
      headers.forEach(h => { h.style.width = h.offsetWidth + 'px'; });
      table.style.tableLayout = 'fixed';
      table.classList.add('layout-frozen');
      table.dataset.frozen = '1';
    }

    headers.forEach(th => {
      const resizer = document.createElement('div');
      resizer.className = 'col-resizer';
      th.appendChild(resizer);

      let startX, startWidth;

      resizer.addEventListener('mousedown', function(e) {
        e.preventDefault();
        e.stopPropagation();
        freezeLayout();
        startX = e.pageX;
        startWidth = th.offsetWidth;
        resizer.classList.add('resizing');

        function onMouseMove(e) {
          th.style.width = (startWidth + e.pageX - startX) + 'px';
        }
        function onMouseUp() {
          resizer.classList.remove('resizing');
          document.removeEventListener('mousemove', onMouseMove);
          document.removeEventListener('mouseup', onMouseUp);
        }

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
      });
    });
  });

  // Shared failure grids sort and resize independently.
  document.querySelectorAll('.detail-grid-header').forEach(gridHeader => {
    const grid = gridHeader.parentElement;
    const cellClasses = ['detail-cell-job', 'detail-cell-version', 'detail-cell-payload', 'detail-cell-topology', 'detail-cell-type', 'detail-cell-recurrence', 'detail-cell-date'];
    const sortableCols = gridHeader.querySelectorAll('[data-sort-col]');

    sortableCols.forEach(col => {
      col.addEventListener('click', function() {
        const colIdx = parseInt(this.dataset.sortCol);
        const isAsc = this.classList.contains('asc');
        sortableCols.forEach(c => c.classList.remove('asc', 'desc'));
        this.classList.add(isAsc ? 'desc' : 'asc');
        const dir = isAsc ? -1 : 1;

        const rows = Array.from(grid.querySelectorAll(':scope > .failure-row'));
        const cellClass = this.dataset.sortCell || cellClasses[colIdx];
        const numPat = /^[-+]?\d+(\.\d+)?%?$/;

        rows.sort((a, b) => {
          const aCell = a.querySelector(':scope > summary .' + cellClass + ', :scope > .sippy-job-summary .' + cellClass);
          const bCell = b.querySelector(':scope > summary .' + cellClass + ', :scope > .sippy-job-summary .' + cellClass);
          let aText = (aCell?.textContent || '').trim().replace(/\s+/g, ' ').toLowerCase();
          let bText = (bCell?.textContent || '').trim().replace(/\s+/g, ' ').toLowerCase();
          if (numPat.test(aText) && numPat.test(bText)) {
            return (parseFloat(aText) - parseFloat(bText)) * dir;
          }
          return aText.localeCompare(bText) * dir;
        });

        let ref = gridHeader;
        rows.forEach(row => { ref.after(row); ref = row; });
        applyCollapse();
        updateExpandButtons();
      });
    });

    // Detail grid column resizing
    const headerDivs = Array.from(gridHeader.querySelectorAll(':scope > div'));
    let gridColWidths = null;

    function applyGridWidths() {
      const tpl = gridColWidths.map(w => w + 'px').join(' ');
      gridHeader.style.gridTemplateColumns = tpl;
      grid.querySelectorAll('.failure-row > summary, .failure-row > .sippy-job-summary').forEach(s => {
        s.style.gridTemplateColumns = tpl;
      });
    }

    function freezeGridLayout() {
      if (gridColWidths) return;
      gridColWidths = headerDivs.map(d => d.offsetWidth);
      applyGridWidths();
    }

    headerDivs.forEach((div, idx) => {
      const resizer = document.createElement('div');
      resizer.className = 'col-resizer';
      div.appendChild(resizer);
      resizer.addEventListener('click', e => e.stopPropagation());

      let startX;
      resizer.addEventListener('mousedown', function(e) {
        e.preventDefault();
        e.stopPropagation();
        freezeGridLayout();
        startX = e.pageX;
        const startWidth = gridColWidths[idx];
        resizer.classList.add('resizing');

        function onMouseMove(e) {
          gridColWidths[idx] = Math.max(40, startWidth + e.pageX - startX);
          applyGridWidths();
        }
        function onMouseUp() {
          resizer.classList.remove('resizing');
          document.removeEventListener('mousemove', onMouseMove);
          document.removeEventListener('mouseup', onMouseUp);
        }
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
      });
    });
  });

  // Jump to failures table with filters pre-applied
  window.jumpToFailures = function(version, jobtype) {
    // Reset all filters first
    Object.keys(activeFilters).forEach(k => delete activeFilters[k]);
    filterBtns.forEach(b => {
      if (b.dataset.value === 'all') {
        b.classList.add('active');
      } else {
        b.classList.remove('active');
      }
    });

    // Activate version filter
    if (version) {
      const allBtn = document.querySelector('.filter-btn[data-group="version"][data-value="all"]');
      const verBtn = document.querySelector('.filter-btn[data-group="version"][data-value="' + version + '"]');
      if (allBtn) allBtn.classList.remove('active');
      if (verBtn) verBtn.classList.add('active');
      activeFilters.version = new Set([version]);
    }

    // Activate job type filter
    if (jobtype) {
      const allBtn = document.querySelector('.filter-btn[data-group="jobtype"][data-value="all"]');
      const typeBtn = document.querySelector('.filter-btn[data-group="jobtype"][data-value="' + jobtype + '"]');
      if (allBtn) allBtn.classList.remove('active');
      if (typeBtn) typeBtn.classList.add('active');
      activeFilters.jobtype = new Set([jobtype]);
    }

    applyFilters();

    // Scroll to the failures heading
    const heading = document.getElementById('section-edge-failures');
    if (heading) heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Blocking and Sippy findings share the same AI summary card and interaction.
  (function buildAIHighlights() {
    function readAnalysis(card, detail) {
      var item = {
        detail: detail,
        topology: detail.dataset.topology || '',
        jobtype: detail.dataset.jobtype || '',
        version: detail.dataset.version || '',
        jobName: detail.dataset.jobName || '',
        rootCause: '', failureType: '', recommendation: ''
      };
      var jobName = card.querySelector('.da-job-name');
      if (jobName) item.jobName = jobName.textContent.trim();
      card.querySelectorAll('.da-field').forEach(function(field) {
        var label = field.querySelector('.da-label');
        if (!label) return;
        var text = label.textContent.trim().toLowerCase();
        var value = field.textContent.replace(label.textContent, '').trim();
        if (text.indexOf('root cause') === 0) item.rootCause = value;
        if (text.indexOf('failure type') === 0) item.failureType = value;
        if (text.indexOf('recommendation') === 0) item.recommendation = value;
      });
      return item;
    }

    function makeHighlight(item) {
      var card = document.createElement('div');
      card.className = 'fs-job-card ai-highlight-item';
      card.title = 'Click to view full analysis';
      card.setAttribute('role', 'button');
      card.tabIndex = 0;
      card.setAttribute('aria-controls', item.detail.id);
      card.setAttribute('aria-expanded', String(!!item.detail.open));
      function openAnalysis() {
        var isSippy = item.detail.classList.contains('regression-row');
        if (item.detail.classList.contains('detail-row') && item.detail.style.display === 'none') {
          window.jumpToFailures(item.version, item.jobtype);
        }
        if (isSippy && item.detail.style.display === 'none') {
          ['regtopology', 'regversion'].forEach(function(group) {
            delete activeFilters[group];
            document.querySelectorAll('.filter-btn[data-group="' + group + '"]').forEach(function(btn) {
              btn.classList.toggle('active', btn.dataset.value === 'all');
            });
          });
          applyFilters();
        }
        if (item.detail.classList.contains('collapsed-row')) {
          sectionsCollapsed[isSippy ? 'regressions' : 'details'] = false;
          applyCollapse();
          updateExpandButtons();
        }
        item.detail.open = true;
        item.detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      card.addEventListener('click', function(event) {
        if (!event.target.closest('a, button')) openAnalysis();
      });
      card.addEventListener('keydown', function(event) {
        if (event.target !== card) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openAnalysis();
        }
      });
      item.detail.addEventListener('toggle', function() {
        card.setAttribute('aria-expanded', String(item.detail.open));
      });

      var header = document.createElement('div');
      header.className = 'ai-highlight-header';
      var name = document.createElement('span');
      name.className = 'job-name';
      name.textContent = item.jobName;
      header.appendChild(name);
      [[item.topology, item.topology.toLowerCase()],
       [item.jobtype, item.jobtype.toLowerCase()],
       [item.failureType, 'da-type']].forEach(function(badge) {
        if (!badge[0]) return;
        var span = document.createElement('span');
        span.className = 'badge ' + badge[1];
        span.textContent = badge[0];
        header.appendChild(span);
      });
      if (item.version) {
        var version = document.createElement('span');
        version.className = 'fs-job-version';
        version.textContent = item.version;
        header.appendChild(version);
      }
      card.appendChild(header);
      if (item.rates) card.appendChild(item.rates);
      if (item.metadata) card.appendChild(item.metadata);
      [[item.rootCause, 'ai-highlight-cause', 'Root Cause:'],
       [item.recommendation, 'ai-highlight-rec', 'Action:']].forEach(function(field) {
        if (!field[0]) return;
        var div = document.createElement('div');
        div.className = field[1];
        var label = document.createElement('span');
        label.className = 'da-label da-label-highlight';
        label.textContent = field[2];
        div.appendChild(label);
        div.appendChild(document.createTextNode(' ' + field[0]));
        card.appendChild(div);
      });
      return card;
    }

    var container = document.getElementById('ai-highlights-container');
    if (container) {
      var items = [];
      document.querySelectorAll('details.detail-row[data-jobtype="blocking"] .deep-analysis-card').forEach(function(card) {
        items.push(readAnalysis(card, card.closest('details.detail-row')));
      });
      if (items.length) {
        var wrapper = document.createElement('div');
        wrapper.className = 'finding ai-highlights';
        var title = document.createElement('div');
        title.className = 'ai-highlights-title';
        var dot = document.createElement('span');
        dot.className = 'ai-dot';
        dot.textContent = '\u25CF';
        title.appendChild(dot);
        var strong = document.createElement('strong');
        strong.textContent = 'AI Root Cause Analysis \u2014 ' + items.length + ' blocking failure' + (items.length !== 1 ? 's' : '') + ' analyzed';
        title.appendChild(strong);
        wrapper.appendChild(title);
        items.forEach(function(item) { wrapper.appendChild(makeHighlight(item)); });
        container.appendChild(wrapper);

        // A Sippy or informing analysis must never hide an unanalyzed blocker.
        var inlineJobs = container.closest('.fs-section-critical').querySelector('.fs-inline-jobs');
        var analyzedNames = new Set(items.map(function(item) { return item.jobName; }));
        if (inlineJobs && Array.from(inlineJobs.querySelectorAll('.fs-blocking-name')).every(function(name) {
          return analyzedNames.has(name.textContent.trim());
        })) inlineJobs.style.display = 'none';
      }
    }

    document.querySelectorAll('.fs-sippy-list > li').forEach(function(row) {
      var detail = document.getElementById(row.dataset.analysisId);
      var analysis = detail && detail.querySelector('.deep-analysis-card');
      if (!analysis) return;
      var name = detail.querySelector('.detail-cell-job');
      if (name && !name.querySelector('.ai-analyzed')) {
        var badge = document.createElement('span');
        badge.className = 'badge ai-analyzed';
        badge.textContent = 'AI Analyzed';
        name.appendChild(badge);
      }
      var item = readAnalysis(analysis, detail);
      item.rates = row.querySelector('.fs-sippy-rates').cloneNode(true);
      item.metadata = row.querySelector('.fs-sippy-details').cloneNode(true);
      item.metadata.querySelectorAll('.badge, .fs-job-version').forEach(function(el) { el.remove(); });
      var highlight = makeHighlight(item);
      row.insertBefore(highlight, row.firstChild);
      // Preserve the fallback summary for findings with no AI result.
      row.querySelector('.fs-sippy-summary').hidden = true;
    });
  })();

  // Copy full bug description to clipboard
  document.querySelectorAll('.copy-desc-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var text = this.dataset.description;
      // Decode HTML entities (safe: off-screen textarea, never rendered)
      var tmp = document.createElement('textarea');
      tmp.innerHTML = text;
      navigator.clipboard.writeText(tmp.value).then(function() {
        var original = btn.textContent;
        btn.textContent = 'Copied!';
        btn.style.color = 'var(--green)';
        btn.style.borderColor = 'var(--green)';
        setTimeout(function() {
          btn.textContent = original;
          btn.style.color = '';
          btn.style.borderColor = '';
        }, 2000);
      });
    });
  });
});

/* SVG chart tooltips (data-tip attribute on .svg-tip circles) */
(function() {
  var tip = document.createElement('div');
  tip.className = 'svg-tooltip';
  tip.style.display = 'none';
  document.body.appendChild(tip);
  document.addEventListener('mouseover', function(e) {
    var el = e.target.closest('.svg-tip');
    if (!el || !el.dataset.tip) return;
    tip.textContent = el.dataset.tip;
    tip.style.display = '';
    var r = el.getBoundingClientRect();
    var x = r.left + r.width / 2 - tip.offsetWidth / 2;
    var y = r.top - tip.offsetHeight - 6;
    x = Math.max(4, Math.min(x, window.innerWidth - tip.offsetWidth - 4));
    y = Math.max(4, Math.min(y, window.innerHeight - tip.offsetHeight - 4));
    tip.style.left = (x + window.scrollX) + 'px';
    tip.style.top = (y + window.scrollY) + 'px';
  });
  document.addEventListener('mouseout', function(e) {
    if (e.target.closest('.svg-tip')) tip.style.display = 'none';
  });
})();
