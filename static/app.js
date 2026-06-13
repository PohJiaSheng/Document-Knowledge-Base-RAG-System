/**
 * Document Knowledge Base RAG frontend
 */

// ─── Marked config ────────────────────────────────────────────────────────────
marked.setOptions({
    highlight: (code, lang) => {
        if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
        return hljs.highlightAuto(code).value;
    },
    breaks: true, gfm: true,
});

// ─── Tab switching ────────────────────────────────────────────────────────────
let _activeTab = 'chat';

function switchTab(tab) {
    _activeTab = tab;
    document.getElementById('tabChat').classList.toggle('active', tab === 'chat');
    document.getElementById('tabDms').classList.toggle('active',  tab === 'dms');
    document.getElementById('panelChat').style.display = tab === 'chat' ? 'flex' : 'none';
    document.getElementById('panelDms').style.display  = tab === 'dms'  ? 'flex' : 'none';
    if (tab === 'dms') loadDocuments();
}

// ═══════════════════════════════════════════════════════════════════════════════
// FILTER SELECTION
// ═══════════════════════════════════════════════════════════════════════════════

let _metaOptions = { process: [], module: [], eq_type: [], package: [] };
// { process: Set, module: Set, eq_type: Set, package: Set }
let _selectedFilters = { process: new Set(), module: new Set(), eq_type: new Set(), package: new Set() };
let _filtersConfirmed = false;

const _filterLabels = {
    process: 'Process',
    module: 'Module',
    eq_type: 'Equipment Type',
    package: 'Package',
};

async function loadFilterOptions() {
    try {
        const data = await (await fetch('/api/dms/metadata-options')).json();
        _metaOptions = {
            process:  data.process  || [],
            module:   data.module   || [],
            eq_type:  data.eq_type  || [],
            package:  data.package  || [],
        };
        // Also populate DMS selects (shared fetch)
        populateSelect('metaProcess',   _metaOptions.process,   false);
        populateMultiSelect('metaModule',    _metaOptions.module,   false);
        populateMultiSelect('metaEquipment', _metaOptions.eq_type,  true);
        populateMultiSelect('metaPackage',   _metaOptions.package,  true);
        renderFilterBody();
    } catch {
        const body = document.getElementById('filterSelectionBody');
        if (body) body.innerHTML = '<p style="color:var(--error);text-align:center;padding:16px;">Failed to load filter options.</p>';
    }
}

function renderFilterBody() {
    const body = document.getElementById('filterSelectionBody');
    if (!body) return;
    const order = ['process', 'module', 'eq_type', 'package'];
    body.innerHTML = order.map(key => {
        const opts = _metaOptions[key] || [];
        if (!opts.length) return '';
        const anyActive = _selectedFilters[key].size === 0;
        const btns = opts.map(v => {
            const active = _selectedFilters[key].has(v);
            return `<button class="sel-btn${active ? ' active' : ''}" onclick="toggleFilter('${key}','${escapeAttr(v)}')">${escapeHtml(v)}</button>`;
        }).join('');
        return `<div class="sel-group">
            <div class="sel-group-label">${_filterLabels[key]}</div>
            <div class="sel-btns">
                <button class="sel-btn sel-btn-any${anyActive ? ' active' : ''}" onclick="clearFilter('${key}')">Any</button>
                ${btns}
            </div>
        </div>`;
    }).join('');
    updateFilterSummary();
}

function toggleFilter(key, value) {
    if (_selectedFilters[key].has(value)) {
        _selectedFilters[key].delete(value);
    } else {
        _selectedFilters[key].add(value);
    }
    renderFilterBody();
}

function clearFilter(key) {
    _selectedFilters[key].clear();
    renderFilterBody();
}

function updateFilterSummary() {
    const summary = document.getElementById('filterSummary');
    if (!summary) return;
    const parts = [];
    for (const [key, sel] of Object.entries(_selectedFilters)) {
        if (sel.size) parts.push(`${_filterLabels[key]}: ${[...sel].join(', ')}`);
    }
    summary.textContent = parts.length
        ? parts.join(' · ')
        : 'No filters selected — all documents will be considered.';
}

