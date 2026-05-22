// FlowForge Dashboard — app.js
// Vanilla JS, no frameworks, no dependencies.
// Consumes Fase 2 API: POST /api/generate, GET /api/accounts, GET /api/events

(function () {
  'use strict';

  // ── State ────────────────────────────────────────────
  var state = {
    batchId: null,
    running: false,
    accounts: [],
    results: [],
    progress: { done: 0, total: 0 },
    eventSource: null,
    lastOutputFolder: '',
    lastModel: 'NARWHAL',
    references: [],        // {name: str, data_b64: str} — imagenes de referencia
    projectRefsCache: {},  // projectName -> references[] — cache por proyecto
    lastReferenceImages: null  // preservado para retry
  };

  // ── DOM refs ─────────────────────────────────────────
  var $ = function (id) { return document.getElementById(id); };
  var accountsGrid = $('accountsGrid');
  var promptsInput = $('promptsInput');
  var outputFolder = $('outputFolder');
  var modelSelect = $('modelSelect');
  var generateBtn = $('generateBtn');
  var progressSection = $('progressSection');
  var progressFill = $('progressFill');
  var progressLabel = $('progressLabel');
  var resultsSection = $('resultsSection');
  var resultsList = $('resultsList');
  var toast = $('toast');
  var badgeDot = $('badgeDot');
  var badgeText = $('badgeText');
  var retryBtn = $('retryBtn');

  // ── Toast ────────────────────────────────────────────
  function showToast(msg, duration) {
    duration = duration || 4000;
    toast.textContent = msg;
    toast.classList.add('visible');
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(function () {
      toast.classList.remove('visible');
    }, duration);
  }

  // ── Account Polling ──────────────────────────────────
  function pollAccounts() {
    fetch('/api/accounts')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.accounts = data.accounts || [];
        renderAccounts();
      })
      .catch(function () {
        // Silent — server might not be running yet
      });
  }

  function renderAccounts() {
    var accounts = state.accounts;
    var onlineCount = 0;

    if (accounts.length === 0) {
      accountsGrid.innerHTML = '<div class="empty-state"><div class="empty-icon">&#128187;</div><div class="empty-title">Esperando extensiones de Flow</div><div class="empty-hint">Abre <code>labs.google/fx/tools/flow</code> en Chrome con la extensi&oacute;n cargada</div></div>';
    } else {
      var html = '';
      for (var i = 0; i < accounts.length; i++) {
        var a = accounts[i];
        if (a.connected) onlineCount++;
        var cls = a.connected ? 'connected' : '';
        var checked = a.connected ? ' checked' : '';
        var initial = (a.email || a.hash || '?').charAt(0).toUpperCase();
        var email = esc(a.email || 'Sin email');
        html += '<div class="account-card ' + cls + '">' +
          '<label>' +
          '<input type="checkbox" class="acct-cb" value="' + esc(a.hash) + '"' + checked + ' onchange="toggleAccount()">' +
          '<div class="account-avatar">' + initial + '</div>' +
          '<div class="account-info">' +
          '<span class="email">' + email + '</span>' +
          '<span class="hash">' + esc(a.hash) + '</span>' +
          '</div>' +
          '<span class="account-status"></span>' +
          '</label>' +
          '</div>';
      }
      accountsGrid.innerHTML = html;
    }

    // Update badge
    if (onlineCount > 0) {
      badgeDot.className = 'dot online';
      badgeText.textContent = onlineCount + ' cuenta' + (onlineCount !== 1 ? 's' : '') + ' conectada' + (onlineCount !== 1 ? 's' : '');
    } else {
      badgeDot.className = 'dot';
      badgeText.textContent = accounts.length + ' cuenta' + (accounts.length !== 1 ? 's' : '') + ' (sin conexi\u00f3n WS)';
    }
  }

  function esc(str) {
    str = '' + str;
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Account Selector ─────────────────────────────────
  function getSelectedAccounts() {
    var cbs = document.querySelectorAll('.acct-cb:checked');
    var hashes = [];
    for (var i = 0; i < cbs.length; i++) {
      hashes.push(cbs[i].value);
    }
    return hashes;
  }

  function toggleAccount() {
    // No-op — checkbox already toggled, just visual
  }

  // ── Batch Submission ─────────────────────────────────
  function startBatch() {
    var prompts = promptsInput.value.trim();
    var folder = outputFolder.value.trim();

    if (!prompts) {
      showToast('Escrib\u00ed al menos una escena.');
      return;
    }
    if (!folder) {
      showToast('Especific\u00e1 la carpeta de salida.');
      return;
    }

    // Capturar referencias antes de limpiar estado
    var refImages = state.references.length > 0 ? state.references.map(function (r) { return r.data_b64; }) : undefined;
    state.lastReferenceImages = refImages;  // preservar para retry

    // Limpiar referencias del batch anterior al arrancar uno nuevo
    state.references = [];
    renderReferencePreviews();

    // Disable UI
    state.running = true;
    state.results = [];
    state.progress = { done: 0, total: 0 };
    state.lastOutputFolder = folder;
    state.lastModel = modelSelect.value;
    generateBtn.disabled = true;
    generateBtn.textContent = 'Iniciando...';
    retryBtn.style.display = 'none';
    resultsList.innerHTML = '';
    resultsSection.classList.remove('visible');
    progressSection.classList.remove('visible');

    // Close previous SSE
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }

    fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompts: prompts,
        output_folder: folder,
        model: modelSelect.value,
        concurrency: parseInt($('concurrencySlider').value) || 2,
        project: currentProject || '',
        accounts: getSelectedAccounts(),
        reference_images: refImages
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          showToast(data.error || 'Error al iniciar batch');
          resetUI();
          return;
        }
        state.batchId = data.batch_id;
        state.progress.total = data.total;
        progressSection.classList.add('visible');
        resultsSection.classList.add('visible');
        generateBtn.textContent = 'Generando...';
        renderProgress();

        // Pre-populate results with queued placeholders
        var promptsList = prompts.split('\n');
        for (var i = 0; i < promptsList.length; i++) {
          var p = promptsList[i].trim();
          if (p) {
            state.results.push({ index: i + 1, prompt: p, status: 'queued' });
          }
        }
        renderResults();

        // Connect SSE
        connectSSE(state.batchId);
      })
      .catch(function (e) {
        showToast('Error de conexi\u00f3n: ' + e.message);
        resetUI();
      });
  }

  function resetUI() {
    state.running = false;
    state.batchId = null;
    generateBtn.disabled = false;
    generateBtn.textContent = 'Generar';
    retryBtn.style.display = 'none';
    progressSection.classList.remove('visible');
    state.references = [];
    renderReferencePreviews();
  }

  // ── SSE Connection ───────────────────────────────────
  function connectSSE(batchId) {
    var url = '/api/events?batch=' + encodeURIComponent(batchId);
    var es = new EventSource(url);
    state.eventSource = es;
    var receivedAny = false;

    es.onopen = function () {
      // Connected
    };

    es.onmessage = function (event) {
      try {
        var data = JSON.parse(event.data);
        receivedAny = true;
        handleSSEEvent(data);
      } catch (e) {
        // Ignore non-JSON (keepalive comments)
      }
    };

    es.onerror = function () {
      if (!receivedAny && state.running) {
        showToast('Conectando al servidor SSE...', 4000);
      } else if (!state.running) {
        es.close();
        state.eventSource = null;
      }
    };
  }

  function handleSSEEvent(data) {
    // Ignore events from stale batches
    if (data.batch_id && data.batch_id !== state.batchId) { return; }

    var type = data.type;

    if (type === 'progress') {
      state.progress.done = data.done;
      state.progress.total = data.total;
      renderProgress();

    } else if (type === 'item_status') {
      // Update result row status color
      for (var i = 0; i < state.results.length; i++) {
        if (state.results[i].index === data.index) {
          state.results[i].status = data.status;
          break;
        }
      }
      renderResults();

    } else if (type === 'item_result') {
      // Full result — update or replace
      var found = false;
      for (var j = 0; j < state.results.length; j++) {
        if (state.results[j].index === data.index) {
          state.results[j] = data;
          found = true;
          break;
        }
      }
      if (!found) {
        state.results.push(data);
      }
      state.results.sort(function (a, b) { return a.index - b.index; });
      renderResults();

    } else if (type === 'complete') {
      // Batch finished
      state.running = false;
      generateBtn.disabled = false;
      generateBtn.textContent = 'Generar';
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }

      // Use final results if provided
      if (data.results && data.results.length > 0) {
        state.results = data.results;
        state.results.sort(function (a, b) { return a.index - b.index; });
        renderResults();
      }

      var doneCount = countByStatus('done');
      var failCount = countByStatus('failed') + countByStatus('error') + countByStatus('cancelled');
      showToast('Batch completo: ' + doneCount + ' OK, ' + failCount + ' fallaron.');
      showRetryIfNeeded();

    } else if (type === 'error') {
      showToast('Error: ' + (data.message || 'desconocido'));
      state.running = false;
      generateBtn.disabled = false;
      generateBtn.textContent = 'Generar';
      showRetryIfNeeded();
    }
  }

  function showRetryIfNeeded() {
    var failCount = countByStatus('failed') + countByStatus('error') + countByStatus('cancelled');
    if (failCount > 0) {
      retryBtn.style.display = 'inline-block';
      retryBtn.textContent = 'Reintentar ' + failCount + ' fallidas';
    } else {
      retryBtn.style.display = 'none';
    }
  }

  function retryFailed() {
    var failed = [];
    for (var i = 0; i < state.results.length; i++) {
      var r = state.results[i];
      if (r.status === 'failed' || r.status === 'error' || r.status === 'cancelled') {
        failed.push(r.prompt);
      }
    }
    if (failed.length === 0) {
      showToast('No hay prompts fallidos para reintentar.');
      return;
    }
    promptsInput.value = failed.join('\n');
    outputFolder.value = state.lastOutputFolder;
    modelSelect.value = state.lastModel;
    retryBtn.style.display = 'none';
    // Restaurar referencias para el retry
    if (state.lastReferenceImages) {
      state.references = state.lastReferenceImages.map(function (b64, i) {
        return { name: 'ref_' + (i + 1) + '.png', data_b64: b64 };
      });
      renderReferencePreviews();
    }
    startBatch();
  }

  // ── Reference Images ──────────────────────────────────
  function onReferenceFilesChange(event) {
    console.log('[Ref] onReferenceFilesChange called');
    var files = event.target.files;
    var fileCount = files ? files.length : 0;
    console.log('[Ref] files count:', fileCount);
    if (!files || fileCount === 0) return;
    var maxSize = 5 * 1024 * 1024; // 5MB
    for (var i = 0; i < fileCount; i++) {
      console.log('[Ref] file:', files[i].name, files[i].size, 'bytes');
      if (files[i].size > maxSize) {
        showToast('La imagen excede 5MB: ' + files[i].name);
        event.target.value = '';
        return;
      }
    }
    // Leer archivos y convertirlos a base64
    var loadedCount = 0;
    for (var j = 0; j < fileCount; j++) {
      (function (file) {
        var reader = new FileReader();
        reader.onload = function (e) {
          // Extraer base64 crudo (sin prefix data:image/...;base64,)
          var result = e.target.result;
          var rawBase64 = result.split(',')[1] || result;
          state.references.push({ name: file.name, data_b64: rawBase64 });
          console.log('[Ref] loaded:', file.name, 'base64 length:', rawBase64.length);
          loadedCount++;
          console.log('[Ref] loadedCount=' + loadedCount + ' fileCount=' + fileCount + ' match=' + (loadedCount === fileCount));
          if (loadedCount === fileCount) {
            console.log('[Ref] all files loaded, rendering previews');
            showToast(fileCount + ' imagen(es) de referencia cargada(s)');
            renderReferencePreviews();
            validateForm();
          }
        };
        reader.onerror = function (e) {
          console.error('[Ref] FileReader error:', e);
          showToast('Error al leer: ' + file.name);
        };
        reader.readAsDataURL(file);
      })(files[j]);
    }
    // Reset input para permitir re-seleccionar el mismo archivo
    event.target.value = '';
  }

  function removeReference(index) {
    state.references.splice(index, 1);
    renderReferencePreviews();
    validateForm();
  }

  function renderReferencePreviews() {
    var previewsEl = $('refPreviews');
    var countEl = $('refCount');
    var hintEl = $('refHint');
    var sectionEl = $('refSection');
    console.log('[Ref] renderReferencePreviews — refs:', state.references.length,
      'previewsEl:', !!previewsEl, 'countEl:', !!countEl, 'hintEl:', !!hintEl);
    if (!previewsEl || !countEl || !hintEl) return;

    if (state.references.length === 0) {
      previewsEl.style.display = 'none';
      hintEl.style.display = '';
      countEl.style.display = 'none';
      previewsEl.innerHTML = '';
      return;
    }
    previewsEl.style.display = 'flex';
    hintEl.style.display = 'none';
    countEl.style.display = '';
    countEl.textContent = state.references.length;

    var html = '';
    for (var i = 0; i < state.references.length; i++) {
      var ref = state.references[i];
      var mime = ref.name.match(/\.(png|jpg|jpeg|webp)$/i);
      var mimeType = mime ? 'image/' + mime[1].toLowerCase().replace('jpg', 'jpeg') : 'image/png';
      html += '<div class="ref-thumb-wrap">' +
        '<img src="data:' + mimeType + ';base64,' + ref.data_b64 + '" class="ref-thumb" title="' + esc(ref.name) + '">' +
        '<span class="ref-thumb-remove" onclick="removeReference(' + i + ')">&times;</span>' +
        '</div>';
    }
    previewsEl.innerHTML = html;
  }

  function loadProjectReferences(projectName) {
    if (!projectName) return;
    // Usar cache si existe
    if (state.projectRefsCache.hasOwnProperty(projectName)) {
      state.references = state.projectRefsCache[projectName];
      renderReferencePreviews();
      validateForm();
      return;
    }
    // Fetch del endpoint
    fetch('/api/projects/' + encodeURIComponent(projectName) + '/references')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var refs = (data.ok && data.images) ? data.images : [];
        state.references = refs;
        state.projectRefsCache[projectName] = refs;
        renderReferencePreviews();
        validateForm();
      })
      .catch(function () {
        state.references = [];
        state.projectRefsCache[projectName] = [];
        renderReferencePreviews();
        validateForm();
      });
  }

  function countByStatus(status) {
    var n = 0;
    for (var i = 0; i < state.results.length; i++) {
      if (state.results[i].status === status) n++;
    }
    return n;
  }

  // ── Renderers ────────────────────────────────────────
  function renderProgress() {
    var done = state.progress.done;
    var total = state.progress.total;
    var pct = total > 0 ? Math.round((done / total) * 100) : 0;
    progressFill.style.width = pct + '%';
    progressLabel.textContent = done + ' / ' + total + ' completado';
    var pctEl = document.getElementById('progressPct');
    if (pctEl) pctEl.textContent = pct + '%';
  }

  function renderResults() {
    var html = '';
    var iconMap = {
      done: '\u2713',
      generating: '\u26a1',
      queued: '\u23f3',
      failed: '\u2717',
      cancelled: '\u2717',
      error: '\u2717'
    };
    for (var i = 0; i < state.results.length; i++) {
      var r = state.results[i];
      var status = r.status || 'queued';
      var icon = iconMap[status] || '\u23f3';
      var outputPath = r.output_path || '';
      var fileName = outputPath.split('\\').pop().split('/').pop();
      var meta = fileName || (status === 'done' ? 'Guardado' : 'En cola');
      if (status === 'failed' || status === 'error') meta = 'Error';
      if (status === 'cancelled') meta = 'Cancelado';
      html += '<div class="result-card ' + status + '">' +
        '<span class="result-icon">' + icon + '</span>' +
        '<div class="result-body">' +
        '<div class="result-index">#' + r.index + '</div>' +
        '<div class="result-prompt">' + esc(r.prompt || '') + '</div>' +
        ((status === 'failed' || status === 'error') && r.error ? '<div class="result-error">' + esc(r.error) + '</div>' : '') +
        '<div class="result-meta">' + esc(meta) + '</div>' +
        '</div>' +
        '</div>';
    }
    resultsList.innerHTML = html;

    // Auto-scroll to bottom
    resultsList.scrollTop = resultsList.scrollHeight;
  }

  // ── JSON File Loader ──────────────────────────────────
  function loadPromptsFromJSON(event) {
    var file = event.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function (e) {
      try {
        var data = JSON.parse(e.target.result);
        var items = Array.isArray(data) ? data : (data.prompts || data.items || []);
        var prompts = [];
        for (var i = 0; i < items.length; i++) {
          var item = items[i];
          // Extract image_prompt (from Text Splitter format)
          var prompt = item.image_prompt || item.prompt || item.text || '';
          if (prompt && typeof prompt === 'string' && prompt.trim()) {
            prompts.push(prompt.trim());
          }
        }
        if (prompts.length === 0) {
          showToast('No se encontraron prompts en el JSON (buscados: image_prompt, prompt, text)');
          return;
        }
        promptsInput.value = prompts.join('\n');
        validateForm();
        var label = document.getElementById('jsonFileName');
        label.textContent = file.name + ' (' + prompts.length + ' prompts)';
        showToast('Cargados ' + prompts.length + ' prompts de ' + file.name);
      } catch (err) {
        showToast('Error al leer JSON: ' + err.message);
      }
    };
    reader.readAsText(file);
    // Reset input so same file can be re-selected
    event.target.value = '';
  }

  // ── Projects ──────────────────────────────────────────
  var projectGrid = $('projectGrid');
  var projectStats = $('projectStats');
  var currentProject = null;

  function pollProjects() {
    fetch('/api/projects')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var projects = data.projects || [];
        renderProjectCards(projects);
      })
      .catch(function () {});
  }

  function renderProjectCards(projects) {
    var html = '';
    for (var i = 0; i < projects.length; i++) {
      var p = projects[i];
      var name = p._name || p.name || '';
      var title = p.title || '';
      var stats = p.stats || {};
      var done = stats.images_generated || 0;
      var total = stats.prompts_total || 0;
      var pct = total > 0 ? Math.round((done / total) * 100) : 0;
      var status = p.status || 'planning';
      var color = { planning: 'var(--text-muted)', generating: 'var(--warning)', editing: 'var(--accent)', complete: 'var(--success)' }[status] || 'var(--text-muted)';
      var sel = (currentProject === name) ? ' selected' : '';
      html += '<div class="project-card' + sel + '" onclick="selectProjectCard(\'' + esc(name) + '\')">' +
        '<div class="proj-status status-' + status + '" title="' + status + '"></div>' +
        '<div class="thumb"><span class="no-thumb">&#128193;</span></div>' +
        '<div class="proj-name">' + esc(title || name) + '</div>' +
        '<div class="proj-title">' + esc(name) + '</div>' +
        '<div class="proj-stats">' + done + '/' + total + ' imgs</div>' +
        '<div class="proj-bar"><div class="fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
        '</div>';
    }
    projectGrid.innerHTML = html || '<span class="no-accounts">No hay proyectos.</span>';
    document.getElementById('projectCount').textContent = '(' + projects.length + ')';
    updateSelectedPill();
  }

  function updateSelectedPill() {
    var pill = document.getElementById('selectedProject');
    if (!currentProject) {
      pill.innerHTML = 'Ning\u00fan proyecto seleccionado';
      return;
    }
    // Find project data
    fetch('/api/projects/' + encodeURIComponent(currentProject))
      .then(function (r) { return r.json(); })
      .then(function (proj) {
        var done = (proj.stats || {}).images_generated || 0;
        var total = (proj.stats || {}).prompts_total || 0;
        var status = proj.status || 'planning';
        var emoji = { planning: '\u26aa', generating: '\u{1f7e1}', editing: '\u{1f534}', complete: '\u{1f7e2}' }[status] || '\u26aa';
        pill.innerHTML = emoji + ' <b>' + esc(proj.title || currentProject) + '</b> &mdash; ' + done + '/' + total + ' imgs &middot; <a href=\"#\" onclick=\"currentProject=null;updateSelectedPill();pollProjects();event.preventDefault()\" style=\"color:var(--accent)\">quitar</a>';
      })
      .catch(function () {
        pill.innerHTML = '<b>' + esc(currentProject) + '</b> (error al cargar)';
      });
  }

  function toggleProjects() {
    var grid = projectGrid;
    var toggle = document.getElementById('projectToggle');
    if (grid.style.display === 'none' || !grid.style.display) {
      grid.style.display = 'grid';
      toggle.textContent = '\u25bc';
    } else {
      grid.style.display = 'none';
      toggle.textContent = '\u25b6';
    }
  }

  function selectProjectCard(name) {
    currentProject = name;
    // Collapse the grid after selection
    projectGrid.style.display = 'none';
    document.getElementById('projectToggle').textContent = '\u25b6';
    // Re-render cards to show selected state
    pollProjects();
    // Load project details
    fetch('/api/projects/' + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (proj) {
        // Auto-set output folder
        var imagesRel = (proj.files || {}).images_dir || 'imagenes';
        var imagesDir = (proj._dir || '') + '/' + imagesRel;
        outputFolder.value = imagesDir.replace(/\\/g, '/');
        validateForm();
        // Auto-set concurrency from project if saved
        var conc = proj.concurrency;
        if (conc && !isNaN(conc)) {
          var slider = $('concurrencySlider');
          slider.value = Math.max(1, Math.min(20, parseInt(conc)));
          $('concurrencyLabel').textContent = slider.value;
        }
        // Load prompts file
        var promptsFile = (proj.files || {}).prompts || '';
        if (promptsFile) {
          var promptsPath = (proj._dir || '') + '/' + promptsFile;
          loadPromptsFromPath(promptsPath);
        }
        // Cargar referencias del proyecto
        loadProjectReferences(name);
      })
      .catch(function () {});
  }

  function migrateProjects() {
    var btn = document.getElementById('migrateBtn');
    btn.textContent = 'Guardando...';
    btn.disabled = true;
    fetch('/api/projects/migrate', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        showToast('Guardados ' + data.migrated + ' proyectos de ' + data.total);
        btn.textContent = 'Guardar todo';
        btn.disabled = false;
      })
      .catch(function () {
        showToast('Error al guardar');
        btn.textContent = 'Guardar todo';
        btn.disabled = false;
      });
  }

  function loadPromptsFromPath(path) {
    fetch('/api/read-file?path=' + encodeURIComponent(path))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.content) {
          try {
            var items = JSON.parse(data.content);
            var prompts = [];
            for (var i = 0; i < items.length; i++) {
              var p = items[i].image_prompt || items[i].prompt || items[i].text || '';
              if (p && typeof p === 'string' && p.trim()) {
                prompts.push(p.trim());
              }
            }
            if (prompts.length > 0) {
              promptsInput.value = prompts.join('\n');
              validateForm();
              showToast('Cargados ' + prompts.length + ' prompts del proyecto');
            }
          } catch (e) {
            // Not JSON — use raw text
          }
        }
      })
      .catch(function () {});
  }

  function showNewProject() {
    document.getElementById('newProjectForm').style.display = 'flex';
    document.getElementById('newProjectName').focus();
  }

  function cancelNewProject() {
    document.getElementById('newProjectForm').style.display = 'none';
    document.getElementById('newProjectName').value = '';
    document.getElementById('newProjectTitle').value = '';
  }

  function createProject() {
    var name = document.getElementById('newProjectName').value.trim();
    var title = document.getElementById('newProjectTitle').value.trim();
    if (!name) { showToast('El nombre es obligatorio'); return; }
    fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, title: title })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          showToast('Proyecto creado: ' + name);
          cancelNewProject();
          pollProjects();
          projectSelect.value = name;
          currentProject = name;
          selectProject();
        } else {
          showToast('Error: ' + (data.error || 'desconocido'));
        }
      })
      .catch(function () { showToast('Error de conexion'); });
  }

  // ── Validation ───────────────────────────────────────
  function validateForm() {
    var hasPrompts = promptsInput.value.trim().length > 0;
    var hasFolder = outputFolder.value.trim().length > 0;
    if (hasPrompts && hasFolder) {
      generateBtn.classList.remove('invalid');
      generateBtn.disabled = false;
    } else {
      generateBtn.classList.add('invalid');
      generateBtn.disabled = true;
    }
  }

  // ── Init ─────────────────────────────────────────────
  function init() {
    pollAccounts();
    setInterval(pollAccounts, 5000);
    pollProjects();
    setInterval(pollProjects, 30000);
    validateForm();
    promptsInput.addEventListener('input', validateForm);
    outputFolder.addEventListener('input', validateForm);
  }

  // Expose globally (called from onclick)
  window.startBatch = startBatch;
  window.retryFailed = retryFailed;
  window.toggleAccount = toggleAccount;
  window.loadPromptsFromJSON = loadPromptsFromJSON;
  window.selectProject = selectProjectCard;
  window.selectProjectCard = selectProjectCard;
  window.showNewProject = showNewProject;
  window.cancelNewProject = cancelNewProject;
  window.createProject = createProject;
  window.migrateProjects = migrateProjects;
  window.toggleProjects = toggleProjects;
  window.onReferenceFilesChange = onReferenceFilesChange;
  window.removeReference = removeReference;

  // Kick off
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
