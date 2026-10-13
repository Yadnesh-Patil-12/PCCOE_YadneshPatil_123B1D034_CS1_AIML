/* HLD Assistant front end - plain JavaScript, no libraries, works fully offline. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rx = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const S = { token: sessionStorage.getItem('hld_token'), user: null, project: null, projects: [], thread: [] };
const canReview = () => S.user && ['architect', 'admin'].includes(S.user.role);
const canExport = () => S.user && ['architect', 'admin', 'tester'].includes(S.user.role);

/* ---------------------------------------------------------------- helpers */
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(S.token ? { Authorization: 'Bearer ' + S.token } : {}) };
  const res = await fetch('/api' + path, { method: opts.method || 'GET', headers: { ...headers, ...(opts.headers || {}) }, body: opts.rawBody ?? (opts.body ? JSON.stringify(opts.body) : undefined) });
  let data = null;
  try { data = await res.json(); } catch (e) { /* non JSON */ }
  if (res.status === 401 && path !== '/login') { signOut(); throw new Error('Session expired. Please sign in again.'); }
  if (!res.ok) throw new Error((data && data.error) || 'Request failed (' + res.status + ')');
  return data;
}
let toastTimer;
function toast(msg, bad = false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = 'toast', 3200);
}
async function download(path, filename) {
  try {
    const res = await fetch('/api' + path, { headers: { Authorization: 'Bearer ' + S.token } });
    if (!res.ok) throw new Error((await res.json()).error);
    const blob = await res.blob(); const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = filename; a.click(); URL.revokeObjectURL(a.href);
  } catch (e) { toast(e.message, true); }
}
const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleString() : '';
const pill = (txt, cls = '') => `<span class="pill ${esc(cls || txt)}">${esc(txt)}</span>`;
const src = p => `${esc(p.doc_id)} v${esc(p.version)} section ${esc(p.sec_id)}, page ${esc(p.page)}`;
function loading(el) { el.innerHTML = '<div class="card grid"><div class="skeleton" style="width:60%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>'; }
function failure(el, e) { el.innerHTML = `<div class="empty"><strong>Something went wrong</strong><p>${esc(e.message)}</p></div>`; }

/* ---------------------------------------------------------------- auth */
function showLogin(msg = '') { $('#app').hidden = true; $('#login').hidden = false; $('#lErr').textContent = msg; }
function signOut() { S.token = null; sessionStorage.removeItem('hld_token'); showLogin(); }
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault(); $('#lErr').textContent = '';
  try {
    const r = await api('/login', { method: 'POST', body: { username: $('#lUser').value, password: $('#lPass').value } });
    S.token = r.token; sessionStorage.setItem('hld_token', r.token); await boot();
  } catch (err) { $('#lErr').textContent = err.message; }
});
$$('#demoUsers .chip').forEach(b => b.onclick = () => { $('#lUser').value = b.dataset.u; $('#lPass').value = 'demo123'; });
$('#logout').onclick = async () => { try { await api('/logout', { method: 'POST' }); } catch (e) { } signOut(); };
$('#menuBtn').onclick = () => $('#rail').classList.toggle('open');

