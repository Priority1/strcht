"""The library: every video of MEDIA_DIR, cut once into HLS segments + thumbnails, kept in DATA_DIR.

Per video, in DATA_DIR/<id>/:
  thumbs/00001.jpg …   one frame per THUMB_EVERY seconds (keyframes only: a 15 min video takes ~1 s)
  lo/00000.ts …        preview rendition: LO_HEIGHT, exact SEGMENT_SECONDS segments, the source's audio untouched
  hi/00000.ts …        full rendition: HI_MODE=encode → same cut as lo at the source's size (hardware encoder when
                       there is one) · HI_MODE=copy → the source's own packets, a segment per source GOP
  meta.json            what was built, from which file (size + mtime) and with which settings

Anything missing or built with other settings is rebuilt in the background; a stream is live as soon as one
rendition is there. The audio of every rendition is a stream copy of the source: what a tap hears is the recording.
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import live
from config import Config

VERSION = 1                                   # bump to rebuild every cache
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".mkv"}
PRIO = {"thumbs": 0, "copy": 1, "lo": 2, "hi": 3}   # thumbnails for all, then every stream on air, then full quality
LANG_WORDS = {
    "russian": "ru", "english": "en", "french": "fr", "german": "de", "japanese": "ja", "spanish": "es",
    "italian": "it", "portuguese": "pt", "polish": "pl", "turkish": "tr", "arabic": "ar", "chinese": "zh",
    "korean": "ko", "dutch": "nl", "ukrainian": "uk", "hindi": "hi",
}


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "stream"


# ── description.txt ──────────────────────────────────────────────
def parse_descriptions(text: str) -> dict[str, list[str]]:
    """`Name:` followed by `- note` lines → {name: [notes]}."""
    out: dict[str, list[str]] = {}
    cur = None
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith("-"):
            if cur is not None:
                out[cur].append(ln.lstrip("-").strip())
        elif ln.endswith(":"):
            cur = ln[:-1].strip()
            out.setdefault(cur, [])
    return out


def languages_of(notes: list[str]) -> list[str]:
    """Language codes named in the notes; the ones called native first."""
    native, other = [], []
    for note in notes:
        low = note.lower()
        for word, code in LANG_WORDS.items():
            if word in low:
                (other if "not native" in low or "native" not in low else native).append(code)
    return list(dict.fromkeys(native + other))


def match_descriptions(ids: list[str], desc: dict[str, list[str]]) -> dict[str, list[str]]:
    """Notes per stream id: by name, then by the closest name left (the file is `Nova_Kitchin`, the notes say `Nova_Kitchen`)."""
    by_slug = {slug(k): v for k, v in desc.items()}
    out = {i: by_slug.pop(i) for i in ids if i in by_slug}
    for i in ids:
        if i not in out:
            close = difflib.get_close_matches(i, list(by_slug), n=1, cutoff=0.8)
            if close:
                out[i] = by_slug.pop(close[0])
    return out


# ── ffmpeg ───────────────────────────────────────────────────────
_caps: dict | None = None


def ffmpeg_caps() -> dict:
    global _caps
    if _caps is None:
        def out(*args):
            try:
                return subprocess.run(["ffmpeg", "-hide_banner", *args], capture_output=True, text=True, timeout=20).stdout
            except (OSError, subprocess.SubprocessError):
                return ""
        enc = out("-encoders")
        _caps = {
            "ffmpeg": bool(enc),
            "videotoolbox_enc": "h264_videotoolbox" in enc,
            "videotoolbox_dec": "videotoolbox" in out("-hwaccels"),
        }
    return _caps


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode:
        raise RuntimeError(f"ffprobe: {r.stderr.strip()[-200:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise RuntimeError("no video stream")
    duration = float(d.get("format", {}).get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("unknown duration")
    total_bps = int(float(d.get("format", {}).get("bit_rate") or 0))
    a_bps = int(float(a.get("bit_rate") or 0)) if a else 0
    return {
        "duration": round(duration, 3),
        "width": int(v["width"]), "height": int(v["height"]),
        "fps": _fps(v),
        "vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None,
        "vbitrate": int(float(v.get("bit_rate") or 0)) or max(0, total_bps - a_bps),
        "abitrate": a_bps,
        "size": int(float(d.get("format", {}).get("size") or 0)),
    }


def _rate(text) -> float:
    try:
        num, _, den = str(text).partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _fps(v: dict) -> int:
    """A whole frame rate to cut on (recordings of live streams carry jittery timestamps: 24.003, 29.97…)."""
    f = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
    return min(60, max(10, round(f))) if 5 <= f <= 200 else 30


def codecs_of(vstream: dict, acodec: str | None) -> str:
    prof = {"baseline": (66, 0x00), "constrained baseline": (66, 0xE0), "main": (77, 0x40), "high": (100, 0x00)}
    idc = prof.get(str(vstream.get("profile", "")).lower())
    level = int(vstream.get("level") or 0)
    parts = []
    if vstream.get("codec_name") == "h264" and idc and level > 0:
        parts.append(f"avc1.{idc[0]:02x}{idc[1]:02x}{level:02x}")
    if acodec == "aac":
        parts.append("mp4a.40.2")
    return ",".join(parts) if len(parts) == (2 if acodec else 1) else ""


def _hls_out(out: Path, hls_time: float) -> list[str]:
    return ["-sn", "-dn", "-f", "hls", "-hls_time", f"{hls_time:.3f}", "-hls_list_size", "0",
            "-hls_playlist_type", "vod", "-start_number", "0",
            "-hls_segment_filename", str(out / "%05d.ts"), str(out / "index.m3u8")]


def rendition_args(src: Path, out: Path, spec: dict, hwdec: bool) -> list[str]:
    seg = spec["seg"]
    if spec["mode"] == "copy":
        return ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", *_hls_out(out, seg)]
    vf = [f"fps={spec['fps']}"]              # constant frame rate: a keyframe lands on every whole segment_seconds
    if spec.get("height"):
        vf.append(f"scale=-2:'min({spec['height']},ih)'")
    a = (["-hwaccel", "videotoolbox"] if hwdec else []) + ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(vf)]
    if spec["codec"] == "h264_videotoolbox":
        k = spec["bitrate_k"]
        a += ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-pix_fmt", "yuv420p", "-b:v", f"{k}k",
              "-maxrate", f"{int(k * 1.5)}k", "-g", str(max(1, round(spec["fps"] * seg))), "-bf", "0", "-allow_sw", "1"]
    else:
        k = spec["maxrate_k"]
        a += ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", spec.get("profile", "main"), "-pix_fmt", "yuv420p",
              "-crf", str(spec["crf"]), "-maxrate", f"{k}k", "-bufsize", f"{2 * k}k", "-bf", "0", "-sc_threshold", "0"]
    # the muxer cuts at the first keyframe after hls_time; keyframes exist only where they are forced
    return a + ["-force_key_frames", f"expr:gte(t,n_forced*{seg:g})", "-c:a", "copy", *_hls_out(out, seg * 0.8)]


def thumbs_args(src: Path, out: Path, every: float, width: int) -> list[str]:
    select = f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{max(0.5, every - 0.5):g})"
    return ["-skip_frame", "nokey", "-i", str(src), "-an", "-vf", f"select='{select}',showinfo,scale={width}:-2",
            "-fps_mode", "vfr", "-q:v", "4", str(out / "%05d.jpg")]


# ── model ────────────────────────────────────────────────────────
@dataclass
class Rendition:
    name: str
    spec: dict
    timeline: live.Timeline
    bandwidth: int
    avg_bandwidth: int
    width: int
    height: int
    fps: float
    codecs: str
    build: str            # changes when the segments do: part of every segment url

    def meta(self) -> dict:
        return {"spec": self.spec, "durs": list(self.timeline.durs), "bandwidth": self.bandwidth,
                "avg_bandwidth": self.avg_bandwidth, "width": self.width, "height": self.height, "fps": self.fps,
                "codecs": self.codecs, "build": self.build}

    @classmethod
    def from_meta(cls, name: str, m: dict) -> "Rendition":
        return cls(name, m["spec"], live.Timeline.of(m["durs"]), m["bandwidth"], m["avg_bandwidth"],
                   m["width"], m["height"], m["fps"], m.get("codecs", ""), m["build"])


class Stream:
    def __init__(self, sid: str, src: Path, fingerprint: dict, cfg: Config):
        self.id = sid
        self.name = src.stem
        self.src = src
        self.fingerprint = fingerprint
        self.dir = cfg.data_dir / sid
        self.info: dict = {}
        self.period = 0.0                       # length of one loop = the source's duration
        self.epoch = 0.0                        # when loop 0 started
        self.thumbs: list[float] = []           # time of storyboard frame i+1 (thumbs/%05d.jpg)
        self.poster = 1
        self.renditions: dict[str, Rendition] = {}
        self.jobs: dict[str, dict] = {}         # thumbs | lo | hi → {state, pct, error}
        self.notes: list[str] = []
        self.languages: list[str] = []

    def ready(self) -> list[Rendition]:
        """Renditions on air, lowest bandwidth first."""
        return sorted(self.renditions.values(), key=lambda r: r.bandwidth)

    def position(self, now: float) -> tuple[int, float]:
        return live.position(self.period, self.epoch, now)

    def thumb_at(self, t: float) -> int:
        """Number (1-based) of the storyboard frame showing position t, 0 when there is none."""
        n = 0
        for i, ft in enumerate(self.thumbs):
            if ft <= t + 0.001:
                n = i + 1
            else:
                break
        return n or (1 if self.thumbs else 0)


class Library:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.streams: dict[str, Stream] = {}
        self.lock = threading.RLock()
        self.q: queue.PriorityQueue = queue.PriorityQueue()
        self._n = itertools.count()
        self._stop = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._threads: list[threading.Thread] = []
        self._bad: dict[str, dict] = {}         # files that could not be read, by path → fingerprint

    # ── lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        caps = ffmpeg_caps()
        if not caps["ffmpeg"]:
            raise RuntimeError("ffmpeg / ffprobe not found on PATH")
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.scan()
        for i in range(max(1, self.cfg.workers)):
            self._spawn(self._worker, f"ingest-{i}")
        self._spawn(self._rescan, "rescan")

    def _spawn(self, fn, name):
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        with self.lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.terminate()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=5)

    def _rescan(self) -> None:
        while not self._stop.wait(self.cfg.rescan_seconds):
            try:
                self.scan()
            except Exception as e:  # noqa: BLE001
                log("scan error", type(e).__name__, e)

    # ── scan ─────────────────────────────────────────────────────
    def scan(self) -> None:
        """Bring `streams` in line with MEDIA_DIR and queue whatever is not built yet."""
        media = self.cfg.media_dir
        now = time.time()
        files = {}
        if media.is_dir():
            for p in sorted(media.iterdir()):
                if p.suffix.lower() in VIDEO_EXT and p.is_file() and not p.name.startswith("."):
                    st = p.stat()
                    if now - st.st_mtime > 5:               # not while it is still being written
                        files[p] = {"name": p.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        else:
            log(f"MEDIA_DIR {media} is not a directory")
        with self.lock:
            for sid, s in list(self.streams.items()):
                if files.get(s.src) != s.fingerprint:
                    log(f"- {s.name}: {'changed' if s.src in files else 'gone'}")
                    del self.streams[sid]
            known = {s.src for s in self.streams.values()}
            for p, fp in files.items():
                if p in known or self._bad.get(str(p)) == fp:
                    continue
                sid = base = slug(p.stem)
                k = 2
                while sid in self.streams:
                    sid, k = f"{base}_{k}", k + 1
                s = Stream(sid, p, fp, self.cfg)
                try:
                    self._load(s)
                except Exception as e:  # noqa: BLE001
                    self._bad[str(p)] = fp
                    log(f"! {p.name}: {type(e).__name__}: {e}")
                    continue
                self.streams[sid] = s
                self._plan(s)
            self._describe()

    def _describe(self) -> None:
        f = self.cfg.media_dir / "description.txt"
        try:
            desc = parse_descriptions(f.read_text(errors="replace")) if f.is_file() else {}
        except OSError:
            desc = {}
        notes = match_descriptions(list(self.streams), desc)
        for sid, s in self.streams.items():
            all_notes = notes.get(sid, [])
            s.notes = [n for n in all_notes if not n.lower().startswith("duration")]
            s.languages = languages_of(all_notes)

    def _load(self, s: Stream) -> None:
        """Take over what DATA_DIR already holds for this file; probe the file when it holds nothing."""
        meta = None
        try:
            meta = json.loads((s.dir / "meta.json").read_text())
        except (OSError, ValueError):
            pass
        if not meta or meta.get("version") != VERSION or meta.get("source") != s.fingerprint:
            if s.dir.exists():
                shutil.rmtree(s.dir)
            meta = {}
        s.dir.mkdir(parents=True, exist_ok=True)
        for part in s.dir.glob("*.part"):                   # a job that was interrupted
            shutil.rmtree(part, ignore_errors=True)
        s.info = meta.get("info") or probe(s.src)
        s.period = float(s.info["duration"])
        s.epoch = self.cfg.base_epoch + zlib.crc32(s.id.encode()) % max(1, int(s.period))   # streams out of step
        th = meta.get("thumbs") or {}
        if th.get("spec") == self._thumb_spec() and (s.dir / "thumbs").is_dir():
            s.thumbs, s.poster = th["times"], th.get("poster", 1)
        want = self._specs(s)
        for name, m in (meta.get("renditions") or {}).items():
            if m.get("spec") == want.get(name) and (s.dir / name / f"{len(m['durs']) - 1:05d}.ts").is_file():
                s.renditions[name] = Rendition.from_meta(name, m)
        self._save(s)

    def _thumb_spec(self) -> dict:
        return {"every": self.cfg.thumb_every, "width": self.cfg.thumb_width}

    def _specs(self, s: Stream) -> dict[str, dict]:
        cfg, info, caps = self.cfg, s.info, ffmpeg_caps()
        seg, fps = cfg.segment_seconds, info["fps"]
        src_k = max(300, info["vbitrate"] // 1000)
        lo = {"mode": "encode", "codec": "libx264", "height": cfg.lo_height, "fps": min(fps, 30), "crf": 27,
              "maxrate_k": 700, "seg": seg}
        if cfg.hi_mode == "copy":
            hi = {"mode": "copy", "seg": seg}
        elif caps["videotoolbox_enc"]:
            hi = {"mode": "encode", "codec": "h264_videotoolbox", "fps": fps, "seg": seg,
                  "bitrate_k": min(12000, int(src_k * 1.15))}      # a keyframe every second costs bits
        else:
            hi = {"mode": "encode", "codec": "libx264", "profile": "high", "fps": fps, "crf": 21,
                  "maxrate_k": min(12000, int(src_k * 1.5)), "seg": seg}
        return {"lo": lo, "hi": hi}

    def _plan(self, s: Stream) -> None:
        todo = []
        if not s.thumbs:
            todo.append(("thumbs", PRIO["thumbs"], self._job_thumbs, None))
        for name, spec in self._specs(s).items():
            if name not in s.renditions:
                todo.append((name, PRIO["copy" if spec["mode"] == "copy" else name], self._job_rendition, spec))
        for name, prio, fn, spec in todo:
            if name in s.jobs:
                continue
            s.jobs[name] = {"state": "queued", "pct": 0.0, "error": None}
            self.q.put((prio, s.period, next(self._n), s, name, fn, spec))

    def _save(self, s: Stream) -> None:
        meta = {
            "version": VERSION, "source": s.fingerprint, "info": s.info,
            "thumbs": {"spec": self._thumb_spec(), "times": s.thumbs, "poster": s.poster} if s.thumbs else None,
            "renditions": {n: r.meta() for n, r in s.renditions.items()},
        }
        tmp = s.dir / "meta.json.tmp"
        tmp.write_text(json.dumps(meta))
        tmp.replace(s.dir / "meta.json")

    # ── jobs ─────────────────────────────────────────────────────
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _prio, _dur, _n, s, name, fn, spec = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.streams.get(s.id) is not s:             # the file went away while the job waited
                continue
            job = s.jobs[name]
            job["state"], t0 = "running", time.time()
            try:
                fn(s, name, spec, job)
                job["state"], job["pct"] = "done", 100.0
                log(f"✓ {s.name} · {name} · {time.time() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001
                if self._stop.is_set():
                    return
                job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"[:400]
                log(f"✗ {s.name} · {name}: {job['error']}")

    def _ffmpeg(self, args: list[str], logfile: Path, duration: float, job: dict) -> None:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
        with open(logfile, "wb") as lf:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf, text=True)
            with self.lock:
                self._procs.add(p)
            try:
                for ln in p.stdout:
                    if ln.startswith("out_time_us=") and duration > 0:
                        try:
                            job["pct"] = round(min(99.9, int(ln.split("=", 1)[1]) / 1e6 / duration * 100), 1)
                        except ValueError:
                            pass
                rc = p.wait()
            finally:
                with self.lock:
                    self._procs.discard(p)
                if p.poll() is None:
                    p.kill()
        if self._stop.is_set():
            raise RuntimeError("stopped")
        if rc:
            tail = logfile.read_text(errors="replace").strip().splitlines()[-3:]
            raise RuntimeError(f"ffmpeg exit {rc}: {' | '.join(tail)}"[:400])

    def _job_thumbs(self, s: Stream, name: str, _spec, job: dict) -> None:
        out, part = s.dir / "thumbs", s.dir / "thumbs.part"
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        logfile = s.dir / "thumbs.log"
        self._ffmpeg(thumbs_args(s.src, part, self.cfg.thumb_every, self.cfg.thumb_width), logfile, s.period, job)
        n = len(list(part.glob("*.jpg")))
        if not n:
            raise RuntimeError("no frames")
        times = [round(float(t), 3) for t in re.findall(r"pts_time:\s*([0-9.]+)", logfile.read_text(errors="replace"))]
        if len(times) != n:                                 # the filter log is the truth; without it, the plan
            times = [round(i * self.cfg.thumb_every, 3) for i in range(n)]
        shutil.rmtree(out, ignore_errors=True)
        part.rename(out)
        with self.lock:
            s.thumbs = times
            s.poster = s.thumb_at(max(5.0, s.period * 0.1))  # a frame from inside the video, not its first one
            self._save(s)

    def _job_rendition(self, s: Stream, name: str, spec: dict, job: dict) -> None:
        out, part = s.dir / name, s.dir / f"{name}.part"
        logfile = s.dir / f"{name}.log"
        hw = spec["mode"] == "encode" and ffmpeg_caps()["videotoolbox_dec"]
        for hwdec in ([True, False] if hw else [False]):   # hardware decoding first, software when it refuses the file
            shutil.rmtree(part, ignore_errors=True)
            part.mkdir(parents=True)
            try:
                self._ffmpeg(rendition_args(s.src, part, spec, hwdec), logfile, s.period, job)
                break
            except RuntimeError:
                if not hwdec or self._stop.is_set():
                    raise
        durs = [float(m) for m in re.findall(r"#EXTINF:([0-9.]+)", (part / "index.m3u8").read_text())]
        sizes = [(part / f"{i:05d}.ts").stat().st_size for i in range(len(durs))]
        if not durs:
            raise RuntimeError("no segments")
        v = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-print_format", "json", "-show_entries",
             "stream=codec_name,profile,level,width,height,avg_frame_rate", str(part / "00000.ts")],
            capture_output=True, text=True, timeout=60).stdout or "{}").get("streams") or [{}]
        v = v[0]
        avg = sum(sizes) * 8 / sum(durs)
        r = Rendition(
            name=name, spec=spec, timeline=live.Timeline.of(durs),
            # peak over whole segments (the last one may be a single frame)
            bandwidth=int(max((sz * 8 / d for sz, d in zip(sizes, durs) if d >= 0.5), default=avg)),
            avg_bandwidth=int(avg),
            width=int(v.get("width") or s.info["width"]), height=int(v.get("height") or s.info["height"]),
            fps=round(_rate(v.get("avg_frame_rate")) or spec.get("fps") or s.info["fps"], 3),
            codecs=codecs_of(v, s.info.get("acodec")),
            build=hashlib.sha1(f"{s.fingerprint}{spec}{time.time()}".encode()).hexdigest()[:8],
        )
        (part / "index.m3u8").unlink()
        shutil.rmtree(out, ignore_errors=True)
        part.rename(out)
        with self.lock:
            s.renditions = {**s.renditions, name: r}
            self._save(s)

    # ── views ────────────────────────────────────────────────────
    def all(self) -> list[Stream]:
        with self.lock:                                     # a scan may be adding / dropping streams
            streams = list(self.streams.values())
        return sorted(streams, key=lambda s: s.name.lower())

    def on_air(self) -> list[Stream]:
        return [s for s in self.all() if s.renditions]
