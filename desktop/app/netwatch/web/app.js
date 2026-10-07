/* NetWatch Desktop — UI controller. */
'use strict';

const TOKEN = document.body.dataset.token;
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  tags: [], selected: null, boot: null, markers: {}, track: null, tiles: 'osm',
  window: { from: '', to: '' },   // breadcrumb date filter (YYYY-MM-DD, '' = open)
  lastTrackCount: 0,
};

/* Save a tag's appearance / trail flag and refresh the map straight away. */
async function saveAppearance(id, fields) {
  try {
    await api('/api/tags/update', { method: 'POST', body: { id, ...fields } });
    const { tags } = await api('/api/tags');
    state.tags = tags;
    renderDevices();
    renderMarkers();
    await redrawTrack();
  } catch (e) { oops(e); }
}

/* ───────────────────────────── api ──────────────────────────────────── */
async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { 'X-NetWatch-Token': TOKEN, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/* ──────────────────────────── toasts ────────────────────────────────── */
function toast(title, msg = '', kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `<div class="t">${esc(title)}</div>${msg ? `<div class="m">${esc(msg)}</div>` : ''}`;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'err' ? 9000 : 4500);
}
const oops = (e) => toast('Something went wrong', e.message || String(e), 'err');

/* ──────────────────────────── modal ─────────────────────────────────── */
function modal(html) {
  $('#modal-body').innerHTML = html;
  $('#modal-root').classList.remove('hidden');
}
function closeModal() { $('#modal-root').classList.add('hidden'); $('#modal-body').innerHTML = ''; }
$('#modal-root').addEventListener('click', (e) => { if (e.target.dataset.close !== undefined) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

/* ───────────────────────────── map ──────────────────────────────────── */
/* Basemaps come from the backend, because which ones exist depends on the
 * edition: the retail build offers open sources plus an optional self-hosted
 * tile server, and never Google's scraped tiles. Nothing needs an API key.
 * See netwatch/tileproviders.py. */
let map, tileLayer;

const LICENCE_PILL = {
  community: { text: 'open', cls: '' },
  custom: { text: 'self-hosted', cls: 'ok' },
  unlicensed: { text: 'unlicensed', cls: 'warn' },
};

/* Tags are distinguished by colour alone — one consistent marker shape. */
const TAG_COLORS = ['#2dd4bf', '#60a5fa', '#f472b6', '#fbbf24',
                    '#a78bfa', '#4ade80', '#fb923c', '#f87171'];

function tiles() { return state.boot.tiles; }

function initMap() {
  // attributionControl is off: the stock bar is replaced by a compact (i)
  // button. The OpenStreetMap credit is a licence condition (ODbL) and still
  // appears there - only Leaflet's own branding prefix is dropped.
  map = L.map('map', { zoomControl: true, attributionControl: false }).setView([30, 0], 2);
  setTiles(tiles().selected);
  $('#map-toggle').onclick = (e) => { e.stopPropagation(); toggleLayerMenu(); };
  document.addEventListener('click', () => $('#layer-menu')?.classList.add('hidden'));
  $('#attrib-btn').onclick = (e) => {
    e.stopPropagation();
    $('#attrib-box').classList.toggle('hidden');
  };
}

function updateAttribution(spec) {
  $('#attrib-box').innerHTML = spec.attr || '';
}

function setTiles(id) {
  const all = tiles().layers;
  let spec = all[id];
  if (!spec) {                        // e.g. the self-hosted layer was removed
    id = all[tiles().selected] ? tiles().selected : 'osm';
    spec = all[id];
  }
  if (!spec) return;
  state.tiles = id;
  tiles().selected = id;
  if (tileLayer) map.removeLayer(tileLayer);
  tileLayer = L.tileLayer(spec.url, {
    maxZoom: spec.maxZoom || 19,
    ...(spec.subdomains ? { subdomains: spec.subdomains } : {}),
  }).addTo(map);
  updateAttribution(spec);
  // Don't strand the user past the new layer's max zoom.
  if (map.getZoom() > (spec.maxZoom || 19)) map.setZoom(spec.maxZoom || 19);
}

function toggleLayerMenu() {
  $('#layer-menu')?.remove();
  const menu = document.createElement('div');
  menu.id = 'layer-menu';
  menu.className = 'layer-menu';

  const rows = Object.entries(tiles().layers).map(([k, v]) => {
    const pill = LICENCE_PILL[v.licence] || { text: '', cls: '' };
    return `<button class="layer-item ${k === state.tiles ? 'sel' : ''}" data-k="${k}"
              title="${esc(v.note || '')}">
              <span>${esc(v.label)}</span>
              ${pill.text ? `<span class="pill ${pill.cls}">${pill.text}</span>` : ''}
            </button>`;
  }).join('');

  menu.innerHTML = rows +
    '<div class="layer-sep"></div>' +
    '<button class="layer-item" data-settings="1"><span>Map settings…</span></button>';
  $('.main').appendChild(menu);

  menu.addEventListener('click', (e) => {
    e.stopPropagation();
    const btn = e.target.closest('.layer-item');
    if (!btn) return;
    if (btn.dataset.settings) { menu.remove(); return openMapSettings(); }
    if (btn.hasAttribute('disabled')) return;
    setTiles(btn.dataset.k);
    menu.remove();
    api('/api/tiles', { method: 'POST', body: { selected: state.tiles } })
      .then(t => { state.boot.tiles = t; })
      .catch(() => {});
  });
}

function openMapSettings() {
  const t = tiles();
  modal(`
    <h2>Map settings</h2>
    <p class="lead">Where your basemap tiles come from.</p>

    <div class="card">
      <h3>Sources</h3>
      <p>NetWatch uses open map sources: <b>OpenStreetMap</b> and
         <b>OpenTopoMap</b>. They are free and need no account.</p>
      <p>Their usage policies are written for modest traffic, so if you ever run this
         at volume, host your own tiles below instead.</p>
    </div>

    <div class="card">
      <h3>Self-hosted tiles <span class="pill ok">optional</span></h3>
      <p>A Leaflet URL template pointing at your own tile server, e.g.
         <code>https://tiles.example.com/{z}/{x}/{y}.png</code>. Leave blank to use the
         open sources above.</p>
      <div class="field" style="margin-bottom:0">
        <input id="tile-url" type="text" value="${esc(t.custom_url || '')}"
               placeholder="https://.../{z}/{x}/{y}.png">
      </div>
    </div>

    <div class="row end">
      <button class="btn" data-close>Cancel</button>
      <button id="tile-save" class="btn primary">Save</button>
    </div>
  `);

  $('#tile-save').onclick = async () => {
    const b = $('#tile-save');
    b.disabled = true; b.innerHTML = '<span class="spin"></span> Saving…';
    try {
      state.boot.tiles = await api('/api/tiles',
        { method: 'POST', body: { custom_url: $('#tile-url').value } });
      setTiles(state.boot.tiles.selected);
      closeModal();
      toast('Map settings saved');
    } catch (e) {
      oops(e); b.disabled = false; b.innerHTML = 'Save';
    }
  };
}

function tagIcon(tag) {
  const c = tag.color || '#2dd4bf';
  return L.divIcon({
    className: 'tag-marker',
    html: `<span class="tm-pin" style="--tc:${esc(c)}"></span>`,
    iconSize: [22, 22], iconAnchor: [11, 11], popupAnchor: [0, -11],
  });
}

function renderMarkers() {
  Object.values(state.markers).forEach(m => map.removeLayer(m));
  state.markers = {};
  const pts = [];
  for (const tag of state.tags) {
    const r = tag.last_seen;
    if (!r || tag.visible === false) continue;
    const m = L.marker([r.latitude, r.longitude], { icon: tagIcon(tag) }).addTo(map);
    m.bindPopup(
      `<b>${esc(tag.name)}</b><br>${new Date(r.timestamp).toLocaleString()}` +
      `<br><span style="color:#9aa1a9">±${r.horizontal_accuracy ?? '?'} m</span>`
    );
    m.on('click', () => selectTag(tag.id));
    state.markers[tag.id] = m;
    pts.push([r.latitude, r.longitude]);
  }
  if (pts.length && !state.selected) map.fitBounds(pts, { padding: [60, 60], maxZoom: 15 });
}

/* ── breadcrumbs ──────────────────────────────────────────────────────── */
function clearTrack() {
  if (state.track) { state.track.forEach(l => map.removeLayer(l)); }
  state.track = null;
}

async function drawTrack(tagId) {
  clearTrack();
  const tag = state.tags.find(t => t.id === tagId);
  if (!tag || !tag.show_track || tag.visible === false) return;

  const w = state.window || {};
  const qs = new URLSearchParams({ id: tagId, limit: 5000 });
  if (w.from) qs.set('from', w.from);
  if (w.to) qs.set('to', w.to);
  const { reports } = await api(`/api/history?${qs}`);
  state.lastTrackCount = reports.length;
  if (!reports.length) return;

  const color = tag.color || '#2dd4bf';
  const ordered = reports.slice().reverse();          // oldest -> newest
  const layers = [];

  if (ordered.length > 1) {
    layers.push(L.polyline(ordered.map(r => [r.latitude, r.longitude]),
      { color, weight: 2.5, opacity: .6, dashArray: '4,6' }).addTo(map));
  }
  // A dot per fix, so you can see where it actually sat vs. interpolation.
  ordered.forEach((r, i) => {
    const last = i === ordered.length - 1;
    layers.push(L.circleMarker([r.latitude, r.longitude], {
      radius: last ? 5 : 3.5, color, fillColor: color,
      fillOpacity: last ? .95 : .55, weight: last ? 2 : 1,
    }).bindPopup(
      `<b>${esc(tag.name)}</b><br>${new Date(r.timestamp).toLocaleString()}` +
      `<br><span style="color:#9aa1a9">±${r.horizontal_accuracy ?? '?'} m</span>`
    ).addTo(map));
  });
  state.track = layers;
}

async function redrawTrack() {
  if (state.selected) {
    try { await drawTrack(state.selected); } catch (e) { /* history optional */ }
  }
}

/* ─────────────────────────── devices ────────────────────────────────── */
function renderDevices() {
  const el = $('#device-list');
  if (!state.tags.length) {
    el.innerHTML = '<p class="empty">No devices yet</p>';
    return;
  }
  el.innerHTML = state.tags.map(t => {
    const r = t.last_seen;
    const sub = r ? `${timeAgo(r.timestamp)} · ±${r.horizontal_accuracy ?? '?'} m`
                  : 'Never seen — waiting for a nearby iPhone';
    const shown = t.visible !== false;
    return `<div class="device ${r ? 'seen' : ''} ${state.selected === t.id ? 'active' : ''} ${shown ? '' : 'hidden-tag'}" data-id="${t.id}">
      <button class="eye" data-eye="${t.id}" title="${shown ? 'Hide on the map' : 'Show on the map'}"
              aria-label="${shown ? 'Hide' : 'Show'} ${esc(t.name)} on the map">${shown ? '👁' : '🚫'}</button>
      <span class="dot" style="--tc:${esc(t.color || '#2dd4bf')}"></span>
      <span class="meta"><span class="nm">${esc(t.name)}</span><span class="sub">${esc(sub)}</span></span>
      <button class="gear" data-gear="${t.id}" title="Details and settings"
              aria-label="${esc(t.name)} details and settings">⋯</button>
    </div>`;
  }).join('');

  // Clicking the row goes to the tag on the map. Details live behind the ⋯
  // button: opening a settings dialog every time someone wanted to look at a
  // location put a modal between them and the map on every single click.
  el.querySelectorAll('.device').forEach(d =>
    d.onclick = () => selectTag(Number(d.dataset.id)));

  el.querySelectorAll('[data-gear]').forEach(b => b.onclick = (e) => {
    e.stopPropagation();
    openDevice(Number(b.dataset.gear));
  });

  el.querySelectorAll('[data-eye]').forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    const id = Number(b.dataset.eye);
    const tag = state.tags.find(t => t.id === id);
    if (!tag) return;
    const next = tag.visible === false;
    tag.visible = next;                       // optimistic: the click feels instant
    renderDevices();
    renderMarkers();
    if (!next && state.selected === id) clearTrack();
    else await redrawTrack();
    try {
      // Direct call, not saveAppearance: that helper reports its own errors and
      // never rethrows, so the revert below would never run.
      await api('/api/tags/update', { method: 'POST', body: { id, visible: next } });
    } catch (err) {
      tag.visible = !next;                    // put it back if the save failed
      renderDevices();
      renderMarkers();
      await redrawTrack();
      oops(err);
    }
  });
}