async function boot() {
  try {
    S.user = await api('/me'); S.projects = await api('/projects');
  } catch (e) { return showLogin(); }
  S.project = S.project && S.projects.some(p => p.id === S.project) ? S.project : (S.projects[0] || {}).id;
  $('#login').hidden = true; $('#app').hidden = false;
  $('#whoName').textContent = S.user.display; $('#whoRole').textContent = 'Role: ' + S.user.role;
  $('#navAudit').hidden = !canReview();
  const sel = $('#project');
  sel.innerHTML = S.projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)} (${esc(p.id)})</option>`).join('');
  sel.value = S.project;
  sel.onchange = () => { S.project = sel.value; S.thread = []; render(); };
  try { const h = await fetch('/api/health').then(r => r.json());
    $('#sysInfo').innerHTML = `Embeddings: ${esc(h.embedding)}<br>Vector store: ${esc(h.vector_store)}<br>LLM: ${esc(h.llm)}<br>Network: ${esc(h.external_calls)}`; } catch (e) { }
  if (!location.hash) location.hash = '#/ask';
  render();
}

/* ---------------------------------------------------------------- router */
const VIEWS = { ask: ['Ask the documents', viewAsk], architecture: ['Architecture inventory', viewArch], checks: ['Consistency checks', viewChecks],
  compare: ['Compare revisions', viewCompare], library: ['Documents', viewLibrary], evaluation: ['Evaluation results', viewEval], audit: ['Audit trail', viewAudit] };
window.addEventListener('hashchange', render);
async function render() {
  if (!S.user) return;
  const name = (location.hash.replace('#/', '') || 'ask').split('?')[0];
  const [title, fn] = VIEWS[name] || VIEWS.ask;
  $('#title').textContent = title; document.title = title + ' - HLD Assistant';
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.v === name));
  $('#rail').classList.remove('open');
  const el = $('#view'); loading(el);
  try { await fn(el); } catch (e) { failure(el, e); }
  refreshBadge();
}
async function refreshBadge() {
  try { const s = await api(`/projects/${S.project}/stats`); $('#navFindings').textContent = s.open_findings || ''; } catch (e) { }
}

/* ---------------------------------------------------------------- ASK */
const EXAMPLES = {
  'PRJ-BODY': ['Which components consume SIG_VehicleSpeed?', 'What does SWC_CentralLockArb do?', 'If SWC_SpeedSignalAdapter changes, which components are impacted?',
    'Describe the remote unlock flow.', 'Where is SWC_BcmGateway defined?', 'What is the safety level of SWC_AirbagCtrl?'],
  'PRJ-ENERGY': ['Which components consume SIG_CellVoltage?', 'What does SWC_SocEstimator depend on?', 'If SWC_CellMonitor changes, which components are impacted?',
    'Describe the charge limit calculation flow.', 'What happens to cooling when the cell temperature is lost?', 'What is the capital of France?']
};
const STOP = new Set('what which does with that this from into when where have your their about there should would could'.split(' '));
function highlight(text, q) {
  const words = [...new Set((q.toLowerCase().match(/[a-z0-9_]{4,}/g) || []).filter(w => !STOP.has(w)))];
  let out = esc(text);
  if (!words.length) return out;
  return out.replace(new RegExp('(' + words.map(rx).join('|') + ')', 'gi'), '<mark>$1</mark>');
}
async function viewAsk(el) {
  const ex = EXAMPLES[S.project] || [];
  el.innerHTML = `
    <div class="card askbox">
      <label for="qin">Your question about the approved documents of this project</label>
      <textarea id="qin" placeholder="For example: Which components consume SIG_VehicleSpeed?" maxlength="600"></textarea>
      <div class="row"><button class="btn primary" id="askBtn">Ask</button><span class="muted" style="font-size:13px">Answers only use approved documents and always show their sources. Press Ctrl+Enter to ask.</span></div>
      <div class="examples">${ex.map(x => `<button class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join('')}</div>
    </div>
    <div class="thread" id="thread"></div>
    <div class="card history"><div class="row"><h3>Earlier questions</h3><span class="spacer"></span><button class="btn small" id="reloadHist">Refresh</button></div><div id="hist" class="muted">Loading...</div></div>`;
  const ta = $('#qin');
  const ask = async q => {
    q = (q || ta.value).trim(); if (q.length < 3) return toast('Type a question first', true);
    const slot = document.createElement('div'); slot.className = 'card qa'; slot.innerHTML = `<div class="q">${esc(q)}</div><div class="skeleton"></div><div class="skeleton" style="width:70%"></div>`;
    $('#thread').prepend(slot); $('#askBtn').disabled = true;
    try { const r = await api(`/projects/${S.project}/ask`, { method: 'POST', body: { question: q } }); slot.replaceWith(answerCard(r)); ta.value = ''; loadHistory(); }
    catch (e) { slot.innerHTML = `<div class="q">${esc(q)}</div><p class="bad">${esc(e.message)}</p>`; }
    $('#askBtn').disabled = false;
  };
  $('#askBtn').onclick = () => ask();
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) ask(); });
  $$('.examples .chip').forEach(c => c.onclick = () => { ta.value = c.dataset.q; ask(c.dataset.q); });
  $('#reloadHist').onclick = loadHistory;
  loadHistory(); ta.focus();
}
function answerCard(r) {
  const card = document.createElement('div'); card.className = 'card qa';
  const cits = Object.fromEntries((r.citations || []).map(c => [c.tag, c]));
  const used = new Set((r.answer.match(/\[(S\d+)\]/g) || []).map(t => t.slice(1, -1)));
  const lineHtml = t => esc(t).replace(/\[(S\d+)\]/g, (m, tag) => {
    const c = cits[tag]; return c ? `<button class="tag" data-tag="${tag}" title="${esc(src(c))}">${tag} &sect;${esc(c.sec_id)} p${esc(c.page)}</button>` : m;
  });
  let body;
  if (r.refused) {
    body = `<div class="refusal"><strong>No answer given</strong><p style="margin:6px 0 0">${esc(r.answer)}</p><p class="muted" style="margin:8px 0 0;font-size:13px">Try naming a component, signal or interface that exists in this project, or check the Documents page for what has been approved.</p></div>`;
  } else {
    body = `<div class="answer">${r.answer.split('\n').filter(Boolean).map(l => `<div class="aline"><span class="t">${lineHtml(l)}</span></div>`).join('')}</div>`;
  }
  const pct = Math.round((r.confidence || 0) * 100);
  const cls = pct >= 60 ? '' : pct >= 40 ? 'med' : 'low';
  const meta = r.refused ? `<div class="meta"><span>${r.latency_ms} ms</span></div>` : `
    <div class="meta">
      <span class="row" style="gap:8px">Confidence <span class="meter ${cls}"><i style="width:${pct}%"></i></span> <strong>${esc(r.label)} (${pct}%)</strong></span>
      <span>Grounded statements: <strong>${Math.round((r.groundedness ?? 1) * 100)}%</strong></span>
      ${r.tool ? pill('Structured lookup: ' + r.tool.replace(/_/g, ' '), 'tool') : pill('Retrieved passages', 'open')}
      <span>${esc(r.mode)}</span><span>${r.latency_ms} ms</span>
    </div>`;
  const ordered = (r.citations || []).slice().sort((a, b) => (used.has(b.tag) - used.has(a.tag)));
  const ev = r.refused ? '' : `<div class="evid"><h4 class="muted" style="font-size:13px;font-weight:600">Sources</h4>${ordered.map(c => `
    <details class="ev" data-tag="${c.tag}"><summary><span class="tag" style="cursor:inherit">${c.tag}</span><strong>${esc(c.sec_title)}</strong>
      <span class="muted">${esc(c.doc_id)} v${esc(c.version)} &middot; section ${esc(c.sec_id)} &middot; page ${esc(c.page)}${used.has(c.tag) ? '' : ' &middot; related passage'}</span></summary>
      <pre>${highlight(c.text, r.question)}</pre></details>`).join('')}</div>`;
  const actions = r.refused ? '' : `<div class="actions" data-id="${r.id}">
      ${canReview() ? `<button class="btn small" data-s="accepted">Accept</button><button class="btn small danger" data-s="rejected">Reject</button>` : `<span class="muted" style="font-size:13px">An architect reviews saved answers before they are used in a design decision.</span>`}
      <span class="status">${pill('pending')}</span></div>`;
  card.innerHTML = `<div class="q">${esc(r.question)}</div>${body}${meta}${ev}${actions}`;
  $$('.tag[data-tag]', card).forEach(b => b.onclick = () => {
    const d = $(`.ev[data-tag="${b.dataset.tag}"]`, card); if (!d) return;
    $$('.ev', card).forEach(x => x.classList.remove('hot')); d.open = true; d.classList.add('hot'); d.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
  $$('.actions button[data-s]', card).forEach(b => b.onclick = async () => {
    try { await api(`/answers/${r.id}/review`, { method: 'POST', body: { status: b.dataset.s } }); $('.status', card).innerHTML = pill(b.dataset.s); toast('Saved as ' + b.dataset.s); loadHistory(); }
    catch (e) { toast(e.message, true); }
  });
  return card;
}
async function loadHistory() {
  const box = $('#hist'); if (!box) return;
  try {
    const rows = await api(`/projects/${S.project}/answers`);
    box.innerHTML = rows.length ? rows.slice(0, 12).map(a => `<div class="hrow"><div><strong>${esc(a.question)}</strong><div class="muted" style="font-size:13px">${esc(a.username)} &middot; ${fmtTime(a.ts)} &middot; ${a.refused ? 'refused' : esc(a.label) + ' confidence'}</div></div><div>${pill(a.refused ? 'refused' : a.review_status, a.refused ? 'low' : a.review_status)}</div></div>`).join('') : '<div class="empty">No questions yet. Pick an example above.</div>';
  } catch (e) { box.textContent = e.message; }
}

/* ---------------------------------------------------------------- ARCHITECTURE */
let archTab = 'map';
async function viewArch(el) {
  const st = await api(`/projects/${S.project}/stats`);
  el.innerHTML = `
    <div class="grid g4" style="margin-bottom:18px">
      <div class="tile"><b>${st.components}</b><span>components</span></div><div class="tile"><b>${st.ports}</b><span>ports</span></div>
      <div class="tile"><b>${st.interfaces}</b><span>interfaces</span></div><div class="tile"><b>${st.signals}</b><span>signals</span></div></div>
    <div class="tabs" id="tabs">${[['map', 'Dependency map'], ['components', 'Components'], ['ports', 'Ports'], ['interfaces', 'Interfaces'], ['signals', 'Signals']].map(([k, n]) => `<button data-t="${k}" class="${k === archTab ? 'on' : ''}">${n}</button>`).join('')}</div>
    <div id="tabBody"></div>`;
  $$('#tabs button').forEach(b => b.onclick = () => { archTab = b.dataset.t; $$('#tabs button').forEach(x => x.classList.toggle('on', x === b)); loadTab(); });
  loadTab();
}
async function loadTab() {
  const body = $('#tabBody'); loading(body);
  try { archTab === 'map' ? await drawMap(body) : await drawTable(body, archTab); } catch (e) { failure(body, e); }
}
const COLS = {
  components: [['id', 'Component', 'mono'], ['type', 'Type'], ['level', 'Safety level'], ['depends_on', 'Depends on', 'mono'], ['tests', 'Verification', 'mono']],
  ports: [['component', 'Component', 'mono'], ['name', 'Port', 'mono'], ['direction', 'Direction'], ['interface', 'Interface', 'mono'], ['timing', 'Timing']],
  interfaces: [['id', 'Interface', 'mono'], ['paradigm', 'Paradigm'], ['data_elements', 'Data elements', 'mono'], ['description', 'Description']],
  signals: [['id', 'Signal', 'mono'], ['dtype', 'Data type'], ['unit', 'Unit'], ['range', 'Range'], ['period', 'Update period']]
};
async function drawTable(body, kind) {
  let rows = await api(`/projects/${S.project}/inventory?kind=${kind}`);
  rows = rows.map(r => ({ ...r, timing: r.timing_kind ? `${r.timing_kind} ${r.timing_ms} ms` : '' }));
  body.innerHTML = `<div class="row" style="margin-bottom:12px"><input id="flt" placeholder="Filter ${kind}..." style="max-width:320px" aria-label="Filter"><span class="spacer"></span>
    ${canExport() ? `<button class="btn small" id="expCsv">Export CSV</button><button class="btn small" id="expJson">Export JSON</button>` : ''}</div>
    <div class="tablewrap"><table><thead><tr>${COLS[kind].map(c => `<th>${c[1]}</th>`).join('')}<th>Source</th></tr></thead><tbody id="tb"></tbody></table></div>`;
  const draw = () => {
    const f = $('#flt').value.toLowerCase();
    const shown = rows.filter(r => !f || JSON.stringify(r).toLowerCase().includes(f));
    $('#tb').innerHTML = shown.map((r, i) => `<tr class="click" data-i="${rows.indexOf(r)}">${COLS[kind].map(c => {
      const v = Array.isArray(r[c[0]]) ? r[c[0]].join(', ') : r[c[0]]; return `<td class="${c[2] || ''}">${esc(v || (c[0] === 'depends_on' || c[0] === 'tests' ? '-' : ''))}</td>`; }).join('')}
      <td class="muted" style="font-size:12.5px">${esc(r.prov.doc_id)} &sect;${esc(r.prov.sec_id)} p${esc(r.prov.page)}</td></tr>`).join('') || `<tr><td colspan="9"><div class="empty">Nothing matches.</div></td></tr>`;
    $$('#tb tr.click').forEach(tr => tr.onclick = () => openDrawer(kind, rows[+tr.dataset.i]));
  };
  $('#flt').oninput = draw; draw();
  if (canExport()) { $('#expCsv').onclick = () => download(`/projects/${S.project}/export?kind=${kind}&format=csv`, `${S.project}_${kind}.csv`);
    $('#expJson').onclick = () => download(`/projects/${S.project}/export?kind=${kind}&format=json`, `${S.project}_${kind}.json`); }
}
async function openDrawer(kind, r) {
  closeDrawer();
  const d = document.createElement('aside'); d.className = 'drawer'; d.id = 'drawer';
  const id = r.id || r.name;
  const fields = Object.entries(r).filter(([k]) => !['prov', 'timing'].includes(k)).map(([k, v]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd class="${k === 'id' ? 'mono' : ''}">${esc(Array.isArray(v) ? v.join(', ') || '-' : v || '-')}</dd>`).join('');
  d.innerHTML = `<div class="row"><h3 class="mono">${esc(id)}</h3><span class="spacer"></span><button class="btn small" id="dClose">Close</button></div>
    <p class="muted">Found in ${src(r.prov)} (${esc(r.prov.title)})</p><dl class="kv">${fields}</dl><div id="imp"></div>`;
  document.body.appendChild(d); $('#dClose').onclick = closeDrawer;
  if (kind === 'components' || kind === 'signals') {
    try {
      const imp = await api(`/projects/${S.project}/impact?entity=${encodeURIComponent(r.id)}`);
      $('#imp').innerHTML = `<h4 style="margin:18px 0 6px">If this changes</h4>` + (imp.transitive.length ?
        `<p style="margin:0 0 6px">Directly impacted: <span class="mono">${esc(imp.direct.join(', ') || 'none')}</span></p><p style="margin:0">All impacted: <span class="mono">${esc(imp.transitive.join(', '))}</span></p>` : '<p class="muted">No downstream components found.</p>');
    } catch (e) { }
  }
}
function closeDrawer() { const d = $('#drawer'); if (d) d.remove(); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

async function drawMap(body) {
  const g = await api(`/projects/${S.project}/graph`);
  if (!g.nodes.length) { body.innerHTML = '<div class="empty">No components extracted yet. Upload a document on the Documents page.</div>'; return; }
  // layering: longest path from sources, cycle safe
  const ids = g.nodes.map(n => n.id), layer = Object.fromEntries(ids.map(i => [i, 0]));
  for (let it = 0; it < ids.length; it++) { let ch = false; g.edges.forEach(e => { if (layer[e.target] < layer[e.source] + 1 && layer[e.source] + 1 < ids.length) { layer[e.target] = layer[e.source] + 1; ch = true; } }); if (!ch) break; }
  // pull pure sources next to their first consumer so long edges do not cut across the diagram
  ids.forEach(i => { const incoming = g.edges.some(e => e.target === i); const outs = g.edges.filter(e => e.source === i).map(e => layer[e.target]);
    if (!incoming && outs.length) layer[i] = Math.max(0, Math.min(...outs) - 1); });
  const layers = {}; ids.forEach(i => (layers[layer[i]] = layers[layer[i]] || []).push(i));
  const W = 176, H = 50, GX = 110, GY = 28, pos = {};
  const maxRows = Math.max(...Object.values(layers).map(l => l.length));
  const order = Object.keys(layers).map(Number).sort((a, b) => a - b);
  order.forEach(L => { const col = layers[L]; col.sort(); const off = (maxRows - col.length) * (H + GY) / 2; col.forEach((id, k) => pos[id] = { x: 20 + L * (W + GX), y: 20 + off + k * (H + GY) }); });
  // a few barycentre passes to reduce crossings
  for (let pass = 0; pass < 4; pass++) order.slice(1).forEach(L => {
    const bc = id => { const p = g.edges.filter(e => e.target === id).map(e => pos[e.source].y); return p.length ? p.reduce((a, b) => a + b) / p.length : pos[id].y; };
    const col = layers[L].slice().sort((a, b) => bc(a) - bc(b)); const ys = layers[L].map(id => pos[id].y).sort((a, b) => a - b); col.forEach((id, k) => pos[id].y = ys[k]);
  });
  const w = 40 + order.length * (W + GX) - GX, h = 40 + maxRows * (H + GY) - GY;
  const nodeCls = n => !n.defined ? 'undef' : n.level.replace('ASIL ', '');
  const edges = g.edges.map((e, i) => { const a = pos[e.source], b = pos[e.target]; const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
    const back = x2 <= x1; const d = back ? `M${x1} ${y1} C ${x1 + 50} ${y1 - 60}, ${x2 - 50} ${y2 - 60}, ${x2} ${y2}` : `M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
    return `<path class="edge ${e.kind === 'depends' ? 'dep' : ''}" data-s="${esc(e.source)}" data-t="${esc(e.target)}" d="${d}" marker-end="url(#arr)"><title>${esc(e.source)} to ${esc(e.target)}${e.signal ? ' via ' + esc(e.signal) : ' (declared dependency)'}</title></path>`; }).join('');
  const nodes = g.nodes.map(n => `<g class="node ${nodeCls(n)}" data-id="${esc(n.id)}" transform="translate(${pos[n.id].x},${pos[n.id].y})" tabindex="0"><rect width="${W}" height="${H}" rx="8"/><text x="10" y="21" font-weight="600">${esc(n.id.length > 24 ? n.id.slice(0, 23) + '...' : n.id)}</text><text class="lv" x="10" y="38">${esc(n.defined ? n.level + ' - ' + n.type : 'referenced but not defined')}</text><title>${esc(n.id)}</title></g>`).join('');
  body.innerHTML = `<div class="legend"><span><i></i>signal flow (producer to consumer)</span><span><i class="d"></i>declared dependency</span><span>Border: grey = QM, blue = ASIL A/B, violet = ASIL C/D, red dashed = not defined</span></div>
    <div class="graph"><svg viewBox="0 0 ${w} ${h}" style="max-width:${w}px" role="img" aria-label="Component dependency map"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#1646C2"/></marker></defs>${edges}${nodes}</svg></div>
    <p class="muted" style="font-size:13px">Hover a component to highlight its connections. Click it to see what is impacted if it changes.</p>`;
  const focus = id => { $$('.edge', body).forEach(p => { const on = p.dataset.s === id || p.dataset.t === id; p.classList.toggle('hl', !!id && on); p.classList.toggle('dim', !!id && !on); });
    const near = new Set([id]); g.edges.forEach(e => { if (e.source === id) near.add(e.target); if (e.target === id) near.add(e.source); });
    $$('.node', body).forEach(n => n.classList.toggle('dim', !!id && !near.has(n.dataset.id))); };
  $$('.node', body).forEach(n => { n.onmouseenter = () => focus(n.dataset.id); n.onmouseleave = () => focus(null); n.onfocus = () => focus(n.dataset.id); n.onblur = () => focus(null);
    n.onclick = async () => { const comps = await api(`/projects/${S.project}/inventory?kind=components`); const c = comps.find(x => x.id === n.dataset.id);
      if (c) openDrawer('components', c); else { closeDrawer(); const d = document.createElement('aside'); d.className = 'drawer'; d.id = 'drawer'; d.innerHTML = `<div class="row"><h3 class="mono">${esc(n.dataset.id)}</h3><span class="spacer"></span><button class="btn small" id="dClose">Close</button></div><p>This component is referenced by other components but no approved document defines it. See the Checks page.</p>`; document.body.appendChild(d); $('#dClose').onclick = closeDrawer; } }; });
}

/* ---------------------------------------------------------------- CHECKS */
let fSev = 'all', fOpen = true;
async function viewChecks(el) {
  const rows = await api(`/projects/${S.project}/findings`);
  const open = rows.filter(r => r.status === 'open');
  const n = s => open.filter(r => r.severity === s).length;
  const shown = rows.filter(r => (fSev === 'all' || r.severity === fSev) && (!fOpen || r.status === 'open'));
  el.innerHTML = `
    <div class="grid g4" style="margin-bottom:16px"><div class="tile warn"><b>${n('high')}</b><span>high severity open</span></div><div class="tile"><b>${n('medium')}</b><span>medium open</span></div>
      <div class="tile"><b>${n('low')}</b><span>informational open</span></div><div class="tile"><b>${rows.filter(r => r.status === 'accepted').length}</b><span>accepted as real issues</span></div></div>
    <div class="row" style="margin-bottom:14px">
      <div class="chips">${['all', 'high', 'medium', 'low'].map(s => `<button class="chip ${fSev === s ? 'on' : ''}" data-sev="${s}">${s === 'all' ? 'All severities' : s[0].toUpperCase() + s.slice(1)}</button>`).join('')}</div>
      <label class="row" style="flex-direction:row;gap:6px"><input type="checkbox" id="onlyOpen" style="width:auto" ${fOpen ? 'checked' : ''}> Open only</label><span class="spacer"></span>
      ${canExport() ? '<button class="btn small" id="expF">Export CSV</button>' : ''}${canReview() ? '<button class="btn primary small" id="runC">Run checks again</button>' : ''}</div>
    <div class="grid" id="flist">${shown.map(f => `
      <article class="finding ${esc(f.severity)} ${f.status !== 'open' ? 'closed' : ''}" data-id="${f.id}"><div class="bar"></div><div class="body">
        <div class="row">${pill(f.severity)}<strong>${esc(f.title)}</strong><span class="spacer"></span>${pill(f.status)}</div>
        <div>${esc(f.detail)}</div>
        <div class="row">${f.evidence.map(p => `<span class="tag" style="cursor:default">${esc(p.doc_id)} &sect;${esc(p.sec_id)} p${esc(p.page)}</span>`).join('')}<span class="muted" style="font-size:12.5px">Rule: ${esc(f.rule.replace(/_/g, ' ').toLowerCase())}</span></div>
        ${f.note ? `<div class="muted" style="font-size:13px">Reviewer note (${esc(f.reviewer)}): ${esc(f.note)}</div>` : ''}
        ${canReview() ? `<div class="row"><input class="note" placeholder="Reviewer note (optional)" style="max-width:340px" aria-label="Reviewer note">
          <button class="btn small" data-s="accepted">Confirm issue</button><button class="btn small" data-s="rejected">Not an issue</button>${f.status !== 'open' ? '<button class="btn small" data-s="open">Reopen</button>' : ''}</div>` : ''}
      </div></article>`).join('') || '<div class="empty">No findings match the filter.</div>'}</div>`;
  $$('.chip[data-sev]').forEach(c => c.onclick = () => { fSev = c.dataset.sev; render(); });
  $('#onlyOpen').onchange = e => { fOpen = e.target.checked; render(); };
  if ($('#runC')) $('#runC').onclick = async () => { try { const r = await api(`/projects/${S.project}/findings/run`, { method: 'POST' }); toast(r.count + ' findings checked'); render(); } catch (e) { toast(e.message, true); } };
  if ($('#expF')) $('#expF').onclick = () => download(`/projects/${S.project}/export?kind=findings&format=csv`, `${S.project}_findings.csv`);
  $$('.finding button[data-s]').forEach(b => b.onclick = async () => {
    const card = b.closest('.finding'); try { await api(`/findings/${card.dataset.id}/review`, { method: 'POST', body: { status: b.dataset.s, note: $('.note', card).value } }); toast('Saved'); render(); } catch (e) { toast(e.message, true); } });
}

/* ---------------------------------------------------------------- COMPARE */
async function viewCompare(el) {
  const docs = await api(`/projects/${S.project}/documents`);
  const opts = docs.map(d => `<option value="${esc(d.doc_id)}:${esc(d.version)}">${esc(d.doc_id)} v${esc(d.version)} (${esc(d.status)})</option>`).join('');
  const fams = {}; docs.forEach(d => (fams[d.doc_id] = fams[d.doc_id] || []).push(d.version));
  const fam = Object.entries(fams).find(([, v]) => v.length > 1);
  el.innerHTML = `<div class="card"><div class="row"><label style="min-width:230px">Older revision<select id="cA">${opts}</select></label><label style="min-width:230px">Newer revision<select id="cB">${opts}</select></label>
      <button class="btn primary" id="cGo" style="align-self:end">Compare</button></div><p class="hint" style="margin-top:10px">Shows added, removed and changed components, ports, interfaces and signals, plus the changed text.</p></div><div id="cOut" style="margin-top:18px"></div>`;
  if (fam) { const [id, v] = fam; $('#cA').value = `${id}:${v[0]}`; $('#cB').value = `${id}:${v[v.length - 1]}`; }
  $('#cGo').onclick = async () => {
    const out = $('#cOut'); loading(out);
    try {
      const r = await api(`/projects/${S.project}/compare?a=${encodeURIComponent($('#cA').value)}&b=${encodeURIComponent($('#cB').value)}`);
      const nameOf = { components: 'Components', ports: 'Ports', interfaces: 'Interfaces', signals: 'Signals' };
      const chg = c => Object.entries(c.changes).map(([k, v]) => `<div>${esc(k)}: <span class="bad">${esc(Array.isArray(v.old) ? v.old.join(', ') : v.old)}</span> &rarr; <span class="good">${esc(Array.isArray(v.new) ? v.new.join(', ') : v.new)}</span></div>`).join('');
      out.innerHTML = `<div class="grid g4">${Object.keys(nameOf).map(k => `<div class="tile"><b>+${r.summary[k].added} / -${r.summary[k].removed} / ~${r.summary[k].changed}</b><span>${nameOf[k]} added / removed / changed</span></div>`).join('')}</div>
        <div class="grid g2" style="margin-top:16px">${Object.keys(nameOf).map(k => { const v = r.inventory[k]; if (!v.added.length && !v.removed.length && !v.changed.length) return '';
          return `<div class="card"><h3 style="margin-bottom:10px">${nameOf[k]}</h3>${v.added.map(x => `<div class="good mono">+ ${esc(x.id)}</div>`).join('')}${v.removed.map(x => `<div class="bad mono">- ${esc(x.id)}</div>`).join('')}
          ${v.changed.map(c => `<div class="change" style="margin-top:8px"><strong class="mono">~ ${esc(c.id)}</strong>${chg(c)}</div>`).join('')}</div>`; }).join('')}</div>
        <h3 style="margin:22px 0 10px">Changed text by section</h3><div class="grid">${r.sections.map(s => `<div class="card"><div class="row"><strong>${esc(s.section)}</strong>${pill(s.status, s.status === 'added' ? 'low' : s.status === 'removed' ? 'high' : 'medium')}</div>
          ${s.diff.length ? `<div class="diff">${s.diff.map(l => `<div class="${l[0] === '+' ? 'p' : 'm'}">${esc(l)}</div>`).join('')}</div>` : ''}</div>`).join('') || '<div class="empty">No text differences.</div>'}</div>`;
    } catch (e) { failure(out, e); }
  };
  if (fam) $('#cGo').click();
}

/* ---------------------------------------------------------------- LIBRARY */
async function viewLibrary(el) {
  const docs = await api(`/projects/${S.project}/documents`);
  el.innerHTML = `
    <div class="tablewrap"><table><thead><tr><th>Document</th><th>Version</th><th>Title</th><th>Status</th><th>Pages</th><th>Chunks</th><th>Checksum</th><th>Added</th>${canReview() ? '<th></th>' : ''}</tr></thead><tbody>
    ${docs.map(d => `<tr><td class="mono">${esc(d.doc_id)}</td><td>${esc(d.version)}</td><td>${esc(d.title)}</td><td>${pill(d.status)}</td><td>${d.pages}</td><td>${d.chunks}</td><td class="mono" title="${esc(d.sha256)}">${esc(d.sha256.slice(0, 10))}</td><td class="muted">${fmtTime(d.ingested_at)}<br>${esc(d.uploaded_by)}</td>
    ${canReview() ? `<td><button class="btn small danger" data-d="${esc(d.doc_id)}" data-v="${esc(d.version)}">Remove</button></td>` : ''}</tr>`).join('') || '<tr><td colspan="9"><div class="empty">No documents yet.</div></td></tr>'}</tbody></table></div>
    <p class="hint" style="margin-top:10px">Only <strong>approved</strong> documents are searched. When you add a newer version of a document it becomes the approved one and older versions are marked superseded (they stay available for comparison). Text that looks like an instruction to an AI is flagged and left out of answers.</p>
    ${canReview() ? `<div class="card" style="margin-top:20px"><h3>Add a document</h3>
      <div class="drop" id="drop" tabindex="0">Drop a PDF, Markdown or text file here, or click to choose<input type="file" id="file" accept=".pdf,.md,.txt" hidden></div>
      <div class="grid g4" style="margin-top:12px"><label>Document ID<input id="uId" placeholder="HLD-XYZ-010"></label><label>Version<input id="uVer" value="1.0"></label><label style="grid-column:span 2">Title<input id="uTitle" placeholder="Short document title"></label></div>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="uGo" disabled>Add and index</button><span id="uName" class="muted"></span></div></div>`
      : `<div class="empty" style="margin-top:20px">Only architects can add or remove documents.</div>`}`;
  $$('button[data-d]').forEach(b => b.onclick = async () => { if (!confirm(`Remove ${b.dataset.d} v${b.dataset.v}? Its chunks and findings are re-calculated.`)) return;
    try { await api(`/projects/${S.project}/documents/${encodeURIComponent(b.dataset.d)}/${encodeURIComponent(b.dataset.v)}`, { method: 'DELETE' }); toast('Removed'); render(); } catch (e) { toast(e.message, true); } });
  if (!canReview()) return;
  let file = null; const drop = $('#drop'), inp = $('#file');
  const pick = f => { if (!f) return; file = f; $('#uName').textContent = f.name + ' (' + Math.round(f.size / 1024) + ' KB)'; $('#uGo').disabled = false;
    if (!$('#uId').value) $('#uId').value = f.name.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 40); if (!$('#uTitle').value) $('#uTitle').value = f.name.replace(/\.[^.]+$/, ''); };
  drop.onclick = () => inp.click(); drop.onkeydown = e => { if (e.key === 'Enter') inp.click(); }; inp.onchange = () => pick(inp.files[0]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); }; drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); pick(e.dataTransfer.files[0]); };
  $('#uGo').onclick = async () => {
    $('#uGo').disabled = true; $('#uGo').textContent = 'Indexing...';
    const q = new URLSearchParams({ filename: file.name, doc_id: $('#uId').value, version: $('#uVer').value, title: $('#uTitle').value });
    try { const r = await api(`/projects/${S.project}/documents?${q}`, { method: 'POST', rawBody: file, headers: { 'Content-Type': 'application/octet-stream' } }); toast(`Indexed ${r.pages} pages into ${r.chunks} chunks`); render(); }
    catch (e) { toast(e.message, true); $('#uGo').disabled = false; $('#uGo').textContent = 'Add and index'; }
  };
}

