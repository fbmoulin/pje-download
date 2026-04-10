/**
 * PJe Download Dashboard — Application
 * ======================================
 * Single-page dashboard for batch PJe document downloads.
 * Communicates with dashboard_api.py via REST.
 */

// ── Constants ──

const API = window.location.origin;
const POLL_INTERVAL_MIN = 1500;
const POLL_INTERVAL_MAX = 15000;
const TOAST_DURATION_MS = 5000;

// ── State ──

let pollTimer = null;
let pollInterval = POLL_INTERVAL_MIN;
let currentView = 'main'; // 'main' | 'batch-detail'
const API_KEY_STORAGE_KEY = 'pje-dashboard-api-key';

// ── DOM helpers ──

const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

function esc(str) {
  if (!str) return '';
  const el = document.createElement('span');
  el.textContent = String(str);
  return el.innerHTML;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (typeof c === 'string') node.appendChild(document.createTextNode(c));
    else if (c) node.appendChild(c);
  }
  return node;
}

function getApiKey() {
  return (localStorage.getItem(API_KEY_STORAGE_KEY) || '').trim();
}

function setApiKey(value) {
  const normalized = (value || '').trim();
  if (normalized) localStorage.setItem(API_KEY_STORAGE_KEY, normalized);
  else localStorage.removeItem(API_KEY_STORAGE_KEY);
}

// ── Formatters ──

function fmtBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1_048_576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1_073_741_824) return (bytes / 1_048_576).toFixed(1) + ' MB';
  return (bytes / 1_073_741_824).toFixed(2) + ' GB';
}

