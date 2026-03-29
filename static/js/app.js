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

  if (phase === 'failed') {
    const detail = p.phase_detail || p.erro || '';
    let html = '<span class="tag tag--failed">Falhou</span>';
    if (detail) html += `<div class="pipeline__detail" title="${esc(detail)}">${esc(detail.substring(0, 80))}</div>`;
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
    if (i > 0) html += '<span class="pipeline__arrow">\u203A</span>';
    let cls = 'pipeline__step--inactive';
    if (phase === 'done' || (phase === 'saving' && step === 'done')) cls = 'pipeline__step--done';
    else if (i < currentIdx) cls = 'pipeline__step--done';
    else if (i === currentIdx) cls = 'pipeline__step--active';
    html += `<span class="pipeline__step ${cls}">${labels[step]}</span>`;
  });
  html += '</div>';

  if (p.phase_detail && phase !== 'done') {
    html += `<div class="pipeline__detail" title="${esc(p.phase_detail)}">${esc(p.phase_detail)}</div>`;
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
  const res = await fetch(API + path, opts);
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
  let done = 0, failed = 0, totalDocs = 0, totalBytes = 0;

  for (const p of Object.values(procs)) {
    if (p.status === 'done') done++;
    if (p.status === 'failed') failed++;
    totalDocs += p.docs_baixados || p.docs || 0;
    totalBytes += p.tamanho_bytes || p.bytes || 0;
  }

  const pct = total > 0 ? Math.round((done + failed) / total * 100) : 0;

  updateKPIs(total, totalDocs, totalBytes, parseInt($('#kpi-batches').textContent) || 0);

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
  $('#progress-label').textContent = `${done + failed} / ${total} processos (${pct}%) \u2014 ${totalDocs} docs, ${fmtBytes(totalBytes)}`;

  renderProcessTable($('#processos-tbody'), procs);
}

// ── Process Table ──

function renderProcessTable(tbody, procs) {
  let html = '';
  for (const [num, p] of Object.entries(procs)) {
    const antigo = isAntigo(num);
    const antigoBadge = antigo ? ' <span class="tag tag--antigo">antigo</span>' : '';
    const docs = p.docs_baixados || p.docs || 0;
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
    let done = 0, totalDocs = 0, totalBytes = 0;
    for (const p of Object.values(procs)) {
      if (p.status === 'done') done++;
      totalDocs += p.docs || p.docs_baixados || 0;
      totalBytes += p.bytes || p.tamanho_bytes || 0;
    }

    const total = Object.keys(procs).length;
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    $('#main-progress').style.width = pct + '%';
    $('#progress-label').textContent = `${done} / ${total} processos (${pct}%) \u2014 ${totalDocs} docs, ${fmtBytes(totalBytes)}`;

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
}

// Boot
document.addEventListener('DOMContentLoaded', init);
