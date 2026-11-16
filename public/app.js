'use strict';
// The wall: every stream with the frame that is on air now; hover plays the live preview rendition.

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// the console's translation preset (presets.json): model, then the source it starts from
const presetChip = (p) => p ? `<span class="chip model" title="translation preset for the console: model ${esc(p.translationModel)}, source ${esc(p.sourceLanguage)}${p.detectableLanguages?.length ? ' among ' + esc(p.detectableLanguages.join(', ')) : ''}">${p.translationModel === 'delta2' ? 'DELTA 2' : 'AUTO'} · ${esc(p.sourceLanguage)}</span>` : '';
const pad5 = (n) => String(n).padStart(5, '0');
function clock(t) {
  t = Math.max(0, Math.floor(t));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function copyOnClick(el, text) {
  el.onclick = async (e) => {
    e.preventDefault();
    try { await navigator.clipboard.writeText(text()); el.classList.add('done'); setTimeout(() => el.classList.remove('done'), 900); } catch (_) {}
  };
}

const grid = $('#grid'), statusEl = $('#status'), pinEl = $('#pinhole');
const cards = new Map(); // id → { el, s, at, hls, timer }
copyOnClick(pinEl, () => pinEl.textContent);

function makeCard(id) {
  const el = document.createElement('a');
  el.className = 'card';
  el.href = '/watch.html?id=' + encodeURIComponent(id);
  el.innerHTML = `
    <div class="thumb">
      <div class="none">no picture yet</div>
      <img alt="" hidden />
      <video muted playsinline preload="none"></video>
      <span class="badge"></span>
      <span class="res"></span>
      <div class="bar"><i></i></div>
    </div>
    <div class="meta"><b></b><span class="time"></span></div>
    <div class="chips"></div>`;
  const c = { el, s: null, at: 0, hls: null, timer: 0, thumb: -1 };
  el.addEventListener('mouseenter', () => { c.timer = setTimeout(() => startPreview(c), 200); });
  el.addEventListener('mouseleave', () => { clearTimeout(c.timer); stopPreview(c); });
  return c;
}

function jobText(jobs) {
  const names = Object.keys(jobs);
  const failed = names.find((n) => jobs[n].state === 'error');
  if (failed) return ['err', `${failed} failed`];
  const run = names.find((n) => jobs[n].state === 'running');
  if (run) return ['build', `building ${run} ${Math.floor(jobs[run].pct)}%`];
  return names.length ? ['build', `queued ${names.join(', ')}`] : null;
}

function update(c, s, now) {
  c.s = s;
  c.at = now; // server time of s.position
  const el = c.el;
  $('.meta b', el).textContent = s.name;
  $('.meta b', el).title = s.file;
  $('.res', el).textContent = s.renditions.length ? `${s.renditions[0].width}×${s.renditions[0].height}` : `${s.width}×${s.height} src`;
  const job = jobText(s.jobs);
  const badge = $('.badge', el);
  if (s.onAir) { badge.className = 'badge live'; badge.textContent = 'LIVE'; badge.title = job ? job[1] : ''; }
  else { badge.className = 'badge ' + (job ? job[0] : 'build'); badge.textContent = job ? job[1] : 'waiting'; }
  if (s.onAir && job) $('.res', el).textContent += ` · ${job[1]}`;
  const n = s.thumbs ? (s.onAir ? s.thumb : s.poster) : 0;
  if (n !== c.thumb) {
    c.thumb = n;
    const img = $('img', el);
    img.hidden = !n;
    $('.none', el).hidden = !!n;
    if (n) img.src = `/thumbs/${encodeURIComponent(s.id)}/${pad5(n)}.jpg`;
  }
  $('.chips', el).innerHTML = presetChip(s.preset) + s.languages.map((l) => `<span class="chip">${esc(l)}</span>`).join('') + esc(s.notes.join(' · '));
  $('.chips', el).title = s.notes.join('\n');
  tick(c);
}

function tick(c) {
  const s = c.s;
  if (!s) return;
  const pos = s.onAir ? (s.position + (serverNow() - c.at)) % s.duration : 0;
  $('.bar i', c.el).style.width = (s.onAir ? (pos / s.duration) * 100 : 0) + '%';
  $('.time', c.el).textContent = s.onAir ? `${clock(pos)} / ${clock(s.duration)}` : clock(s.duration);
}

// ── hover: the live preview rendition, muted ─────────────────────
function startPreview(c) {
  const url = c.s && c.s.beacon && c.s.beacon.previewUrl;
  const v = $('video', c.el);
  if (!url || c.hls) return;
  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls({ maxBufferLength: 6, liveSyncDurationCount: 2, lowLatencyMode: true });
    c.hls = hls;
    hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal) stopPreview(c); });
    hls.loadSource(url);
    hls.attachMedia(v);
  } else if (v.canPlayType('application/vnd.apple.mpegurl')) {
    c.hls = {};
    v.src = url;
  } else return;
  v.onplaying = () => c.el.classList.add('playing');
  v.play().catch(() => {});
}

function stopPreview(c) {
  if (!c.hls) return;
  if (c.hls.destroy) c.hls.destroy();
  c.hls = null;
  const v = $('video', c.el);
  v.onplaying = null;
  v.removeAttribute('src');
  v.load();
  c.el.classList.remove('playing');
}

// ── poll ─────────────────────────────────────────────────────────
let skew = 0; // server clock − this clock, seconds
const serverNow = () => Date.now() / 1000 + skew;

async function poll() {
  let d;
  try {
    d = await (await fetch('/api/streams')).json();
  } catch (_) {
    statusEl.textContent = 'server unreachable';
    statusEl.className = 'err';
    return;
  }
  skew = d.now - Date.now() / 1000;
  statusEl.className = '';
  statusEl.textContent = `${d.onAir} of ${d.count} on air` + (d.building ? ` · ${d.building} build job${d.building > 1 ? 's' : ''} left` : '') + ` · ${d.segmentSeconds}s segments`;
  pinEl.textContent = d.pinholeUrl;
  const ids = new Set(d.streams.map((s) => s.id));
  for (const [id, c] of cards) if (!ids.has(id)) { stopPreview(c); c.el.remove(); cards.delete(id); }
  d.streams.forEach((s, i) => {
    let c = cards.get(s.id);
    if (!c) { c = makeCard(s.id); cards.set(s.id, c); }
    if (grid.children[i] !== c.el) grid.insertBefore(c.el, grid.children[i] || null);
    update(c, s, d.now);
  });
  let empty = $('.empty', grid);
  if (!d.count && !empty) { empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'no videos in MEDIA_DIR'; grid.appendChild(empty); }
  if (d.count && empty) empty.remove();
}

poll();
setInterval(poll, 3000);
setInterval(() => cards.forEach(tick), 1000);
