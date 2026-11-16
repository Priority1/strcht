# Deploying strcht

Written for whoever — or whatever agent — puts this on a server. Generic on purpose: one container, two
volumes, one port. Adapt it to the platform at hand (compose, Kubernetes, a VM).

## What runs

| | |
|---|---|
| image | `docker build -t strcht .` (Python 3.12 + ffmpeg; no build args) |
| process | one, `python app.py`, as uid `10001` |
| port | `8790` (HTTP) |
| health | `GET /healthz` → `200 ok` (liveness and readiness) |
| replicas | **1** — the jobs that build `/data` run inside the process; two would build the same files |
| `/media` | the videos (`*.mp4`, `.m4v`, `.mov`, `.mkv`, top level only) and optionally `description.txt`. Read-only is enough. Not in git, not in the image |
| `/data` | segments and thumbnails built from `/media`, about 1.2× its size. Must be writable by uid `10001` and should persist: it is a cache, but an empty one means the build below runs again |
| outbound | none from the server. The pages load `hls.js` from `cdn.jsdelivr.net` in the viewer's browser |

```bash
docker run -d --name strcht -p 8790:8790 \
  -v /srv/videos:/media:ro -v strcht-data:/data \
  -e PUBLIC_URL=https://streams.example.com \
  strcht
```

## Settings

All optional; the full list with defaults is `.env.example`. The ones that matter on a server:

| | |
|---|---|
| `PUBLIC_URL` | the address viewers reach this service at, e.g. `https://streams.example.com`. The beacon hands out absolute stream urls that **browsers fetch directly**, so set it whenever the service sits behind a proxy or ingress. Empty = the `Host` of each request |
| `PINHOLE_TOKEN` | when set, `GET /pinhole/list` requires `authorization: bearer <token>`. Keep it in the platform's secret store |
| `HI_MODE` | `encode` (default: exact 1 s segments; CPU-heavy on the first start) or `copy` (instant, segments as long as the videos' keyframe interval) |
| `INGEST_WORKERS` | parallel ffmpeg jobs during the build (default 4); set it to the CPUs available |
| `HOST`, `PORT`, `MEDIA_DIR`, `DATA_DIR` | preset in the image: `0.0.0.0`, `8790`, `/media`, `/data` |

## First start

Nothing is built in advance. On start the service scans `/media` and, in the background: makes thumbnails
(seconds), then a 360p rendition of every video — a stream is **on air** as soon as it has one — then the
full-size rendition. Later starts reuse `/data` and are on air at once.

- CPU: the build uses what it gets. A server has no hardware encoder, so plan roughly real time ÷ a few per
  core for the full-size rendition; 2–4 cores are enough to be on air within minutes for a few hours of video.
  After the build the service idles (it only serves files): 0.5 CPU / 512 MiB is plenty.
- Progress: `GET /api/streams` → `onAir`, `count`, `building` (jobs left), and per stream `jobs`.
- New, changed and removed files in `/media` are picked up within 30 s; no restart needed.

## In front of it

- There is **no sign-in** on the pages, the streams or the thumbnails; only the beacon can ask for a token.
  Put access control in front (ingress auth, VPN, IP allow-list) unless the videos are public.
- Plain HTTP GET only, no WebSockets. Responses are small (playlists) or a few MB (segments); do not buffer or
  cache `*.m3u8`, do cache `*.ts` (they are immutable: the url changes when the content does).
- If a page on HTTPS plays these streams (the console below), this service must be on HTTPS too.

## Check it

```bash
curl -fsS $URL/healthz                                   # ok
curl -fsS $URL/api/streams | jq '{count, onAir, building}'
curl -fsS $URL/pinhole/list | jq '.[0] | {streamId, masterUrl, previewUrl}'   # urls must start with PUBLIC_URL
curl -fsS "$(curl -fsS $URL/pinhole/list | jq -r '.[0].masterUrl')"            # an HLS master playlist
```

Then open `$URL/` — every stream shows a thumbnail and `LIVE`; a click plays it.

## The console on top (optional)

A live translation console can use this service as its source of streams. It is a separate project with its
own image, settings and secrets (its Palabra API key); deploy it its own way and add:

| | |
|---|---|
| its beacon url variable | `<PUBLIC_URL or in-cluster address of strcht>/pinhole/list` |
| its beacon token variable | the value of `PINHOLE_TOKEN`, when one is set |
| presets | `presets.json` (in the image; `PRESETS_FILE` to use another) gives each video its own source language (never `auto`) and translation model — `delta2` when Delta2 serves that source (en de es fr it nl pt ru), `auto` otherwise. The beacon carries them as `sourceLanguage`, `detectableLanguages`, `translationModel`; the console starts that stream's session with them and shows `Translation model: DELTA 2` / `AUTO` in its status line. Edits are picked up within 30 s |
| reachability | the console's **server** reads the beacon and the streams; the viewer's **browser** plays the streams from `PUBLIC_URL` directly (CORS is open) |

To show the console without names it was written for, start it through `console/skin.py` of this repository
instead of its own entry point: put that one file next to the console's code (image layer, volume or
ConfigMap) and run, from the console's directory,

```bash
SKIN_HIDE=<name>[,<name>…] python -m uvicorn skin:create --factory --host 0.0.0.0 --port <port>
```

`SKIN_HIDE` belongs in the deployment's configuration, not in git. `scripts/console.sh` does all of this for a
local checkout and is the reference.

## Operating

- Restart, redeploy, roll back: any time. Streams are computed from the clock, so players continue where
  they were; `/data` is reused as long as the videos and the cut settings are the same.
- Update: new image, same volumes.
- Losing `/data` costs the first-start build, nothing else. Losing `/media` takes the streams off air.