function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return 'just now';
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/* Go to a tag on the map. Deliberately does NOT open the details dialog —
   use the ⋯ button for that. */
async function selectTag(id) {
  state.selected = id;
  renderDevices();
  const tag = state.tags.find(t => t.id === id);
  if (tag?.last_seen) {
    map.setView([tag.last_seen.latitude, tag.last_seen.longitude], 15);
    state.markers[id]?.openPopup();
  } else if (tag) {
    toast(tag.name, 'No location yet — waiting for a nearby iPhone');
  }
  try { await drawTrack(id); } catch { /* history is optional */ }
}

/* ─────────────────────────── load / sync ────────────────────────────── */
async function loadTags() {
  const { tags } = await api('/api/tags');
  state.tags = tags;
  renderDevices();
  renderMarkers();
}

async function syncAll(tagId = null) {
  const btn = $('#sync-all');
  const old = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Syncing…';
  try {
    const r = await api('/api/sync', { method: 'POST', body: tagId ? { tag_id: tagId } : {} });
    await loadTags();
    toast('Sync complete',
      r.new_reports ? `${r.new_reports} new location${r.new_reports === 1 ? '' : 's'}`
                    : 'No new locations yet — tags report when an iPhone passes by');
  } catch (e) {
    // Apple expiring the session is the common failure, and it needs a code,
    // not a password. Go straight to the two-factor step so the fix is one
    // click away instead of a dead end.
    if (/two-factor/i.test(e.message)) {
      toast('Apple needs a code', e.message, 'err');
      await resume2fa();
    } else if (/sign in/i.test(e.message)) {
      oops(e); openLogin();
    } else oops(e);
  } finally { btn.disabled = false; btn.innerHTML = old; }
}