/* ---------------------------------------------------------------- EVALUATION */
async function viewEval(el) {
  const { summary: s, rows } = await api('/evaluation');
  const t = (v, l) => `<div class="tile"><b>${v}</b><span>${l}</span></div>`;
  const pc = x => Math.round(x * 1000) / 10 + '%';
  el.innerHTML = `
    <p class="muted" style="margin-top:0">Run on ${esc(s.generated_at)} with embeddings <strong>${esc(s.configuration.embedding)}</strong>, vector store <strong>${esc(s.configuration.vector_store)}</strong>, LLM <strong>${esc(s.configuration.llm)}</strong>. Questions: ${s.counts.questions} (${s.counts.answerable} answerable, ${s.counts.out_of_scope} out of scope).</p>
    <div class="grid g4">${t(pc(s.answers.answer_pass_rate), 'answers correct and cited')}${t(pc(s.answers.citation_accuracy), 'citation accuracy')}${t(pc(s.refusal.refusal_accuracy), 'out-of-scope questions refused')}
      ${t(pc(s.retrieval.hit_at_3), 'retrieval hit@3 (text search only)')}${t(s.retrieval.mrr, 'retrieval MRR')}${t(pc(s.extraction.macro_f1), 'entity extraction F1')}
      ${t(pc(s.issues.recall), 'seeded issues found (' + s.issues.detected + '/' + s.issues.seeded + ')')}${t(s.latency_ms.p95 + ' ms', 'p95 answer time')}</div>
    <div class="card" style="margin:18px 0"><strong>How to read this.</strong> The documents are synthetic and follow one template, so extraction and issue-detection scores are an upper bound for real documents. Questions marked P are paraphrases without identifiers and are the harder test. Failures are kept in the table below on purpose.</div>
    <div class="row" style="margin-bottom:10px"><div class="chips"><button class="chip on" data-f="all">All</button><button class="chip" data-f="FAIL">Failed</button><button class="chip" data-f="P">Paraphrased</button><button class="chip" data-f="N">Out of scope</button></div></div>
    <div class="tablewrap"><table><thead><tr><th>#</th><th>Question</th><th>Route</th><th>Rank</th><th>Confidence</th><th>Result</th><th>Note</th></tr></thead><tbody id="eb"></tbody></table></div>`;
  const draw = f => { $('#eb').innerHTML = rows.filter(r => f === 'all' || (f === 'FAIL' ? r.result === 'FAIL' : r.id.startsWith(f))).map(r => `<tr><td class="mono">${esc(r.id)}</td><td>${esc(r.question)}</td><td>${esc(r.mode.replace(/_/g, ' '))}</td><td>${esc(r.retrieval_rank)}</td><td>${esc(r.confidence)}</td><td>${pill(r.result)}</td><td class="muted">${esc(r.note)}</td></tr>`).join(''); };
  $$('.chip[data-f]').forEach(c => c.onclick = () => { $$('.chip[data-f]').forEach(x => x.classList.toggle('on', x === c)); draw(c.dataset.f); }); draw('all');
}

/* ---------------------------------------------------------------- AUDIT */
async function viewAudit(el) {
  const rows = await api('/audit');
  el.innerHTML = `<div class="tablewrap"><table><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Project</th><th>Detail</th></tr></thead><tbody>${rows.map(r => `<tr><td class="muted">${fmtTime(r.ts)}</td><td>${esc(r.username)}</td><td>${esc(r.action)}</td><td class="mono">${esc(r.project || '')}</td><td>${esc(r.detail)}</td></tr>`).join('')}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- start */
(async () => { if (S.token) await boot(); else showLogin(); })();
