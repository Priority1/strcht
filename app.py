#!/usr/bin/env python3
"""strcht — recordings played as live streams, behind a beacon: the list of streams that are on air.

Every video of MEDIA_DIR is on air as a looping live HLS stream (1 s MPEG-TS segments, PROGRAM-DATE-TIME,
two variants), with thumbnails. `GET /pinhole/list` is the beacon a live platform would offer, so a console
written against such a beacon — Palabra's live translation console, see scripts/console.sh — runs on top of
the recordings as it would on top of real live streams.

  /pinhole/list                      [{streamId, userName, masterUrl, previewUrl, …}]   the beacon
  /hls/{id}/master.m3u8              live: variants hi, lo            /hls/{id}/vod.m3u8   the recording, seekable
  /hls/{id}/{lo|hi}/live.m3u8        live media playlist              …/vod.m3u8 · …/00042.ts
  /thumbs/{id}/live.jpg              the frame on air now             poster.jpg · 00001.jpg … · storyboard.json|vtt
  /api/streams                       everything above + build progress (the wall at / reads it)
"""
from __future__ import annotations

import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import live
from config import Config
from library import Library, Rendition, Stream

PUBLIC = Path(__file__).resolve().parent / "public"
M3U8 = "application/vnd.apple.mpegurl"
NO_CACHE = {"Cache-Control": "no-cache, no-store, max-age=0"}
FOREVER = {"Cache-Control": "public, max-age=31536000, immutable"}   # segment urls carry the build id


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.from_env()
    lib = Library(cfg)

    @asynccontextmanager
    async def lifespan(_app):
        lib.start()
        print(f"strcht  http://{cfg.host}:{cfg.port}  media={cfg.media_dir}  data={cfg.data_dir}"
              f"  streams={len(lib.streams)}  hi={cfg.hi_mode}  token={'set' if cfg.pinhole_token else 'off'}", flush=True)
        yield
        lib.stop()

    app = FastAPI(title="strcht", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.lib, app.state.cfg = lib, cfg
    # players and taps come from other origins (the console's page plays our HLS directly)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "HEAD", "OPTIONS"], allow_headers=["*"])

    @app.middleware("http")
    async def _fresh_assets(request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    # ── helpers ──────────────────────────────────────────────────
    def base_url(request: Request) -> str:
        if cfg.public_url:
            return cfg.public_url
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or f"{cfg.host}:{cfg.port}"
        return f"{proto}://{host}"

    def stream_or_404(sid: str) -> Stream:
        s = lib.streams.get(sid)
        if not s:
            raise HTTPException(404, "no such stream")
        return s

    def rendition_or_404(s: Stream, name: str) -> Rendition:
        r = s.renditions.get(name)
        if not r:
            raise HTTPException(404, "no such rendition")
        return r

    def record(s: Stream, base: str, now: float) -> dict:
        """One beacon record. The first six fields are the beacon's; the rest is ours."""
        u = urlsplit(base)
        loop, pos = s.position(now)
        return {
            "streamId": s.id,
            "userName": s.name,
            "masterUrl": f"{base}/hls/{s.id}/master.m3u8",
            "previewUrl": f"{base}/hls/{s.id}/{s.ready()[0].name}/live.m3u8",
            "ipAddress": u.hostname or cfg.host,
            "port": u.port or (443 if u.scheme == "https" else 80),
            "thumbnailUrl": f"{base}/thumbs/{s.id}/live.jpg",
            "posterUrl": f"{base}/thumbs/{s.id}/poster.jpg",
            "durationSeconds": s.period,
            "positionSeconds": round(pos, 3),
            "loop": loop,
            "languages": s.languages,
            "notes": s.notes,
        }

    def master(s: Stream, playlist: str) -> Response:
        if not s.renditions:
            raise HTTPException(404, "stream is not on air yet")
        variants = [{"name": r.name, "bandwidth": r.bandwidth, "avg_bandwidth": r.avg_bandwidth, "width": r.width,
                     "height": r.height, "fps": r.fps, "codecs": r.codecs} for r in reversed(s.ready())]   # best first
        return Response(live.render_master(variants, playlist), media_type=M3U8, headers=NO_CACHE)

    # ── the beacon ───────────────────────────────────────────────
    @app.get("/pinhole/list")
    def pinhole_list(request: Request):
        if cfg.pinhole_token:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), cfg.pinhole_token):
                return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
        base, now = base_url(request), time.time()
        return JSONResponse([record(s, base, now) for s in lib.on_air()], headers=NO_CACHE)

    # ── HLS ──────────────────────────────────────────────────────
    @app.get("/hls/{sid}/master.m3u8")
    def hls_master(sid: str):
        return master(stream_or_404(sid), "live.m3u8")

    @app.get("/hls/{sid}/vod.m3u8")
    def hls_vod_master(sid: str):
        return master(stream_or_404(sid), "vod.m3u8")

    @app.get("/hls/{sid}/{rend}/live.m3u8")
    def hls_live(sid: str, rend: str):
        s = stream_or_404(sid)
        r = rendition_or_404(s, rend)
        segs = live.live_window(r.timeline, s.period, s.epoch, time.time(), cfg.live_window)
        return Response(live.render_live(segs, r.timeline.target, lambda i: f"{i:05d}.ts?v={r.build}"),
                        media_type=M3U8, headers=NO_CACHE)

    @app.get("/hls/{sid}/{rend}/vod.m3u8")
    def hls_vod(sid: str, rend: str):
        r = rendition_or_404(stream_or_404(sid), rend)
        return Response(live.render_vod(r.timeline, lambda i: f"{i:05d}.ts?v={r.build}"), media_type=M3U8, headers=NO_CACHE)

    @app.get("/hls/{sid}/{rend}/{n}.ts")
    def hls_segment(sid: str, rend: str, n: str):
        s = stream_or_404(sid)
        r = rendition_or_404(s, rend)
        if not n.isdigit() or int(n) >= r.timeline.n:
            raise HTTPException(404, "no such segment")
        return FileResponse(s.dir / r.name / f"{int(n):05d}.ts", media_type="video/mp2t", headers=FOREVER)

    # ── thumbnails ───────────────────────────────────────────────
    def thumb(s: Stream, n: int, headers: dict) -> FileResponse:
        if not 1 <= n <= len(s.thumbs):
            raise HTTPException(404, "no such thumbnail")
        return FileResponse(s.dir / "thumbs" / f"{n:05d}.jpg", media_type="image/jpeg", headers=headers)

    @app.get("/thumbs/{sid}/live.jpg")
    def thumb_live(sid: str):
        s = stream_or_404(sid)
        return thumb(s, s.thumb_at(s.position(time.time())[1]), NO_CACHE)

    @app.get("/thumbs/{sid}/poster.jpg")
    def thumb_poster(sid: str):
        s = stream_or_404(sid)
        return thumb(s, s.poster, {"Cache-Control": "public, max-age=300"})

    @app.get("/thumbs/{sid}/storyboard.json")
    def storyboard(sid: str, request: Request):
        s, base = stream_or_404(sid), base_url(request)
        return {"every": cfg.thumb_every, "width": cfg.thumb_width, "duration": s.period,
                "frames": [{"n": i + 1, "t": t, "url": f"{base}/thumbs/{s.id}/{i + 1:05d}.jpg"} for i, t in enumerate(s.thumbs)]}

    @app.get("/thumbs/{sid}/storyboard.vtt")
    def storyboard_vtt(sid: str, request: Request):
        """WebVTT thumbnail track (the format players take for seek-bar previews)."""
        s, base = stream_or_404(sid), base_url(request)

        def ts(t):
            ms = round(t * 1000)
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"

        out = ["WEBVTT", ""]
        for i, t in enumerate(s.thumbs):
            start = t if i else 0.0                           # the first frame stands for everything before the second
            end = s.thumbs[i + 1] if i + 1 < len(s.thumbs) else s.period
            out += [f"{ts(start)} --> {ts(max(end, start + 0.001))}", f"{base}/thumbs/{s.id}/{i + 1:05d}.jpg", ""]
        return Response("\n".join(out), media_type="text/vtt", headers=NO_CACHE)

    @app.get("/thumbs/{sid}/{n}.jpg")
    def thumb_n(sid: str, n: str):
        if not n.isdigit():
            raise HTTPException(404, "no such thumbnail")
        return thumb(stream_or_404(sid), int(n), {"Cache-Control": "public, max-age=3600"})

    # ── our own wall ─────────────────────────────────────────────
    def describe(s: Stream, base: str, now: float) -> dict:
        loop, pos = s.position(now)
        on_air = bool(s.renditions)
        d = {
            "id": s.id, "name": s.name, "onAir": on_air, "file": s.src.name,
            "duration": s.period, "position": round(pos, 3), "loop": loop,
            "width": s.info.get("width"), "height": s.info.get("height"), "fps": s.info.get("fps"),
            "languages": s.languages, "notes": s.notes,
            "thumbs": len(s.thumbs), "thumb": s.thumb_at(pos), "poster": s.poster,
            "jobs": {k: dict(v) for k, v in s.jobs.items() if v["state"] != "done"},
            "renditions": [{
                "name": r.name, "mode": r.spec["mode"], "codec": r.spec.get("codec", "copy"), "width": r.width,
                "height": r.height, "fps": r.fps, "bandwidth": r.bandwidth, "avgBandwidth": r.avg_bandwidth,
                "segments": r.timeline.n, "targetDuration": r.timeline.target, "codecs": r.codecs,
                "liveUrl": f"{base}/hls/{s.id}/{r.name}/live.m3u8",
            } for r in reversed(s.ready())],
            "storyboardUrl": f"{base}/thumbs/{s.id}/storyboard.json",
            "storyboardVttUrl": f"{base}/thumbs/{s.id}/storyboard.vtt",
        }
        if on_air:
            d["beacon"] = record(s, base, now)
            d["vodUrl"] = f"{base}/hls/{s.id}/vod.m3u8"
        return d

    @app.get("/api/streams")
    def api_streams(request: Request):
        base, now = base_url(request), time.time()
        streams = [describe(s, base, now) for s in lib.all()]
        return JSONResponse({
            "ok": True, "now": round(now, 3), "count": len(streams), "onAir": sum(s["onAir"] for s in streams),
            "building": sum(len(s["jobs"]) for s in streams),
            "pinholeUrl": f"{base}/pinhole/list", "tokenRequired": bool(cfg.pinhole_token),
            "segmentSeconds": cfg.segment_seconds, "window": cfg.live_window, "hiMode": cfg.hi_mode,
            "streams": streams,
        }, headers=NO_CACHE)

    @app.get("/api/streams/{sid}")
    def api_stream(sid: str, request: Request):
        return JSONResponse({"ok": True, "now": round(time.time(), 3),
                             "stream": describe(stream_or_404(sid), base_url(request), time.time())}, headers=NO_CACHE)

    @app.get("/healthz")
    def healthz():
        return Response("ok", media_type="text/plain")

    app.mount("/", StaticFiles(directory=str(PUBLIC), html=True), name="public")
    return app


if __name__ == "__main__":
    import uvicorn

    _cfg = Config.from_env()
    uvicorn.run(create_app(_cfg), host=_cfg.host, port=_cfg.port, log_level="warning")