/* ──────────────────────────── login ─────────────────────────────────── */
function openLogin() {
  const st = state.boot?.apple || {};
  if (st.state === 'logged_in') return openAccount();
  modal(`
    <h2>Sign in to Apple</h2>
    <p class="lead">NetWatch asks Apple for your tags' encrypted location reports. Use a
      spare Apple ID if you have one.</p>
    <div class="card">
      <p><strong>Your sign-in stays on this computer</strong>, encrypted with your Windows
      account, and is sent only to Apple. It includes your password, because Apple expires
      the session every few days and NetWatch has to renew it for you — without that you
      would be retyping your password and a code twice a week.</p>
      <p>Use a spare Apple ID if you would rather not store your main one.</p>
    </div>
    <div class="field"><label for="ap-email">Apple ID</label>
      <input id="ap-email" type="email" autocomplete="username" placeholder="you@example.com"></div>
    <div class="field"><label for="ap-pass">Password</label>
      <input id="ap-pass" type="password" autocomplete="current-password"></div>
    <div class="row end"><button class="btn" data-close>Cancel</button>
      <button id="ap-go" class="btn primary">Sign in</button></div>
  `);
  $('#ap-go').onclick = doLogin;
  $('#ap-pass').onkeydown = (e) => { if (e.key === 'Enter') doLogin(); };
  $('#ap-email').focus();
}

async function doLogin() {
  const email = $('#ap-email').value.trim();
  const passEl = $('#ap-pass');
  const btn = $('#ap-go');
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Signing in…';
  try {
    const st = await api('/api/login', { method: 'POST', body: { email, password: passEl.value } });
    passEl.value = '';
    state.boot.apple = st;
    if (st.state === 'needs_2fa') return open2fa(st);
    await afterLogin();
  } catch (e) {
    oops(e); btn.disabled = false; btn.innerHTML = 'Sign in';
  }
}

/* Apple expired the session and wants a new code. The account is still here and
   the tag keys are untouched, so pick the two-factor flow back up rather than
   making someone sign in from scratch. */
async function resume2fa() {
  try {
    const b = await api('/api/bootstrap');
    state.boot.apple = b.apple;
    refreshLoginLabel();
    if (b.apple.state === 'needs_2fa' && b.apple.methods?.length) {
      return open2fa(b.apple);
    }
    openLogin();
  } catch (e) { oops(e); }
}

