'use strict';
// One stream: LIVE (the looping stream the beacon serves) or RECORDING (the same segments as a seekable
// playlist). The bar under the picture is the recording's timeline; hovering it shows the storyboard frame.

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function clock(t) {
  t = Math.max(0, Math.floor(t));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}

const id = new URLSearchParams(location.search).get('id') || '';
const video = $('#video'), msg = $('#msg'), scrub = $('#scrub'), tip = $('#tip');
let S = null;        // the stream as /api/streams/{id} describes it
let frames = [];     // storyboard: [{n, t, url}]
let mode = '';       // live | vod
let hls = null;
let skew = 0;        // server clock − this clock, s
let loopStart = 0;   // server time at which the loop on air started

const mod = (a, n) => ((a % n) + n) % n;
const serverNow = () => Date.now() / 1000 + skew;
const onAirPos = () => mod(serverNow() - loopStart, S.duration);
function playPos() {
  if (mode === 'vod') return video.currentTime || 0;
  const d = hls && hls.playingDate; // PROGRAM-DATE-TIME of the frame on screen
  return d instanceof Date && isFinite(d.getTime()) ? mod(d.getTime() / 1000 - loopStart, S.duration) : onAirPos();
}

async function fetchStream() {
  const r = await fetch('/api/streams/' + encodeURIComponent(id));
  if (r.status === 404) throw new Error('no such stream');
  const d = await r.json();
  skew = d.now - Date.now() / 1000;
  loopStart = d.now - d.stream.position;
  return d.stream;
}

// ── player ───────────────────────────────────────────────────────
function start(m, at) {
  mode = m;
  if (hls) { hls.destroy(); hls = null; }
  $('#b-live').classList.toggle('on', m === 'live');
  $('#b-vod').classList.toggle('on', m === 'vod');
  const url = m === 'live' ? S.beacon.masterUrl : S.vodUrl;
  const play = () => video.play().catch(() => { video.muted = true; video.play().catch(() => {}); });
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls(m === 'live'
      ? { lowLatencyMode: true, liveSyncDuration: 3, liveMaxLatencyDuration: 9, maxLiveSyncPlaybackRate: 1.2, backBufferLength: 10 }
      : { startPosition: at ?? 0 });
    hls.on(Hls.Events.MANIFEST_PARSED, play);
    hls.on(Hls.Events.ERROR, (_e, d) => {
      if (!d.fatal) return;
      if (d.type === Hls.ErrorTypes.NETWORK_ERROR) setTimeout(() => hls && hls.startLoad(), 1000);
      else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
      else $('#stat').textContent = 'player error: ' + d.details;
    });
    hls.loadSource(url);
    hls.attachMedia(video);
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    video.onloadedmetadata = () => { if (m === 'vod' && at) video.currentTime = at; play(); };
  } else {
    $('#stat').textContent = 'this browser cannot play HLS';
  }
}

$('#b-live').onclick = () => start('live');
$('#b-vod').onclick = () => start('vod', playPos());

