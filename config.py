"""Settings (environment / .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass
class Config:
    media_dir: Path = Path("~/Downloads/Mayflower").expanduser()   # *.mp4 here are the streams (not recursive)
    data_dir: Path = ROOT / "data"                                 # segments, thumbnails, meta (rebuilt when missing)
    presets_file: Path = ROOT / "presets.json"                     # translation preset per video, for the console
    host: str = "127.0.0.1"
    port: int = 8790
    public_url: str = ""          # base of the urls in /pinhole/list; empty = the Host the request came to
    pinhole_token: str = ""       # when set, /pinhole/list wants `authorization: bearer <token>`
    segment_seconds: float = 1.0  # what low-latency live platforms cut, and what the console on top is tuned for
    live_window: int = 12         # segments in a live playlist
    hi_mode: str = "encode"       # encode: re-encode with a keyframe every segment_seconds · copy: lossless, segment = source GOP
    lo_height: int = 360          # the preview rendition
    thumb_every: float = 10.0     # one storyboard frame per this many seconds
    thumb_width: int = 320
    workers: int = 4              # parallel ffmpeg jobs
    rescan_seconds: float = 30.0  # look for new / removed files this often
    base_epoch: float = 1767225600.0   # 2026-01-01T00:00:00Z: loop 0 of every stream starts here (+ a per-stream offset)

    @classmethod
    def from_env(cls) -> "Config":
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        d = cls()
        env = os.getenv
        cfg = cls(
            media_dir=Path(env("MEDIA_DIR") or d.media_dir).expanduser(),
            data_dir=Path(env("DATA_DIR") or d.data_dir).expanduser(),
            presets_file=Path(env("PRESETS_FILE") or d.presets_file).expanduser(),
            host=env("HOST") or d.host,
            port=int(env("PORT") or d.port),
            public_url=(env("PUBLIC_URL") or "").rstrip("/"),
            pinhole_token=env("PINHOLE_TOKEN") or "",
            segment_seconds=float(env("SEGMENT_SECONDS") or d.segment_seconds),
            live_window=int(env("LIVE_WINDOW_SEGMENTS") or d.live_window),
            hi_mode=(env("HI_MODE") or d.hi_mode).strip().lower(),
            lo_height=int(env("LO_HEIGHT") or d.lo_height),
            thumb_every=float(env("THUMB_EVERY_SECONDS") or d.thumb_every),
            thumb_width=int(env("THUMB_WIDTH") or d.thumb_width),
            workers=int(env("INGEST_WORKERS") or d.workers),
            rescan_seconds=float(env("RESCAN_SECONDS") or d.rescan_seconds),
        )
        cfg.check()
        return cfg

    def check(self) -> None:
        if self.hi_mode not in ("encode", "copy"):
            raise ValueError(f"HI_MODE={self.hi_mode!r}: expected encode or copy")
        if not 0.5 <= self.segment_seconds <= 10:
            raise ValueError(f"SEGMENT_SECONDS={self.segment_seconds}: expected 0.5–10")
        if self.live_window < 3:
            raise ValueError(f"LIVE_WINDOW_SEGMENTS={self.live_window}: a live playlist needs at least 3 segments")