function open2fa(st) {
  modal(`
    <h2>Two-factor authentication</h2>
    <p class="lead">Apple needs to confirm it's you.</p>
    <div id="methods">${st.methods.map((m, i) =>
      `<div class="method ${i === 0 ? 'sel' : ''}" data-i="${i}">
         <span class="status-dot ok"></span><span>${esc(m.label)}</span></div>`).join('')}</div>
    <div class="row end" style="margin-top:14px">
      <button class="btn" data-close>Cancel</button>
      <button id="tf-send" class="btn primary">Send code</button></div>
  `);
  let idx = 0;
  $('#methods').querySelectorAll('.method').forEach(el => el.onclick = () => {
    idx = Number(el.dataset.i);
    $('#methods').querySelectorAll('.method').forEach(x => x.classList.toggle('sel', x === el));
  });
  $('#tf-send').onclick = async () => {
    const b = $('#tf-send');
    b.disabled = true; b.innerHTML = '<span class="spin"></span> Sending…';
    try { await api('/api/2fa/request', { method: 'POST', body: { index: idx } }); openCode(idx); }
    catch (e) { oops(e); b.disabled = false; b.innerHTML = 'Send code'; }
  };
}

function openCode(idx) {
  modal(`
    <h2>Enter the code</h2>
    <p class="lead">Type the six-digit code Apple just sent.</p>
    <div class="field"><input id="tf-code" type="text" inputmode="numeric" maxlength="8"
      placeholder="123456" style="font-size:24px;letter-spacing:.3em;text-align:center"></div>
    <div class="row end"><button class="btn" data-close>Cancel</button>
      <button id="tf-go" class="btn primary">Verify</button></div>
  `);
  const go = async () => {
    const b = $('#tf-go');
    b.disabled = true; b.innerHTML = '<span class="spin"></span> Verifying…';
    try {
      state.boot.apple = await api('/api/2fa/submit',
        { method: 'POST', body: { index: idx, code: $('#tf-code').value } });
      await afterLogin();
    } catch (e) { oops(e); b.disabled = false; b.innerHTML = 'Verify'; }
  };
  $('#tf-go').onclick = go;
  const inp = $('#tf-code');
  inp.focus();
  inp.onkeydown = (e) => { if (e.key === 'Enter') go(); };
}

async function afterLogin() {
  closeModal();
  refreshLoginLabel();
  toast('Signed in', 'You can sync your tags now');
  await loadTags();
  if (state.tags.length) syncAll();
}

function openAccount() {
  const st = state.boot.apple;
  modal(`
    <h2>Apple account</h2>
    <div class="card"><div class="spread">
      <div><div style="font-weight:600">${esc(st.email || 'Signed in')}</div>
      <div style="color:var(--fg-dim);font-size:13px">Session stored encrypted on this computer</div></div>
      <span class="status-dot ok"></span></div></div>
    <div class="row end"><button class="btn" data-close>Close</button>
      <button id="ap-out" class="btn danger">Sign out</button></div>
  `);
  $('#ap-out').onclick = async () => {
    try {
      await api('/api/logout', { method: 'POST' });
      state.boot.apple = { state: 'logged_out', email: '', methods: [] };
      refreshLoginLabel(); closeModal(); toast('Signed out');
    } catch (e) { oops(e); }
  };
}

function refreshLoginLabel() {
  const st = state.boot?.apple || {};
  // needs_2fa is its own thing: the account is still there, Apple just wants a
  // code. Saying "Login" would send someone off to retype a password they do
  // not need to retype.
  $('#login-label').textContent =
    st.state === 'logged_in' ? (st.email || 'Account')
    : st.state === 'needs_2fa' ? 'Enter Apple code'
    : 'Login';
  $('#login-btn').classList.toggle('needs-attention', st.state === 'needs_2fa');
}

/* ────────────────────────── add device ──────────────────────────────── */
function openAdd() {
  modal(`
    <h2>Add a tag</h2>
    <p class="lead">Create new keys for a board you're about to flash, or import a tag you
      already own.</p>
    <div class="field"><label for="nd-name">Name</label>
      <input id="nd-name" type="text" placeholder="Backpack" maxlength="48"></div>
    <div class="card">
      <h3>Import existing keys <span class="pill">optional</span></h3>
      <p>Paste a tag's JSON key file to restore it on this computer. Leave empty to generate
         a brand-new key pair.</p>
      <div class="field"><textarea id="nd-keys" placeholder='[{"name":"Backpack","privateKey":"…"}]'></textarea></div>
    </div>
    <div class="row end"><button class="btn" data-close>Cancel</button>
      <button id="nd-go" class="btn primary">Add tag</button></div>
  `);
  $('#nd-name').focus();
  $('#nd-go').onclick = async () => {
    const name = $('#nd-name').value.trim();
    const keyfile = $('#nd-keys').value.trim();
    if (!name && !keyfile) return toast('Name required', 'Give the tag a name', 'warn');
    const b = $('#nd-go'); b.disabled = true; b.innerHTML = '<span class="spin"></span> Adding…';
    try {
      await api('/api/tags', { method: 'POST', body: { name: name || 'Imported tag', keyfile } });
      closeModal(); await loadTags(); toast('Tag added', keyfile ? 'Imported' : 'New keys generated');
    } catch (e) { oops(e); b.disabled = false; b.innerHTML = 'Add tag'; }
  };
}