async function confirmFilters() {
    _filtersConfirmed = true;
    document.getElementById('filterOverlay').classList.add('hidden');
    // Enable chat input
    const ci = document.getElementById('chatInput');
    const sb = document.getElementById('sendBtn');
    if (ci) { ci.disabled = false; ci.placeholder = 'Ask about your documents\u2026'; }
    if (sb) sb.disabled = false;
    renderActiveFilterTags();
    updateFilterSummary();
    // Fetch DB-matching filenames then refresh sidebar
    await fetchFilteredFilenames();
    loadShareFiles(_currentSharePath);
    refreshKBSources();
}

function openFilterOverlay() {
    renderFilterBody();
    document.getElementById('filterOverlay').classList.remove('hidden');
}

function renderActiveFilterTags() {
    const container = document.getElementById('activeFilterTags');
    if (!container) return;
    const parts = [];
    for (const [key, sel] of Object.entries(_selectedFilters)) {
        for (const v of sel) {
            parts.push(`<span class="source-ref-card" style="font-size:10px;" title="${escapeAttr(_filterLabels[key])}">${escapeHtml(_filterLabels[key][0])}: ${escapeHtml(v)}</span>`);
        }
    }
    container.innerHTML = parts.join('');
}

function getActiveFiltersPayload() {
    const out = {};
    for (const [key, sel] of Object.entries(_selectedFilters)) {
        if (sel.size) out[key] = [...sel];
    }
    return out;
}

/**
 * Set of allowed basenames returned by the DB for current filters.
 * null = no filter active → show everything.
 */
let _dbAllowedFilenames = null; // Set<string> | null

function fileIsAllowed(nameOrPath) {
    if (_dbAllowedFilenames === null) return true; // no filter
    const basename = nameOrPath.split(/[\\/]/).pop();
    return _dbAllowedFilenames.has(basename);
}