// ── the bar: position, what is on air, storyboard on hover ───────
function frameAt(t) {
  let lo = 0, hi = frames.length - 1, best = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t <= t + 0.001) { best = frames[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  return best || frames[0] || null;
}

function timeAt(ev) {
  const r = $('.track').getBoundingClientRect();
  return Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * S.duration;
}

scrub.addEventListener('mousemove', (ev) => {
  const t = timeAt(ev), f = frameAt(t);
  const box = scrub.getBoundingClientRect();
  tip.hidden = false;
  tip.style.left = Math.min(box.width - 100, Math.max(100, ev.clientX - box.left)) + 'px';
  $('#tip-time').textContent = clock(t);
  const img = $('#tip-img');
  img.hidden = !f;
  if (f && img.dataset.n !== String(f.n)) { img.dataset.n = f.n; img.src = f.url; }
});
scrub.addEventListener('mouseleave', () => { tip.hidden = true; });
scrub.addEventListener('click', (ev) => {
  const t = timeAt(ev);
  if (mode === 'vod') video.currentTime = t;
  else start('vod', t);
});

function tick() {
  if (!S || !mode) return;
  const pos = playPos(), air = onAirPos();
  $('#fill').style.width = (pos / S.duration) * 100 + '%';
  $('#edge').style.left = (air / S.duration) * 100 + '%';
  const parts = [`${clock(pos)} / ${clock(S.duration)}`];
  if (mode === 'live') {
    parts.push(`loop ${Math.floor((serverNow() - loopStart) / S.duration) + S.loop}`);
    if (hls && isFinite(hls.latency) && hls.latency > 0) parts.push(`${hls.latency.toFixed(1)}s behind live edge`);
  } else {
    parts.push(`on air: ${clock(air)}`);
  }
  const lv = hls && hls.levels && hls.levels[hls.currentLevel];
  if (lv) parts.push(`${lv.height}p ${Math.round(lv.bitrate / 1000)}k`);
  $('#stat').textContent = parts.join(' · ');
}

// ── the side panel ───────────────────────────────────────────────
function urlRow(label, url) {
  const row = document.createElement('div');
  row.className = 'url';
  row.innerHTML = `<label>${esc(label)}</label><code class="copy" title="click to copy">${esc(url)}</code>`;
  const code = row.querySelector('code');
  code.onclick = async () => {
    try { await navigator.clipboard.writeText(url); code.classList.add('done'); setTimeout(() => code.classList.remove('done'), 900); } catch (_) {}
  };
  return row;
}

function renderSide() {
  document.title = `${S.name} · strcht`;
  $('#name').textContent = S.name;
  $('#about').textContent = `${S.file} · ${S.width}×${S.height} · ${S.fps} fps · ${clock(S.duration)}`;
  $('#chips').innerHTML = S.languages.map((l) => `<span class="chip">${esc(l)}</span>`).join('');
  $('#notes').innerHTML = S.notes.map((n) => `<li>${esc(n)}</li>`).join('');
  const jobs = Object.entries(S.jobs).map(([n, j]) => `<tr><td>${esc(n)}</td><td colspan="4">${j.state === 'error' ? 'failed: ' + esc(j.error) : esc(j.state) + ' ' + Math.floor(j.pct) + '%'}</td></tr>`).join('');
  $('#rends').innerHTML = '<tr><th></th><th>size</th><th>fps</th><th>kbps</th><th>segment</th></tr>' + S.renditions.map((r) =>
    `<tr><td>${esc(r.name)}</td><td>${r.width}×${r.height}</td><td>${Math.round(r.fps)}</td><td>${Math.round(r.avgBandwidth / 1000)}</td><td>${r.targetDuration}s · ${esc(r.codec)}</td></tr>`).join('') + jobs;
  const urls = $('#urls');
  urls.innerHTML = '';
  urls.appendChild(urlRow('master', S.beacon.masterUrl));
  urls.appendChild(urlRow('preview', S.beacon.previewUrl));
  urls.appendChild(urlRow('recording', S.vodUrl));
  urls.appendChild(urlRow('thumbnail', S.beacon.thumbnailUrl));
  urls.appendChild(urlRow('storyboard', S.storyboardVttUrl));
  urls.appendChild(urlRow('beacon', new URL('/pinhole/list', location.href).href));
  const { streamId, userName, masterUrl, previewUrl, ipAddress, port } = S.beacon;
  $('#record').textContent = JSON.stringify({ streamId, userName, masterUrl, previewUrl, ipAddress, port }, null, 2);
}

// ── boot ─────────────────────────────────────────────────────────
async function boot() {
  try {
    S = await fetchStream();
  } catch (e) {
    msg.textContent = e.message === 'no such stream' ? `no stream "${id}"` : 'server unreachable — retrying…';
    if (e.message !== 'no such stream') setTimeout(boot, 3000);
    return;
  }
  if (!S.onAir) {
    const j = Object.entries(S.jobs).map(([n, v]) => `${n} ${v.state}${v.state === 'running' ? ' ' + Math.floor(v.pct) + '%' : ''}`).join(' · ');
    msg.textContent = `${S.name} is not on air yet — ${j || 'waiting'}`;
    setTimeout(boot, 2000);
    return;
  }
  msg.hidden = true;
  $('#watch').hidden = false;
  renderSide();
  try { frames = (await (await fetch(S.storyboardUrl)).json()).frames || []; } catch (_) { frames = []; }
  start('live');
  setInterval(tick, 250);
  setInterval(async () => { try { S = await fetchStream(); renderSide(); } catch (_) {} }, 15000); // builds finishing, clock drift
}
boot();