/* ────────────────────────── device detail ───────────────────────────── */
async function openDevice(id) {
  const t = state.tags.find(x => x.id === id);
  if (!t) return;
  const fmts = state.boot.export_formats || [];
  modal(`
    <h2>${esc(t.name)}</h2>
    <p class="lead">${t.report_count} location${t.report_count === 1 ? '' : 's'} recorded</p>

    <div class="card">
      <h3>Status</h3>
      <dl class="kv">
        <dt>Bluetooth MAC</dt><dd>${esc(t.printedMac)}</dd>
        <dt>Last seen</dt><dd>${t.last_seen ? esc(new Date(t.last_seen.timestamp).toLocaleString()) : 'never'}</dd>
        <dt>Added</dt><dd>${esc(new Date(t.created_at).toLocaleDateString())}</dd>
      </dl>
      <div class="row" style="margin-top:13px">
        <button id="dv-sync" class="btn sm">Sync now</button>
        <button id="dv-flash" class="btn sm">Flash to a board</button>
      </div>
    </div>

    <div class="card">
      <h3>Breadcrumbs</h3>
      <label class="checkline" style="margin:0 0 12px">
        <input type="checkbox" id="dv-track" ${t.show_track ? 'checked' : ''}>
        <span>Show this tag's trail on the map</span></label>
      <div class="grid2">
        <div class="field" style="margin:0"><label for="dv-from">From</label>
          <input id="dv-from" type="date" value="${esc(state.window.from || '')}"></div>
        <div class="field" style="margin:0"><label for="dv-to">To</label>
          <input id="dv-to" type="date" value="${esc(state.window.to || '')}"></div>
      </div>
      <div class="hint" id="dv-range">${t.range && t.range.count
        ? `${t.range.count} point${t.range.count === 1 ? '' : 's'} recorded, `
          + `${new Date(t.range.first).toLocaleDateString()} – ${new Date(t.range.last).toLocaleDateString()}`
        : 'no locations recorded yet'}</div>
      <div class="row" style="margin-top:12px">
        <button id="dv-apply" class="btn sm primary">Apply</button>
        <button id="dv-all" class="btn sm">All dates</button>
        <button id="dv-7d" class="btn sm">Last 7 days</button>
        <button id="dv-24h" class="btn sm">Last 24 h</button>
      </div>
    </div>

    <div class="card">
      <h3>Colour</h3>
      <p>Used for this tag's map marker, its breadcrumbs and its dot in the list.</p>
      <div class="swatches" id="dv-colors">${TAG_COLORS.map(c =>
        `<button class="swatch ${c.toLowerCase() === (t.color || '').toLowerCase() ? 'sel' : ''}"
           data-color="${c}" style="--sc:${c}" title="${c}"></button>`).join('')}</div>
    </div>

    <div class="card">
      <h3>Settings</h3>
      <div class="field"><label for="dv-name">Name</label>
        <input id="dv-name" type="text" value="${esc(t.name)}" maxlength="48"></div>
      <label class="checkline" style="margin:0">
        <input type="checkbox" id="dv-auto" ${t.auto_sync ? 'checked' : ''}>
        <span>Keep this tag updated automatically in the background</span></label>
      <div class="row end" style="margin-top:12px">
        <button id="dv-save" class="btn sm primary">Save</button></div>
    </div>

    <div class="card">
      <h3>Export location history</h3>
      <p>Exports respect the breadcrumb date range above. JSON includes the private key so
         the tag can be re-imported elsewhere; other formats contain location data only.</p>
      <div class="grid2">${fmts.map(f =>
        `<button class="btn sm export" data-fmt="${f.id}" title="${esc(f.description)}">${esc(f.label)}</button>`).join('')}</div>
    </div>

    <div class="row end">
      <button id="dv-del" class="btn danger">Delete tag</button>
      <button class="btn" data-close>Close</button>
    </div>
  `);

  $('#dv-sync').onclick = () => { closeModal(); syncAll(id); };
  $('#dv-flash').onclick = () => openFlash(id);

  // --- colour: apply immediately so you can see it on the map -------------
  $('#dv-colors').onclick = async (e) => {
    const b = e.target.closest('.swatch');
    if (!b) return;
    $('#dv-colors').querySelectorAll('.swatch').forEach(x => x.classList.toggle('sel', x === b));
    await saveAppearance(id, { color: b.dataset.color });
  };

  // --- breadcrumbs --------------------------------------------------------
  $('#dv-track').onchange = async (e) => {
    await saveAppearance(id, { show_track: e.target.checked });
  };
  const applyWindow = async (from, to) => {
    state.window = { from: from || '', to: to || '' };
    $('#dv-from').value = state.window.from;
    $('#dv-to').value = state.window.to;
    await redrawTrack();
    const n = state.lastTrackCount ?? 0;
    toast('Breadcrumbs', n ? `showing ${n} point${n === 1 ? '' : 's'}` : 'no points in that range');
  };
  $('#dv-apply').onclick = () => applyWindow($('#dv-from').value, $('#dv-to').value);
  $('#dv-all').onclick = () => applyWindow('', '');
  const ago = (days) => new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
  $('#dv-7d').onclick = () => applyWindow(ago(7), '');
  $('#dv-24h').onclick = () => applyWindow(ago(1), '');

  $('#dv-save').onclick = async () => {
    try {
      await api('/api/tags/update', { method: 'POST', body: {
        id, name: $('#dv-name').value.trim() || t.name, auto_sync: $('#dv-auto').checked } });
      await loadTags(); closeModal(); toast('Saved');
    } catch (e) { oops(e); }
  };
  $('#dv-del').onclick = async () => {
    if (!confirm(`Delete "${t.name}"?\n\nIts private key is erased. If the board is still out there you will never be able to locate it again.`)) return;
    try {
      await api('/api/tags/delete', { method: 'POST', body: { id } });
      state.selected = null; closeModal(); await loadTags(); toast('Tag deleted');
    } catch (e) { oops(e); }
  };
  $('#modal-body').querySelectorAll('.export').forEach(b => b.onclick = async () => {
    try {
      const qs = new URLSearchParams({ id, fmt: b.dataset.fmt });
      if ($('#dv-from').value) qs.set('from', $('#dv-from').value);
      if ($('#dv-to').value) qs.set('to', $('#dv-to').value);
      const r = await api(`/api/export?${qs}`);
      const blob = new Blob([r.content], { type: 'application/octet-stream' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = r.filename; a.click();
      URL.revokeObjectURL(a.href);
      toast('Exported', `${r.filename}${r.points != null ? ` · ${r.points} point(s)` : ''}`);
    } catch (e) { oops(e); }
  });
}

/* ──────────────────────────── flasher ───────────────────────────────── */
function openFlash(tagId = null) {
  const tag = tagId ? state.tags.find(t => t.id === tagId) : null;
  modal(`
    <h2>Flash a board</h2>
    <p class="lead">${tag ? `Writing <b>${esc(tag.name)}</b>'s key onto a board.`
      : 'Plug an ESP32 in over USB. NetWatch detects the chip and flashes it — no toolchain, no compiling.'}</p>

    <div class="card">
      <h3>1 · Connect</h3>
      <div class="field"><label for="fl-port">Board</label>
        <select id="fl-port"><option>Scanning…</option></select>
        <div class="hint">
          <b>ESP32:</b> use a <b>data</b> USB cable; on C3/C6/S3 hold BOOT, tap RST, release BOOT.<br>
          <b>XIAO nRF52840:</b> double-tap RESET until its drive appears, then Rescan.
        </div></div>
      <div class="row"><button id="fl-scan" class="btn sm">Rescan</button>
        <button id="fl-detect" class="btn sm primary">Connect</button></div>
      <div id="fl-chip" style="margin-top:12px"></div>
    </div>

    <div class="card" id="fl-step2" style="opacity:.45;pointer-events:none">
      <h3>2 · Flash</h3>
      ${tag ? '' : `<div class="field"><label for="fl-name">New tag name</label>
        <input id="fl-name" type="text" placeholder="Backpack" maxlength="48"></div>`}
      <button id="fl-go" class="btn primary">Generate key &amp; flash</button>
      <pre class="log hidden" id="fl-log"></pre>
    </div>
    <div class="row end"><button class="btn" data-close>Close</button></div>
  `);

  let chip = null;
  const sel = $('#fl-port');

  async function scan() {
    sel.innerHTML = '<option>Scanning…</option>';
    try {
      const { ports } = await api('/api/ports');
      sel.innerHTML = ports.length
        ? ports.map(p => `<option value="${esc(p.port)}">${esc(p.port)} — ${esc(p.hint || p.description || 'serial device')}</option>`).join('')
        : '<option value="">No serial ports found</option>';
    } catch (e) { oops(e); }
  }

  $('#fl-scan').onclick = scan;
  $('#fl-detect').onclick = async () => {
    const b = $('#fl-detect');
    b.disabled = true; b.innerHTML = '<span class="spin"></span> Connecting…';
    try {
      const info = await api('/api/detect', { method: 'POST', body: { port: sel.value } });
      chip = info.chip;
      $('#fl-chip').innerHTML = `<div class="spread">
        <div><b>${esc(info.label)}</b><div style="color:var(--fg-dim);font-size:12px">MAC ${esc(info.mac)}</div></div>
        <span class="status-dot ${info.firmware_available ? 'ok' : 'bad'}"></span></div>
        ${info.firmware_available ? '' :
          `<div class="hint" style="color:var(--warn)">No bundled firmware for ${esc(info.chip)} yet.</div>`}`;
      if (info.firmware_available) {
        const s2 = $('#fl-step2'); s2.style.opacity = '1'; s2.style.pointerEvents = 'auto';
      }
    } catch (e) { oops(e); $('#fl-chip').innerHTML = ''; }
    finally { b.disabled = false; b.innerHTML = 'Connect'; }
  };

  $('#fl-go').onclick = async () => {
    const b = $('#fl-go'); const logEl = $('#fl-log');
    const name = tag ? tag.name : ($('#fl-name')?.value || '').trim();
    if (!tag && !name) return toast('Name required', 'Name the new tag first', 'warn');
    b.disabled = true; b.innerHTML = '<span class="spin"></span> Flashing…';
    logEl.classList.remove('hidden'); logEl.textContent = '';
    try {
      const { job } = await api('/api/flash', { method: 'POST',
        body: { port: sel.value, chip, tag_id: tagId, name } });
      const result = await pollJob(job, logEl);
      await loadTags();
      if (result && result.needs_reset) {
        // UF2 bootloaders program one image per session and wait for a reset.
        toast('Flashed', 'Tap RESET once on the board to start it', 'warn');
      } else {
        toast('Flashed', 'Tap RST — the tag is advertising now');
      }
      b.innerHTML = 'Flash another';
      b.disabled = false;
    } catch (e) {
      oops(e); b.disabled = false; b.innerHTML = 'Generate key & flash';
    }
  };

  scan();
}

async function pollJob(jobId, logEl) {
  let shown = 0;
  for (;;) {
    const j = await api(`/api/job?id=${jobId}`);
    for (; shown < j.lines.length; shown++) {
      logEl.textContent += j.lines[shown] + '\n';
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (j.state === 'done') return j.result;
    if (j.state === 'error') throw new Error(j.error || 'Flashing failed');
    await new Promise(r => setTimeout(r, 400));
  }
}

/* ─────────────────────────── ATAK bridge ───────────────────────────── */
/* A read-only door on the LAN so the NetWatch-TAK plugin can show these tags on
   an ATAK map. Off until switched on: a tracker app should not open a port on
   someone's network just in case. */
async function openBridge() {
  let b;
  try {
    b = await api('/api/bridge');
  } catch (e) { return oops(e); }

  const render = (s) => {
    const on = s.running;
    $('#modal-body').innerHTML = `
      <h2>ATAK bridge</h2>
      <p class="lead">Show these tags on an ATAK or WinTAK map with the
         <b>NetWatch-TAK</b> plugin.</p>

      <div class="card">
        <h3><span class="status-dot ${on ? 'ok' : 'bad'}"></span>
            ${on ? 'Running' : 'Switched off'}</h3>
        <p>${on
            ? 'This computer is answering ATAK on the addresses below.'
            : 'Nothing is listening. Switch it on when you want a tablet to connect.'}</p>
        <div class="row">
          <button id="br-toggle" class="btn ${on ? '' : 'primary'}">
            ${on ? 'Switch off' : 'Switch on'}</button>
          <span style="margin-left:12px;color:#9aa1a9">Port</span>
          <input id="br-port" type="number" min="1024" max="65535"
                 value="${s.port}" style="width:92px;margin-left:6px">
        </div>
      </div>

      ${on ? `
      <div class="card">
        <h3>1 &middot; The pair code</h3>
        <pre class="log" style="font-size:22px;letter-spacing:3px;text-align:center;
             padding:14px;margin:6px 0">${esc(s.code)}</pre>
        <div class="row"><button id="br-rotate" class="btn sm">New code</button></div>
      </div>

      <div class="card">
        <h3>2 &middot; Pair the tablet</h3>
        <p><b>Same wifi as this computer?</b> In ATAK open <b>NetWatch-TAK</b>, tap
           <b>Find on my network</b>, and enter the code above.</p>
        <p><b>On a VPN</b> &mdash; Tailscale, WireGuard, IPsec &mdash; "Find on my
           network" finds nothing, because a VPN carries no broadcast traffic.
           That is normal. Tap <b>Paste pairing link</b> instead and use the link
           for the address the tablet can reach:</p>
        ${s.endpoints.length
          ? s.endpoints.map((e, i) => `
            <div style="margin:10px 0 0">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                <span class="pill ${e.label.startsWith('VPN') ? 'ok' : ''}">${esc(e.label)}</span>
                <code>${esc(e.url)}</code>
              </div>
              <pre class="log" style="margin:0">${esc(e.link)}</pre>
              <button class="btn sm br-copy" data-i="${i}"
                      style="margin-top:4px">Copy this link</button>
            </div>`).join('')
          : '<p>No usable network address found. Is this computer on a network?</p>'}
        <p style="margin-top:12px">Pick the one matching how the tablet connects: a
           tablet on the tailnet needs the VPN address, a tablet on your wifi needs
           the local one. If the VPN address is missing, bring the VPN up and
           reopen this panel.</p>
      </div>

      <div class="card">
        <h3>3 &middot; If it will not connect</h3>
        <p>Allow <b>NetWatch</b> through Windows Firewall on a <b>private</b>
           network, or let TCP port ${s.port} through. A VPN adapter often counts
           as a <b>public</b> network to Windows, so allow it there too if the
           tablet is on a VPN.</p>
        <label class="checkline">
          <input type="checkbox" id="br-disc" ${s.discovery ? 'checked' : ''}>
          <span>Answer "Find on my network" requests (UDP ${s.discovery_port})</span>
        </label>
      </div>` : ''}

      <div class="card">
        <h3>What goes over the wire</h3>
        <p>Tag names, colours, positions and history &mdash; and nothing else. Tag
           keys <b>never</b> leave this computer, so a paired tablet cannot be used
           to look your tags up at Apple, and losing it costs you nothing but a
           code change.</p>
        <p>The connection is plain HTTP on your own network, guarded by the pair
           code. That suits a home or office network, or a VPN. <b>Do not forward
           this port through your router</b> &mdash; anyone who then read the
           traffic would see the code and your tag locations.</p>
      </div>

      <div class="row end"><button class="btn" data-close>Close</button></div>`;

    // data-close is handled by the delegated listener on #modal-root.

    $('#br-toggle').onclick = async () => {
      const btn = $('#br-toggle');
      btn.disabled = true;
      btn.innerHTML = '<span class="spin"></span> Working…';
      try {
        const port = parseInt($('#br-port').value, 10) || s.port;
        render(await api('/api/bridge',
          { method: 'POST', body: { enabled: !on, port } }));
        toast(on ? 'ATAK bridge switched off' : 'ATAK bridge is running');
      } catch (e) { oops(e); render(s); }
    };

    if (on) {
      $('#br-rotate').onclick = async () => {
        if (!confirm('Make a new pair code?\n\nEvery tablet already paired with '
                   + 'this computer will stop working until you enter the new '
                   + 'code there.')) return;
        try {
          render(await api('/api/bridge', { method: 'POST', body: { rotate: true } }));
          toast('New pair code', 'Re-pair any tablet you still use');
        } catch (e) { oops(e); }
      };
      $('#modal-body').querySelectorAll('.br-copy').forEach(btn => {
        btn.onclick = async () => {
          const ep = s.endpoints[parseInt(btn.dataset.i, 10)];
          try {
            await navigator.clipboard.writeText(ep.link);
            toast('Copied', `${ep.label} — ${ep.ip}`);
          } catch {
            // Clipboard access can be refused; the link is on screen to read.
            toast('Could not copy', 'Select the link above and copy it', 'err');
          }
        };
      });
      $('#br-disc').onchange = async (e) => {
        try {
          await api('/api/bridge',
            { method: 'POST', body: { discovery: e.target.checked } });
        } catch (err) { oops(err); }
      };
    }
  };

  modal('');
  render(b);
}

/* ───────────────────────────── about ────────────────────────────────── */
async function openAbout() {
  modal(`
    <h2>NetWatch ${esc(state.boot.version)}</h2>
    <div class="card">
      <h3>About</h3>
      <p>NetWatch locates tags you build yourself through Apple's Find My network, without
         needing a Mac, iPhone or iPad.</p>
      <p>Everything is stored <b>locally on this computer</b> — tags, keys, history and your
         Apple session. Nothing is sent to us, and we cannot recover it for you. Use the export
         options to keep your own backups.</p>
      <p>A NetWatch tag is not an AirTag: it will not appear in Find My&nbsp;&rsaquo;&nbsp;Items
         and it does <b>not</b> trigger Apple's unwanted-tracking alerts, so someone carrying one
         is unlikely to be warned. Use these on your own property, and check the law where you
         live.</p>
    </div>
    <!-- The AGPL asks an interactive program to display an appropriate legal
         notice: copyright, absence of warranty, and where to find the licence. -->
    <div class="card">
      <h3>Licence</h3>
      <p>Copyright &copy; 2026 junii55.</p>
      <p>NetWatch is free software, released under the
         <b>GNU Affero General Public License v3</b>, and comes with
         <b>absolutely no warranty</b>. You may redistribute it under those terms;
         see the <code>LICENSE</code> file alongside the application.</p>
      <p>Selling NetWatch, or shipping it inside a product, requires a separate
         commercial licence.</p>
      <p><a href="https://github.com/junii55/netwatch" target="_blank"
            rel="noreferrer noopener">github.com/junii55/netwatch</a></p>
    </div>
    <div class="card" id="ab-health"><h3>Apple Helper <span class="spin"></span></h3>
      <p>Checking…</p></div>
    <div class="card">
      <h3>Logs</h3>
      <p>Backend activity, useful when something isn't working.</p>
      <button id="ab-logs" class="btn sm">View logs</button>
      <pre class="log hidden" id="ab-logbox"></pre>
    </div>
    <div class="row end"><button class="btn" data-close>Close</button></div>
  `);

  $('#ab-logs').onclick = async () => {
    const box = $('#ab-logbox');
    try {
      const { log } = await api('/api/logs');
      box.textContent = log; box.classList.remove('hidden');
    } catch (e) { oops(e); }
  };

  try {
    const h = await api('/api/health');
    $('#ab-health').innerHTML = `
      <h3><span class="status-dot ${h.ok ? 'ok' : 'bad'}"></span>Apple Helper</h3>
      <p>${esc(h.detail)}</p>
      <p><span class="pill">anisette: ${esc(h.anisette)}</span></p>
      <details><summary>Technical details (for support)</summary>
        <pre class="log" style="margin-top:9px">${esc(h.technical)}</pre></details>`;
  } catch (e) {
    $('#ab-health').innerHTML = `<h3><span class="status-dot bad"></span>Apple Helper</h3>
      <p>${esc(e.message)}</p>`;
  }
}

/* ──────────────────────────── bootstrap ─────────────────────────────── */
async function boot() {
  state.boot = await api('/api/bootstrap');
  state.tiles = state.boot.tiles.selected;
  $('#ver').textContent = state.boot.version;
  if (!state.boot.retail) {
    // Make it obvious which build this is: the personal one must not be sold.
    document.querySelector('.wordmark').innerHTML = 'Net<b>Watch</b> <span class="edition">Personal</span>';
    document.title = 'NetWatch Personal';
  }

  // The first-run ownership gate is off unless the build asks for it
  // (NETWATCH_ATTESTATION=1). The markup stays so it can be switched back on.
  if (state.boot.require_attestation && !state.boot.attested) {
    $('#attest').classList.remove('hidden');
    $('#attest-box').onchange = (e) => { $('#attest-go').disabled = !e.target.checked; };
    $('#attest-go').onclick = async () => {
      await api('/api/attest', { method: 'POST', body: { accepted: true } });
      $('#attest').classList.add('hidden');
      await start();
    };
    return;
  }
  await start();
}

async function start() {
  $('#shell').classList.remove('hidden');
  initMap();
  refreshLoginLabel();
  if (state.boot.demo) {
    $('#banner').textContent = 'Demo mode — showing synthetic locations';
    $('#banner').classList.remove('hidden');
  }
  $('#add-device').onclick = openAdd;
  $('#sync-all').onclick = () => syncAll();
  $('#flash-open').onclick = () => openFlash();
  $('#login-btn').onclick = () =>
    (state.boot?.apple?.state === 'needs_2fa' ? resume2fa() : openLogin());
  $('#bridge-btn').onclick = openBridge;
  $('#about-btn').onclick = openAbout;
  await loadTags();

  // The session restore runs in the background at launch; pick it up when ready.
  setTimeout(async () => {
    try {
      const b = await api('/api/bootstrap');
      state.boot.apple = b.apple;
      refreshLoginLabel();
    } catch { /* ignore */ }
  }, 1500);
}

boot().catch(e => {
  document.body.innerHTML =
    `<div style="padding:40px;font-family:system-ui;color:#e8eaed">
       <h1>NetWatch could not start</h1><p style="color:#f87171">${esc(e.message)}</p></div>`;
});
