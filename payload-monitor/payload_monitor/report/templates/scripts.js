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
  const jobRows = document.querySelectorAll('.job-row');
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
  const sectionsCollapsed = { jobs: true, details: true, regressions: true, cr: true, anomalies: true };
  const sectionSelectors = { jobs: '.job-row', details: '.detail-row', regressions: '.regression-row', cr: '.cr-row', anomalies: '.anomaly-row' };
  const sectionLabels = { jobs: ' failing jobs', details: ' failure details', regressions: ' regressions', cr: ' component regressions', anomalies: ' anomalies' };

  // Count rows that pass the current filter (inline style.display is set by filters,
  // collapsed-row class is separate — so style.display !== 'none' means "passes filter")
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
        // Skip rows hidden by filters — don't count them toward the visible limit
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
        const rowValue = row.dataset[group];
        if (!rowValue) continue;
        const rowValues = rowValue.split(' ');
        if (!rowValues.some(v => values.has(v))) {
          return false;
        }
      }
      return true;
    }

    jobRows.forEach(row => {
      row.style.display = isVisible(row) ? '' : 'none';
    });

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
    const visibleCount = countFiltered('.job-row');
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

  // "View Details" links: expand both sections, open the target detail, scroll to it
  document.querySelectorAll('.detail-link').forEach(link => {
    link.addEventListener('click', function(e) {
      e.preventDefault();
      // Expand both sections so the target is visible
      sectionsCollapsed.jobs = false;
      sectionsCollapsed.details = false;
      applyCollapse();
      updateExpandButtons();

      const targetId = this.getAttribute('href').substring(1);
      const detail = document.getElementById(targetId);
      if (detail) {
        detail.open = true;
        detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

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

      // Toggle sort direction — only clear siblings in the same table
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
    const heading = document.querySelector('#tab-payload-health h2:nth-of-type(2)');
    if (heading) heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Build AI Analysis Highlights from patched analysis cards
  (function buildAIHighlights() {
    var container = document.getElementById('ai-highlights-container');
    if (!container) return;

    var analysisCards = document.querySelectorAll('.deep-analysis-card');
    if (analysisCards.length === 0) return;

    var items = [];
    analysisCards.forEach(function(card) {
      var detail = card.closest('details');
      if (!detail) return;

      var detailId = detail.id;
      var summary = detail.querySelector('summary');
      if (!summary) return;

      var badges = summary.querySelectorAll('.badge');
      var topology = '', jobtype = '';
      badges.forEach(function(b) {
        if (b.classList.contains('sno') || b.classList.contains('tna') || b.classList.contains('tnf')) {
          topology = b.textContent.trim();
        } else if (b.classList.contains('blocking') || b.classList.contains('informing')) {
          jobtype = b.textContent.trim();
        }
      });

      var summaryText = summary.textContent.trim();
      var match = summaryText.match(/(?:AI Analyzed)?\s*(.+?)\s*\((\d+\.\d+)\)\s*$/);
      var jobName = match ? match[1].trim() : '';
      var version = match ? match[2] : '';

      var fields = card.querySelectorAll('.da-field');
      var rootCause = '', failureType = '', recommendation = '';
      fields.forEach(function(f) {
        var label = f.querySelector('.da-label');
        if (!label) return;
        var labelText = label.textContent.trim().toLowerCase();
        var typeEl = f.querySelector('.badge.da-type');
        if (labelText.indexOf('root cause') === 0) {
          rootCause = f.textContent.replace(label.textContent, '').trim();
        } else if (labelText.indexOf('failure type') === 0 && typeEl) {
          failureType = typeEl.textContent.trim();
        } else if (labelText.indexOf('recommendation') === 0) {
          recommendation = f.textContent.replace(label.textContent, '').trim();
        }
      });

      items.push({ detailId: detailId, topology: topology, jobtype: jobtype, failureType: failureType, version: version, jobName: jobName, rootCause: rootCause, recommendation: recommendation });
    });

    if (items.length === 0) return;

    // Build DOM elements
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

    items.forEach(function(item) {
      var card = document.createElement('div');
      card.className = 'ai-highlight-item';
      card.title = 'Click to view full analysis';
      card.addEventListener('click', (function(id) {
        return function() {
          var d = document.getElementById(id);
          if (d) { d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
        };
      })(item.detailId));

      var header = document.createElement('div');
      header.className = 'ai-highlight-header';

      if (item.topology) {
        var b1 = document.createElement('span');
        b1.className = 'badge ' + item.topology.toLowerCase();
        b1.textContent = item.topology;
        header.appendChild(b1);
      }
      if (item.jobtype) {
        var b2 = document.createElement('span');
        b2.className = 'badge ' + item.jobtype.toLowerCase();
        b2.textContent = item.jobtype;
        header.appendChild(b2);
      }
      if (item.failureType) {
        var b3 = document.createElement('span');
        b3.className = 'badge da-type';
        b3.textContent = item.failureType;
        header.appendChild(b3);
      }
      if (item.version) {
        var ver = document.createElement('span');
        ver.style.cssText = 'color:var(--text-muted);font-size:12px';
        ver.textContent = item.version;
        header.appendChild(ver);
      }
      if (item.jobName) {
        var name = document.createElement('span');
        name.className = 'job-name';
        name.textContent = item.jobName;
        header.appendChild(name);
      }
      card.appendChild(header);

      if (item.rootCause) {
        var cause = document.createElement('div');
        cause.className = 'ai-highlight-cause';
        var lbl1 = document.createElement('span');
        lbl1.className = 'da-label';
        lbl1.textContent = 'Root Cause:';
        cause.appendChild(lbl1);
        cause.appendChild(document.createTextNode(' ' + item.rootCause));
        card.appendChild(cause);
      }
      if (item.recommendation) {
        var rec = document.createElement('div');
        rec.className = 'ai-highlight-rec';
        var lbl2 = document.createElement('span');
        lbl2.className = 'da-label';
        lbl2.textContent = 'Action:';
        rec.appendChild(lbl2);
        rec.appendChild(document.createTextNode(' ' + item.recommendation));
        card.appendChild(rec);
      }

      wrapper.appendChild(card);
    });

    container.appendChild(wrapper);

    // Hide the plain inline job list only when ALL blocking jobs have AI analysis
    var inlineJobs = container.closest('.fs-section-critical')?.querySelector('.fs-inline-jobs');
    if (inlineJobs) {
        var inlineCount = inlineJobs.querySelectorAll('.fs-blocking-row').length;
        if (items.length >= inlineCount) {
            inlineJobs.style.display = 'none';
        }
    }
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