function fmtDate(iso) {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  return d.toLocaleDateString('pt-BR') + ' ' + d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function fmtDuration(secs) {
  if (typeof secs !== 'number' || secs <= 0) return '\u2014';
  if (secs < 60) return secs.toFixed(1) + 's';
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return `${m}m ${s}s`;
}

function isAntigo(num) {
  return num && !num.startsWith('5');
}

function getDocStats(p = {}) {
  const done = Number(p.docs_baixados || p.docs || 0);
  const total = Number(p.total_docs || 0);
  return { done, total };
}

function fmtDocProgress(p = {}) {
  const { done, total } = getDocStats(p);
  if (total > 0) return `${done}/${total} docs`;
  if (done > 0) return `${done} docs`;
  return '\u2014';
}

function inferStrategy(detail = '') {
  const normalized = String(detail || '').toLowerCase();
  if (!normalized) return '';
  if (normalized.includes('api rest')) return 'API REST';
  if (normalized.includes('browser')) return 'Browser';
  if (normalized.includes('mni')) return 'MNI SOAP';
  if (normalized.includes('gdrive') || normalized.includes('google drive')) return 'GDrive';
  return '';
}

// ── Toast Notifications ──

function toast(message, type = 'info') {
  const container = $('#toast-container');
  const t = el('div', { class: `toast toast--${type}`, text: message });
  container.appendChild(t);
  setTimeout(() => {
    t.classList.add('removing');
    setTimeout(() => t.remove(), 300);
  }, TOAST_DURATION_MS);
}

// ── Badge ──

function setBadge(status) {
  const badge = $('#status-badge');
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  badge.textContent = label;
  badge.className = 'badge badge--' + (status === 'running' ? 'running' : status === 'done' ? 'done' : status === 'failed' ? 'failed' : status === 'offline' ? 'offline' : 'idle');
}

// ── KPI Updates ──

function updateKPIs(processos = 0, docs = 0, bytes = 0, batches = 0) {
  const animate = (el, val) => {
    el.textContent = val;
  };
  animate($('#kpi-processos'), processos);
  animate($('#kpi-docs'), docs);
  animate($('#kpi-size'), fmtBytes(bytes));
  animate($('#kpi-batches'), batches);
}

// ── Phase Pipeline Renderer ──

function renderPhase(p, isAntigoProc) {
  const phase = p.phase || (p.status === 'done' ? 'done' : p.status === 'failed' ? 'failed' : 'waiting');
  const detail = p.phase_detail || '';
  const strategy = inferStrategy(detail);
  const docProgress = fmtDocProgress(p);

  if (phase === 'failed') {
    const failureDetail = detail || p.erro || '';
    let html = '<span class="tag tag--failed">Falhou</span>';
    if (failureDetail) {
      html += `<div class="pipeline__detail" title="${esc(failureDetail)}">${esc(failureDetail.substring(0, 80))}</div>`;
    }
    return html;
  }

  if (phase === 'waiting' || phase === 'starting') {
    return '<span class="tag tag--pending">Aguardando</span>';
  }

  const steps = isAntigoProc
    ? ['gdrive', 'mni_metadata', 'mni_download', 'done']
    : ['mni_metadata', 'mni_download', 'done'];

  const labels = {
    gdrive: 'GDrive',
    mni_metadata: 'Metadados',
    mni_download: 'Download',
    done: 'Concluido',
  };

  const currentPhase = phase === 'saving' ? 'done' : phase;
  const currentIdx = steps.indexOf(currentPhase);

  let html = '<div class="pipeline">';
  steps.forEach((step, i) => {
    if (i > 0) html += '<span class="pipeline__connector"><svg width="20" height="10" viewBox="0 0 20 10" fill="none" xmlns="http://www.w3.org/2000/svg"><line x1="0" y1="5" x2="14" y2="5" stroke="rgba(90,99,128,.4)" stroke-width="1"/><polygon points="14,2 20,5 14,8" fill="rgba(90,99,128,.35)"/></svg></span>';
    let cls = 'pipeline__step--inactive';
    if (phase === 'done' || (phase === 'saving' && step === 'done')) cls = 'pipeline__step--done';
    else if (i < currentIdx) cls = 'pipeline__step--done';
    else if (i === currentIdx) cls = 'pipeline__step--active';
    const dot = cls === 'pipeline__step--active' ? '<span class="pipeline__dot"></span>' : '';
    html += `<span class="pipeline__step ${cls}">${dot}${labels[step]}</span>`;
  });
  html += '</div>';

  if (phase !== 'done') {
    const meta = [];
    if (strategy) meta.push(`<span class="tag tag--running tag--inline">${esc(strategy)}</span>`);
    if (docProgress !== '\u2014') meta.push(`<span class="tag tag--queued tag--inline">${esc(docProgress)}</span>`);
    if (meta.length) html += `<div class="pipeline__meta">${meta.join('')}</div>`;
  }

  if (detail && phase !== 'done') {
    html += `<div class="pipeline__detail" title="${esc(detail)}">${esc(detail)}</div>`;
  }
  return html;
}

// ── Status Tag ──

function statusTag(status) {
  const s = status || 'pending';
  return `<span class="tag tag--${s}">${s}</span>`;
}

// ══════════════════════════════════════════
//  API Communication
// ══════════════════════════════════════════

async function apiFetch(path, opts = {}) {
  const headers = new Headers(opts.headers || {});
  const apiKey = getApiKey();
  if (apiKey && !headers.has('X-API-Key')) headers.set('X-API-Key', apiKey);
  const res = await fetch(API + path, { ...opts, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Submit Download ──

async function submitDownload() {
  const textarea = $('#processos-input');
  const raw = textarea.value.trim();
  if (!raw) {
    toast('Insira pelo menos um numero de processo', 'warning');
    return;
  }

  const processos = raw.split('\n').map(l => l.trim()).filter(l => l.length > 10);
  if (!processos.length) {
    toast('Nenhum numero de processo valido encontrado', 'warning');
    return;
  }

  const skipAnexos = $('#skip-anexos').checked;
  const btn = $('#btn-submit');
  btn.disabled = true;

  try {
    const data = await apiFetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ processos, include_anexos: !skipAnexos }),
    });
    toast(`Batch ${data.batch_id} criado com ${data.processos} processos`, 'success');
    textarea.value = '';
    startPolling();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

function clearForm() {
  $('#processos-input').value = '';
  $('#file-label').textContent = '';
}

// ── File Upload ──

function handleFileUpload(input) {
  const file = input.files[0];
  if (!file) return;
  $('#file-label').textContent = file.name;

  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result.trim();
    let numeros = [];

    if (file.name.endsWith('.json')) {
      try {
        const data = JSON.parse(text);
        const list = Array.isArray(data) ? data : (data.processos || []);
        numeros = list.map(item => typeof item === 'string' ? item.trim() : (item.numero || item.numeroProcesso || item.processo || '')).filter(Boolean);
      } catch (err) {
        toast('JSON invalido: ' + err.message, 'error');
        return;
      }
    } else if (file.name.endsWith('.csv')) {
      const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
      if (lines.length < 2) { toast('CSV vazio', 'warning'); return; }
      const header = lines[0].toLowerCase().split(/[,;\t]/);
      const idx = header.findIndex(h => h.includes('numero') || h.includes('processo'));
      if (idx >= 0) {
        for (let i = 1; i < lines.length; i++) {
          const cols = lines[i].split(/[,;\t]/);
          if (cols[idx]) numeros.push(cols[idx].replace(/"/g, '').trim());
        }
      } else {
        for (const line of lines) {
          const match = line.match(/\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}/);
          if (match) numeros.push(match[0]);
        }
      }
    } else {
      numeros = text.split('\n').map(l => l.trim()).filter(l => l.length > 10 && !l.startsWith('#'));
    }

    numeros = numeros.filter(n => n.length > 10);
    if (!numeros.length) { toast('Nenhum processo encontrado no arquivo', 'warning'); return; }

    const ta = $('#processos-input');
    const existing = ta.value.trim();
    ta.value = existing ? existing + '\n' + numeros.join('\n') : numeros.join('\n');
    toast(`${numeros.length} processo(s) importados de ${file.name}`, 'success');
    $('#file-label').textContent = `${file.name} (${numeros.length})`;
  };
  reader.readAsText(file);
}

// ══════════════════════════════════════════
//  Progress Polling
// ══════════════════════════════════════════

function startPolling() {
  stopPolling();
  pollInterval = POLL_INTERVAL_MIN;
  fetchProgress();
  schedulePoll();
}

function schedulePoll() {
  pollTimer = setTimeout(() => {
    fetchProgress();
    schedulePoll();
  }, pollInterval);
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

async function fetchProgress() {
  try {
    const data = await apiFetch('/api/progress');
    pollInterval = POLL_INTERVAL_MIN; // reset on success
    renderProgress(data);
    if (data.status === 'done' || data.status === 'failed' || data.status === 'idle') {
      stopPolling();
      fetchHistory();
    }
  } catch (e) {
    pollInterval = Math.min(pollInterval * 1.5, POLL_INTERVAL_MAX);
  }
}

function renderProgress(data) {
  setBadge(data.status || 'idle');
  const area = $('#progress-area');
  const batchCard = $('#batch-card');

  if (data.status === 'idle' || !data.processos) {
    area.innerHTML = `
      <div class="empty-state">
        <div class="empty-state__icon">\u2B07</div>
        <div class="empty-state__text">Nenhum download em andamento</div>
        <div class="empty-state__sub">Submeta processos para iniciar</div>
      </div>`;
    batchCard.classList.add('hidden');
    return;
  }

  const procs = data.processos || {};
  const summary = data.summary || {};
  const total = summary.total || Object.keys(procs).length;
  let done = 0, failed = 0, downloadedDocs = 0, expectedDocs = 0, totalBytes = 0;

  for (const p of Object.values(procs)) {
    if (p.status === 'done') done++;
    if (p.status === 'failed') failed++;
    const { done: procDone, total: procTotal } = getDocStats(p);
    downloadedDocs += procDone;
    expectedDocs += procTotal || procDone;
    totalBytes += p.tamanho_bytes || p.bytes || 0;
  }

  const pct = total > 0 ? Math.round((done + failed) / total * 100) : 0;

  updateKPIs(total, downloadedDocs, totalBytes, parseInt($('#kpi-batches').textContent) || 0);

  const statusMsg = data.status === 'running' ? 'Baixando documentos...'
    : data.status === 'failed' ? 'Falhou'
    : data.status === 'done' && failed > 0 ? 'Concluido com falhas'
    : data.status === 'done' ? 'Concluido' : data.status;

  const errorLine = (data.error || (failed > 0 && done === 0))
    ? `<div class="text-xs" style="color:var(--red);margin-top:4px">${esc(data.error || `${failed}/${total} processos falharam`)}</div>`
    : '';

  area.innerHTML = `
    <div style="margin-bottom:8px">
      <span style="font-size:.9rem;font-weight:600">Batch: </span>
      <span class="text-mono text-xs">${esc(data.batch_id || '')}</span>
    </div>
    <div class="text-xs text-muted">
      ${statusMsg}
      ${failed > 0 && done > 0 ? ` \u2022 <span style="color:var(--red)">${failed} falha(s)</span>` : ''}
    </div>
    ${errorLine}`;

  // Show batch table
  batchCard.classList.remove('hidden');
  $('#main-progress').style.width = pct + '%';
  const docLabel = expectedDocs > 0
    ? `${downloadedDocs}/${expectedDocs} docs`
    : `${downloadedDocs} docs`;
  $('#progress-label').textContent = `${done + failed} / ${total} processos (${pct}%) \u2014 ${docLabel}, ${fmtBytes(totalBytes)}`;

  renderProcessTable($('#processos-tbody'), procs);
}

// ── Process Table ──

function renderProcessTable(tbody, procs) {
  let html = '';
  for (const [num, p] of Object.entries(procs)) {
    const antigo = isAntigo(num);
    const antigoBadge = antigo ? ' <span class="tag tag--antigo">antigo</span>' : '';
    const docs = fmtDocProgress(p);
    const bytes = p.tamanho_bytes || p.bytes || 0;
    const dur = p.duracao_s;
    html += `<tr>
      <td class="td-mono">${esc(num)}${antigoBadge}</td>
      <td>${renderPhase(p, antigo)}</td>
      <td>${docs}</td>
      <td>${fmtBytes(bytes)}</td>
      <td>${fmtDuration(dur)}</td>
    </tr>`;
  }
  tbody.innerHTML = html;
}

// ══════════════════════════════════════════
//  History
// ══════════════════════════════════════════

async function fetchHistory() {
  try {
    const data = await apiFetch('/api/history');
    renderHistory(data);
  } catch (e) { /* silent */ }
}

function renderHistory(batches) {
  const tbody = $('#history-tbody');
  const empty = $('#history-empty');

  if (!batches.length) {
    empty.classList.remove('hidden');
    tbody.innerHTML = '';
    return;
  }
  empty.classList.add('hidden');

  // Update global KPIs from history
  let gDocs = 0, gBytes = 0;
  for (const b of batches) {
    gDocs += b.total_docs || 0;
    gBytes += b.total_bytes || 0;
  }
  $('#kpi-batches').textContent = batches.length;
  $('#kpi-docs').textContent = gDocs;
  $('#kpi-size').textContent = fmtBytes(gBytes);

  let html = '';
  for (const b of batches) {
    const errHint = b.error ? `<div class="pipeline__detail" style="color:var(--red)">${esc(b.error.substring(0, 80))}</div>` : '';
    html += `<tr class="clickable" onclick="viewBatch('${esc(b.batch_id)}')">
      <td class="td-mono">${esc(b.batch_id)}</td>
      <td>${b.processos}</td>
      <td>${statusTag(b.status)}${errHint}</td>
      <td>${b.total_docs || 0}</td>
      <td>${fmtBytes(b.total_bytes || 0)}</td>
      <td class="text-xs">${fmtDate(b.finished_at || b.created_at)}</td>
    </tr>`;
  }
  tbody.innerHTML = html;
}

// ── Batch Detail View ──

async function viewBatch(batchId) {
  try {
    const data = await apiFetch('/api/batch/' + batchId);
    if (!data.progress || !data.progress.processos) return;

    const batchCard = $('#batch-card');
    batchCard.classList.remove('hidden');

    const procs = data.progress.processos;
    let done = 0, downloadedDocs = 0, expectedDocs = 0, totalBytes = 0;
    for (const p of Object.values(procs)) {
      if (p.status === 'done') done++;
      const { done: procDone, total: procTotal } = getDocStats(p);
      downloadedDocs += procDone;
      expectedDocs += procTotal || procDone;
      totalBytes += p.bytes || p.tamanho_bytes || 0;
    }

    const total = Object.keys(procs).length;
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    $('#main-progress').style.width = pct + '%';
    const docLabel = expectedDocs > 0
      ? `${downloadedDocs}/${expectedDocs} docs`
      : `${downloadedDocs} docs`;
    $('#progress-label').textContent = `${done} / ${total} processos (${pct}%) \u2014 ${docLabel}, ${fmtBytes(totalBytes)}`;

    renderProcessTable($('#processos-tbody'), procs);
    batchCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    toast('Erro ao carregar batch: ' + e.message, 'error');
  }
}

// ══════════════════════════════════════════
//  Keyboard Shortcuts
// ══════════════════════════════════════════

document.addEventListener('keydown', (e) => {
  // Ctrl+Enter: submit
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    submitDownload();
  }
  // Escape: close batch detail
  if (e.key === 'Escape') {
    $('#batch-card').classList.add('hidden');
  }
});

// ══════════════════════════════════════════
//  Clock
// ══════════════════════════════════════════

function updateClock() {
  $('#clock').textContent = new Date().toLocaleTimeString('pt-BR');
}

// ══════════════════════════════════════════
//  Init
// ══════════════════════════════════════════

async function init() {
  const apiKeyInput = $('#dashboard-api-key');
  if (apiKeyInput) {
    apiKeyInput.value = getApiKey();
    apiKeyInput.addEventListener('change', (e) => setApiKey(e.target.value));
    apiKeyInput.addEventListener('blur', (e) => setApiKey(e.target.value));
  }

  updateClock();
  setInterval(updateClock, 1000);

  try {
    const data = await apiFetch('/api/status');
    setBadge(data.current_status || 'idle');
    if (data.current_status === 'running') {
      startPolling();
    }
  } catch (e) {
    setBadge('offline');
  }

  fetchHistory();
  fetchSessionStatus();
}

// ══════════════════════════════════════════
//  Session
// ══════════════════════════════════════════

let _sessionPollTimer = null;

async function fetchSessionStatus() {
  try {
    const data = await apiFetch('/api/session/status');
    _renderSessionStatus(data);
    // Poll faster while login is running
    if (data.login_running) {
      clearTimeout(_sessionPollTimer);
      _sessionPollTimer = setTimeout(fetchSessionStatus, 1500);
    }
  } catch {
    _setSessionUI('error', '⚠ Serviço indisponível', false);
  }
}

function _renderSessionStatus(data) {
  const btnLogin = $('#btn-session-login');
  const btnVerify = $('#btn-session-verify');

  if (data.login_running) {
    _setSessionUI('running', 'Aguardando login no browser…', true);
    btnLogin.disabled = true;
    btnLogin.textContent = 'Aguardando…';
    btnVerify.disabled = true;
    return;
  }

  btnLogin.disabled = false;
  btnLogin.textContent = 'Fazer Login';
  btnVerify.disabled = false;

  if (!data.file_exists) {
    _setSessionUI('missing', 'Sem sessão salva', false);
    return;
  }

  if (data.last_login_ok === false) {
    _setSessionUI('error', 'Último login falhou', false);
    return;
  }

  const when = data.modified_at ? ' · ' + fmtDate(data.modified_at) : '';
  _setSessionUI('ok', 'Sessão salva' + when, false);
}

function _setSessionUI(state, text, spinning) {
  const dot = $('#session-dot');
  const label = $('#session-status-text');
  const colors = { ok: 'var(--green)', missing: 'var(--text3)', running: 'var(--amber)', error: 'var(--red)', unknown: 'var(--text3)' };
  dot.style.background = colors[state] || colors.unknown;
  dot.style.animation = spinning ? 'pulse 1s infinite' : '';
  label.textContent = text;
}

async function sessionLogin() {
  const btn = $('#btn-session-login');
  btn.disabled = true;
  try {
    const apiKey = getApiKey();
    const headers = apiKey ? { 'X-API-Key': apiKey } : {};
    const res = await fetch(`${API}/api/session/login`, { method: 'POST', headers });
    if (res.status === 409) {
      toast('Login já está em andamento', 'warning');
    } else if (res.status === 202) {
      toast('Browser aberto — complete o login no PJe', 'info');
      _setSessionUI('running', 'Aguardando login no browser…', true);
      _sessionPollTimer = setTimeout(fetchSessionStatus, 2000);
    } else {
      const d = await res.json().catch(() => ({}));
      toast(d.error || 'Erro ao iniciar login', 'error');
      btn.disabled = false;
    }
  } catch {
    toast('Falha na requisição de login', 'error');
    btn.disabled = false;
  }
}

async function sessionVerify() {
  const btn = $('#btn-session-verify');
  btn.disabled = true;
  btn.textContent = 'Verificando…';
  _setSessionUI('running', 'Abrindo browser headless…', true);
  try {
    const apiKey = getApiKey();
    const headers = apiKey ? { 'X-API-Key': apiKey } : {};
    const res = await fetch(`${API}/api/session/verify`, { method: 'POST', headers });
    const data = await res.json().catch(() => ({}));
    if (data.valid) {
      _setSessionUI('ok', 'Sessão válida ✓', false);
      toast('Sessão PJe válida', 'success');
    } else {
      _setSessionUI('error', 'Sessão expirada', false);
      toast('Sessão expirada — faça login novamente', 'warning');
    }
  } catch {
    toast('Erro ao verificar sessão', 'error');
    await fetchSessionStatus();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Verificar';
  }
}

// Boot
document.addEventListener('DOMContentLoaded', init);