async function fetchFilteredFilenames() {
    const payload = getActiveFiltersPayload();
    // No values selected → show all
    const hasAny = Object.values(payload).some(v => v && v.length > 0);
    if (!hasAny) { _dbAllowedFilenames = null; return; }
    try {
        const res  = await fetch('/api/share/filter-files', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        _dbAllowedFilenames = data.filenames === null ? null : new Set(data.filenames);
    } catch {
        _dbAllowedFilenames = null; // on error, show all
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// KNOWLEDGE BASE SIDEBAR
// ═══════════════════════════════════════════════════════════════════════════════

let _currentSharePath = 'File';
let isLoading         = false;
let hasUserMessages   = false;

const chatMessages         = document.getElementById('chatMessages');
const chatInput            = document.getElementById('chatInput');
const sendBtn              = document.getElementById('sendBtn');
const headerSubtitle       = document.getElementById('headerSubtitle');
const suggestionsArea      = document.getElementById('suggestionsArea');
const suggestionsContainer = document.getElementById('suggestionsContainer');

// ── KB Sources ────────────────────────────────────────────────────────────────

async function refreshKBSources() {
    try {
        const data = await (await fetch('/api/sources')).json();
        renderKBSources(data.sources || []);
    } catch { /* silent */ }
}

function renderKBSources(sources) {
    const list   = document.getElementById('kbSourcesList');
    const badge  = document.getElementById('kbBadge');
    badge.textContent = sources.length;
    if (!sources.length) {
        list.innerHTML = '<p class="kb-empty-hint">Load files from ShareDrive below</p>';
        headerSubtitle.textContent = 'Load documents from ShareDrive to begin';
        return;
    }
    // Apply filters: hide sources that don't match (but keep count of total)
    const visible = _filtersConfirmed
        ? sources.filter(s => fileIsAllowed(s.share_path || s.name))
        : sources;
    const hidden = sources.length - visible.length;
    headerSubtitle.textContent = `${sources.length} source${sources.length !== 1 ? 's' : ''} loaded`
        + (hidden ? ` (${hidden} hidden by filter)` : '');
    if (!visible.length) {
        list.innerHTML = '<p class="kb-empty-hint">No loaded sources match the current filters</p>';
    } else {
        list.innerHTML = visible.map(s => `
            <div class="kb-source-item">
                <span class="kb-source-icon">${iconForType(s.type)}</span>
                <div class="kb-source-info">
                    <div class="kb-source-name" title="${escapeAttr(s.name)}">${escapeHtml(s.name)}</div>
                    <div class="kb-source-meta">${escapeHtml(s.type)} · ${formatSize(s.size)}</div>
                </div>
                <button class="kb-remove-btn" onclick="removeFromKB('${escapeAttr(s.id)}')" title="Remove">✕</button>
            </div>`).join('');
    }
    if (!hasUserMessages) fetchSuggestions();
}

async function removeFromKB(sourceId) {
    try {
        await fetch(`/api/source/${sourceId}`, { method: 'DELETE' });
        refreshKBSources();
    } catch { /* silent */ }
}

// ── ShareDrive Browser ────────────────────────────────────────────────────────

async function loadShareFiles(path) {
    if (path !== undefined) _currentSharePath = path;
    updatePathBar(_currentSharePath);
    const list = document.getElementById('shareFilesList');
    list.innerHTML = '<div class="kb-loading"><span></span><span></span><span></span></div>';
    try {
        const encodedPath = encodeURIComponent(_currentSharePath);
        const data = await (await fetch(`/api/share/list?path=${encodedPath}`)).json();
        renderShareFiles(data.entries || []);
    } catch (err) {
        list.innerHTML = `<p class="kb-empty-hint" style="color:var(--error)">Failed to load: ${escapeHtml(err.message)}</p>`;
    }
}

function refreshShareList() { loadShareFiles(_currentSharePath); }

function navigateShare(path) { loadShareFiles(path); }

function updatePathBar(path) {
    // paths use forward slashes (safe in HTML attributes)
    const segments = path ? path.split('/').filter(Boolean) : [];
    const segContainer = document.getElementById('sharePathSegments');
    if (!segments.length) {
        segContainer.innerHTML = '';
        return;
    }
    segContainer.innerHTML = segments.map((seg, i) => {
        const segPath = segments.slice(0, i + 1).join('/');
        return `<span class="kb-path-sep">›</span><button class="kb-path-seg" onclick="navigateShare('${escapeAttr(segPath)}')">${escapeHtml(seg)}</button>`;
    }).join('');
}

function renderShareFiles(entries) {
    const list = document.getElementById('shareFilesList');
    if (!entries.length) {
        list.innerHTML = '<p class="kb-empty-hint">No supported files found</p>';
        return;
    }
    // Directories always shown; files filtered when filters are active
    const rendered = entries.filter(e => {
        if (e.is_dir) return true;  // always navigate into dirs
        if (!_filtersConfirmed) return true;  // no filter set yet, show all
        return fileIsAllowed(e.path || e.name);
    });
    const hiddenCount = entries.filter(e => !e.is_dir).length - rendered.filter(e => !e.is_dir).length;
    if (!rendered.length) {
        list.innerHTML = '<p class="kb-empty-hint">No files match the current filters</p>';
        return;
    }
    const rows = rendered.map(e => {
        if (e.is_dir) {
            return `<div class="kb-file-item kb-dir-item" onclick="navigateShare('${escapeAttr(e.path)}')">
                <span class="kb-file-icon">📁</span>
                <span class="kb-file-name">${escapeHtml(e.name)}</span>
            </div>`;
        }
        const sizeStr = e.size != null ? formatSize(e.size) : '';
        return `<div class="kb-file-item">
            <span class="kb-file-icon">📄</span>
            <div class="kb-file-info">
                <span class="kb-file-name" title="${escapeAttr(e.name)}">${escapeHtml(e.name)}</span>
                ${sizeStr ? `<span class="kb-file-size">${sizeStr}</span>` : ''}
            </div>
            <button class="kb-load-btn" onclick="loadIntoKB('${escapeAttr(e.path)}', this)">Load</button>
        </div>`;
    }).join('');
    const footer = hiddenCount > 0
        ? `<p class="kb-empty-hint" style="border-top:1px solid var(--border-subtle);margin-top:4px;padding-top:6px;">${hiddenCount} file${hiddenCount !== 1 ? 's' : ''} hidden by filter</p>`
        : '';
    list.innerHTML = rows + footer;
}

async function loadIntoKB(sharePath, btn) {
    const status = document.getElementById('shareLoadStatus');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    status.textContent = '';
    try {
        const res  = await fetch('/api/kb/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: sharePath }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to load');
        status.className = 'kb-load-status ok';
        status.textContent = `✓ ${data.source.name} loaded`;
        refreshKBSources();
    } catch (err) {
        status.className = 'kb-load-status err';
        status.textContent = `✗ ${err.message}`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Load'; }
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function iconForType(type) {
    return { pdf: '📄', document: '📝', presentation: '📊', text: '📃', image: '🖼️' }[type] || '📄';
}

function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ═══════════════════════════════════════════════════════════════════════════════
// CHAT
// ═══════════════════════════════════════════════════════════════════════════════

async function fetchSuggestions() {
    if (hasUserMessages) return;
    try {
        const data = await (await fetch('/api/suggestions', { method: 'POST' })).json();
        if (data.suggestions?.length) showSuggestions(data.suggestions);
    } catch { /* silent */ }
}

function showSuggestions(suggestions) {
    if (hasUserMessages) return;
    suggestionsContainer.innerHTML = suggestions.map(s =>
        `<button class="suggestion-btn">${escapeHtml(s)}</button>`
    ).join('');
    suggestionsArea.style.display = 'block';
    suggestionsContainer.querySelectorAll('.suggestion-btn').forEach(btn => {
        btn.addEventListener('click', () => { chatInput.value = btn.textContent; sendMessage(); });
    });
}

function hideSuggestions() { suggestionsArea.style.display = 'none'; }

async function sendMessage() {
    const query = chatInput.value.trim();
    if (!query || isLoading) return;

    isLoading       = true;
    hasUserMessages = true;
    hideSuggestions();
    sendBtn.innerHTML = '<div class="spinner"></div>';
    sendBtn.disabled = chatInput.disabled = true;

    addMessage('user', query);
    chatInput.value = '';
    autoResize(chatInput);
    const loadingId = addLoadingMessage();

    try {
        const res  = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: query, filters: getActiveFiltersPayload() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to get response');
        removeMessage(loadingId);
        addMessage('ai', data.response, data.sources_used);
    } catch (err) {
        removeMessage(loadingId);
        addMessage('system', `Error: ${err.message}`);
    } finally {
        isLoading = false;
        sendBtn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;
        sendBtn.disabled = chatInput.disabled = false;
        chatInput.focus();
    }
}

function addMessage(type, text, sources) {
    const id  = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const div = document.createElement('div');
    div.id        = id;
    div.className = `message ${type}-message fade-in`;

    let avatarClass, avatarContent, bubbleClass;
    if (type === 'user') {
        avatarClass = 'user-avatar'; avatarContent = 'U'; bubbleClass = 'user-bubble';
    } else if (type === 'ai') {
        avatarClass = 'ai-avatar'; avatarContent = 'AI'; bubbleClass = 'ai-bubble';
    } else {
        avatarClass = 'system-avatar';
        avatarContent = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>`;
        bubbleClass = 'system-bubble';
    }

    const contentHTML = type === 'ai' ? marked.parse(text) : escapeHtml(text);

    let sourcesHTML = '';
    if (type === 'ai' && sources && sources.length) {
        const cards = sources.map(s =>
            `<span class="source-ref-card">${iconForType(s.type)} <strong>${escapeHtml(s.name)}</strong></span>`
        ).join('');
        sourcesHTML = `<div class="sources-used"><div class="sources-used-title">Sources Referenced</div><div class="sources-used-grid">${cards}</div></div>`;
    }

    div.innerHTML = `
        <div class="message-avatar ${avatarClass}">${avatarContent}</div>
        <div class="message-content">
            <div class="message-bubble ${bubbleClass}">${contentHTML}${sourcesHTML}</div>
        </div>`;
    chatMessages.appendChild(div);
    scrollToBottom();
    if (type === 'ai') div.querySelectorAll('pre code').forEach(b => { try { hljs.highlightElement(b); } catch {} });
    return id;
}

function addLoadingMessage() {
    const id  = 'loading-' + Date.now();
    const div = document.createElement('div');
    div.id = id; div.className = 'message ai-message fade-in';
    div.innerHTML = `<div class="message-avatar ai-avatar">AI</div><div class="message-content"><div class="message-bubble ai-bubble"><div class="loading-dots"><span></span><span></span><span></span></div></div></div>`;
    chatMessages.appendChild(div);
    scrollToBottom();
    return id;
}

function removeMessage(id) { const el = document.getElementById(id); if (el) el.remove(); }
function scrollToBottom() { requestAnimationFrame(() => { chatMessages.scrollTop = chatMessages.scrollHeight; }); }
function autoResize(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'; }
function handleInputKeydown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }

// ═══════════════════════════════════════════════════════════════════════════════
// DMS
// ═══════════════════════════════════════════════════════════════════════════════
let _dmsFile     = null;
let _dmsMetaOpts = { process: [], module: [], eq_type: [], package: [] };
let _pollTimers  = {};

const dmsDropZone       = document.getElementById('dropZone');
const dmsFileInput      = document.getElementById('fileInput');
const selectedFileName  = document.getElementById('selectedFileName');
const uploadBtn         = document.getElementById('uploadBtn');
const uploadStatus      = document.getElementById('uploadStatus');
const preprocessStatus  = document.getElementById('preprocessStatus');

// Drag & drop
if (dmsDropZone) {
    dmsDropZone.addEventListener('dragover', e => { e.preventDefault(); dmsDropZone.classList.add('drag-over'); });
    dmsDropZone.addEventListener('dragleave', () => dmsDropZone.classList.remove('drag-over'));
    dmsDropZone.addEventListener('drop', e => {
        e.preventDefault(); dmsDropZone.classList.remove('drag-over');
        const f = e.dataTransfer?.files?.[0];
        if (f) setDmsFile(f);
    });
    dmsDropZone.addEventListener('click', () => dmsFileInput && dmsFileInput.click());
}
if (dmsFileInput) {
    dmsFileInput.addEventListener('change', () => { if (dmsFileInput.files?.[0]) setDmsFile(dmsFileInput.files[0]); });
}

function setDmsFile(f) {
    const ext = f.name.split('.').pop().toUpperCase();
    if (!['PDF', 'DOC', 'DOCX'].includes(ext)) {
        setUploadStatus('err', `Unsupported type: .${ext.toLowerCase()}. Upload PDF, DOC, or DOCX.`);
        return;
    }
    _dmsFile = f;
    if (selectedFileName) selectedFileName.textContent = f.name;
    setUploadStatus('', '');
}

// loadDmsMetaOptions is replaced by loadFilterOptions (called in init)

function populateSelect(id, opts, withAny = true) {
    const el = document.getElementById(id);
    if (!el) return;
    const anyOpt = withAny ? '<option value="">Any / None</option>' : '<option value="">— Select —</option>';
    el.innerHTML = anyOpt +
        opts.map(v => `<option value="${escapeAttr(v)}">${escapeHtml(v)}</option>`).join('');
}

// ── Custom multi-select dropdowns ─────────────────────────────────────────────
const _multiDdData = {}; // id → { opts: string[], selected: Set<string>, hasAny: bool }

function populateMultiSelect(id, opts, hasAny = false) {
    _multiDdData[id] = { opts, selected: new Set(), hasAny };
    _renderMultiDd(id);
}

function _renderMultiDd(id) {
    const data  = _multiDdData[id];
    if (!data) return;
    const panel = document.getElementById(`${id}-panel`);
    if (!panel) return;
    const anyActive = data.selected.size === 0;
    let html = '';
    if (data.hasAny) {
        html += `<label class="multi-dd-item multi-dd-any${anyActive ? ' checked' : ''}">
            <input type="radio" name="${id}-any" value="__any__"${anyActive ? ' checked' : ''}
                onchange="_multiDdSelectAny('${id}')">
            Any / None
        </label>`;
    }
    html += data.opts.map(v => {
        const checked = data.selected.has(v);
        return `<label class="multi-dd-item${checked ? ' checked' : ''}">
            <input type="checkbox" value="${escapeAttr(v)}"${checked ? ' checked' : ''}
                onchange="_multiDdChange('${id}', '${escapeAttr(v)}', this.checked)">
            ${escapeHtml(v)}
        </label>`;
    }).join('');
    panel.innerHTML = html;
    _updateMultiDdLabel(id);
}

function _multiDdChange(id, value, checked) {
    const data = _multiDdData[id];
    if (!data) return;
    if (checked) data.selected.add(value); else data.selected.delete(value);
    _renderMultiDd(id);
}

function _multiDdSelectAny(id) {
    const data = _multiDdData[id];
    if (!data) return;
    data.selected.clear();
    _renderMultiDd(id);
}

function _updateMultiDdLabel(id) {
    const data  = _multiDdData[id];
    const label = document.getElementById(`${id}-label`);
    if (!label || !data) return;
    const sel = [...data.selected];
    if (!sel.length) {
        label.textContent = id === 'metaModule' ? 'Select modules…' : 'None selected';
        label.classList.add('muted');
    } else {
        label.textContent = sel.join(', ');
        label.classList.remove('muted');
    }
}

function toggleMultiDd(id) {
    const root = document.getElementById(id);
    if (!root) return;
    const wasOpen = root.classList.contains('open');
    // Close all open dropdowns first
    document.querySelectorAll('.multi-dd.open').forEach(el => el.classList.remove('open'));
    if (!wasOpen) root.classList.add('open');
}

function getMultiSelectValues(id) {
    return [...(_multiDdData[id]?.selected || [])];
}

// Close dropdown when clicking outside
document.addEventListener('click', e => {
    if (!e.target.closest('.multi-dd')) {
        document.querySelectorAll('.multi-dd.open').forEach(el => el.classList.remove('open'));
    }
});

async function dmsUpload() {
    if (!_dmsFile) { setUploadStatus('err', 'Please select a file first.'); return; }

    const title   = (document.getElementById('metaTitle')?.value || '').trim();
    const process = document.getElementById('metaProcess')?.value || '';
    const modules = getMultiSelectValues('metaModule');

    if (!title)   { setUploadStatus('err', 'Please enter a document title.'); return; }
    if (!process) { setUploadStatus('err', 'Please select a Process.'); return; }
    if (!modules.length) { setUploadStatus('err', 'Please select at least one Module.'); return; }

    if (uploadBtn) uploadBtn.disabled = true;
    setUploadStatus('info', 'Uploading document and starting preprocessing… (this may take several minutes)');

    const fd = new FormData();
    fd.append('file',    _dmsFile);
    fd.append('title',   title);
    fd.append('process', process);
    modules.forEach(v => fd.append('module', v));
    getMultiSelectValues('metaEquipment').forEach(v => fd.append('equipment_type', v));
    getMultiSelectValues('metaPackage').forEach(v => fd.append('package', v));

    try {
        const res  = await fetch('/api/dms/upload', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Upload failed');

        let msg = `✓ ${data.filename} uploaded (doc_id=${data.doc_id}). Preprocessing started…`;
        if (data.renamed) msg += ' (renamed to avoid duplicate)';
        setUploadStatus('ok', msg);

        _dmsFile = null;
        document.getElementById('metaTitle').value = '';
        if (selectedFileName) selectedFileName.textContent = '';
        if (dmsFileInput) dmsFileInput.value = '';

        loadDocuments();

        if (data.task_id) {
            setPreprocessStatus('info', `Preprocessing running (task ${data.task_id})… page analysis & embedding may take several minutes.`);
            pollPreprocess(data.task_id);
        }
    } catch (err) {
        setUploadStatus('err', `Upload failed: ${err.message}`);
    } finally {
        if (uploadBtn) uploadBtn.disabled = false;
    }
}

async function loadDocuments() {
    const wrapper = document.getElementById('docTableWrapper');
    if (!wrapper) return;
    wrapper.innerHTML = '<p class="dms-hint">Loading…</p>';
    try {
        const data = await (await fetch('/api/dms/documents')).json();
        const docs = data.documents || [];
        if (!docs.length) {
            wrapper.innerHTML = '<p class="dms-hint">No documents found. Upload one above.</p>';
            return;
        }
        wrapper.innerHTML = `
            <table class="doc-table">
                <thead><tr>
                    <th>ID</th><th>File Name</th><th>Type</th>
                    <th>Process</th><th>Module</th><th>Created</th><th>Action</th>
                </tr></thead>
                <tbody>${docs.map(d => {
                    const name = (d.FILEPATH || '').replace(/.*[\\/]/, '');
                    const created = (d.CREATED_AT || '').slice(0, 19).replace('T', ' ');
                    return `<tr>
                        <td>${d.ID}</td>
                        <td class="doc-name" title="${escapeAttr(d.FILEPATH || '')}">${escapeHtml(name)}</td>
                        <td>${escapeHtml(d.FILE_TYPE || '')}</td>
                        <td>${escapeHtml(d.PROCESS  || '—')}</td>
                        <td>${escapeHtml(d.MODULE   || '—')}</td>
                        <td>${escapeHtml(created)}</td>
                        <td><button class="preprocess-btn" onclick="triggerPreprocess(${d.ID}, this)">⚙️ Preprocess</button></td>
                    </tr>`;
                }).join('')}</tbody>
            </table>`;
    } catch (err) {
        wrapper.innerHTML = `<p class="dms-hint" style="color:var(--error)">Failed to load: ${escapeHtml(err.message)}</p>`;
    }
}

async function triggerPreprocess(docId, btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Starting…';
    setPreprocessStatus('info', `Starting preprocessing for doc_id=${docId}…`);
    try {
        const res  = await fetch(`/api/dms/preprocess/${docId}`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to start preprocessing');
        setPreprocessStatus('info', `Preprocessing running (task ${data.task_id})… page analysis & embedding may take several minutes.`);
        pollPreprocess(data.task_id, btn);
    } catch (err) {
        setPreprocessStatus('err', `Error: ${err.message}`);
        btn.disabled = false;
        btn.textContent = '⚙️ Preprocess';
    }
}

function pollPreprocess(taskId, btn) {
    if (_pollTimers[taskId]) clearInterval(_pollTimers[taskId]);
    _pollTimers[taskId] = setInterval(async () => {
        try {
            const data = await (await fetch(`/api/dms/preprocess/status/${taskId}`)).json();
            if (data.status === 'done') {
                clearInterval(_pollTimers[taskId]);
                const chunks = data.detail?.total_image_chunks ?? '?';
                setPreprocessStatus('ok', `✓ Preprocessing complete — ${chunks} chunks embedded into Qdrant.`);
                if (btn) { btn.disabled = false; btn.textContent = '⚙️ Preprocess'; }
            } else if (data.status === 'error') {
                clearInterval(_pollTimers[taskId]);
                setPreprocessStatus('err', `Preprocessing failed: ${data.detail}`);
                if (btn) { btn.disabled = false; btn.textContent = '⚙️ Preprocess'; }
            } else {
                setPreprocessStatus('info', `Status: ${data.status}…`);
            }
        } catch { /* network hiccup, keep polling */ }
    }, 4000);
}

function setUploadStatus(cls, msg) {
    if (!uploadStatus) return;
    uploadStatus.className = 'dms-status' + (cls ? ` ${cls}` : '');
    uploadStatus.style.whiteSpace = 'pre-line';
    uploadStatus.textContent = msg;
}

function setPreprocessStatus(cls, msg) {
    if (!preprocessStatus) return;
    preprocessStatus.className = 'dms-status' + (cls ? ` ${cls}` : '');
    preprocessStatus.textContent = msg;
}

// ─── Shared utilities ─────────────────────────────────────────────────────────
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
}
function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ─── Init ─────────────────────────────────────────────────────────────────────
async function init() {
    await loadShareFiles('File');
    refreshKBSources();
    await loadFilterOptions(); // loads metadata and renders filter overlay
}

init();
