"""Live HLS out of pre-cut segments: playlists computed from the wall clock (pure functions, no I/O).

A stream is a recording played in a loop on a fixed clock. Loop `L` starts at `epoch + L * period`; segment
`i` of that loop covers `[start_i, end_i)` on the recording's timeline and is listed from the moment its last
sample "has been captured" (`epoch + L * period + end_i`), exactly like a segment of a live encoder. Nothing
runs per stream: every request derives the same playlist from `now`, so all clients (players, taps) see one
live edge, and a restart of the server changes nothing.

  media sequence          L * n + i            (n = segments per loop; never repeats, never goes back)
  PROGRAM-DATE-TIME       epoch + L * period + start_i
  discontinuity sequence  L                    (the recording's timestamps restart with every loop)
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone

EPS = 1e-6


@dataclass(frozen=True)
class Timeline:
    """Segment durations of one rendition and their positions on the recording's timeline (seconds)."""

    durs: tuple[float, ...]
    starts: tuple[float, ...]
    ends: tuple[float, ...]

    @classmethod
    def of(cls, durs) -> "Timeline":
        starts, ends, t = [], [], 0.0
        for d in durs:
            starts.append(round(t, 6))
            t += d
            ends.append(round(t, 6))
        return cls(tuple(float(d) for d in durs), tuple(starts), tuple(ends))

    @property
    def n(self) -> int:
        return len(self.durs)

    @property
    def total(self) -> float:
        return self.ends[-1] if self.ends else 0.0

    @property
    def target(self) -> int:
        """EXT-X-TARGETDURATION: every EXTINF, rounded to the nearest integer, must not exceed it (RFC 8216)."""
        return max(1, max((int(d + 0.5) for d in self.durs), default=1))


@dataclass(frozen=True)
class Seg:
    loop: int
    idx: int
    seq: int        # media sequence number
    pdt: float      # wall-clock time of the segment's first sample (unix seconds)
    dur: float


def position(period: float, epoch: float, now: float) -> tuple[int, float]:
    """(loop number, seconds into the recording) of the live edge at `now`."""
    e = max(0.0, now - epoch)
    loop = int(e // period)
    return loop, e - loop * period


def live_window(tl: Timeline, period: float, epoch: float, now: float, size: int) -> list[Seg]:
    """The last `size` segments that are complete at `now`, oldest first."""
    n = tl.n
    if n == 0 or now < epoch:
        return []
    loop, pos = position(period, epoch, now)
    i = bisect_right(tl.ends, pos + EPS) - 1     # last segment of this loop whose end has passed
    out: list[Seg] = []
    while len(out) < size:
        if i < 0:                                # nothing (more) from this loop: continue with the previous one
            loop -= 1
            i = n - 1
            if loop < 0:
                break
        out.append(Seg(loop, i, loop * n + i, epoch + loop * period + tl.starts[i], tl.durs[i]))
        i -= 1
    out.reverse()
    return out


def iso(t: float) -> str:
    secs, ms = divmod(round(t * 1000), 1000)
    return datetime.fromtimestamp(secs, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _default_uri(idx: int) -> str:
    return f"{idx:05d}.ts"


def render_live(segs: list[Seg], target: int, uri=_default_uri) -> str:
    """Sliding-window media playlist. `uri(idx)` → the segment's URI (relative to the playlist)."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        f"#EXT-X-MEDIA-SEQUENCE:{segs[0].seq if segs else 0}",
        f"#EXT-X-DISCONTINUITY-SEQUENCE:{segs[0].loop if segs else 0}",
    ]
    for k, s in enumerate(segs):
        if k and s.idx == 0:                     # a new loop starts inside the window
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{iso(s.pdt)}")
        lines.append(f"#EXTINF:{s.dur:.6f},")
        lines.append(uri(s.idx))
    return "\n".join(lines) + "\n"


def render_vod(tl: Timeline, uri=_default_uri) -> str:
    """The whole recording as a seekable playlist."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{tl.target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    for i, d in enumerate(tl.durs):
        lines.append(f"#EXTINF:{d:.6f},")
        lines.append(uri(i))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def render_master(variants: list[dict], playlist: str) -> str:
    """variants: [{name, bandwidth, avg_bandwidth, width, height, fps, codecs}], in the order to list them."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for v in variants:
        attrs = [f"BANDWIDTH={int(v['bandwidth'])}", f"AVERAGE-BANDWIDTH={int(v['avg_bandwidth'])}"]
        if v.get("width") and v.get("height"):
            attrs.append(f"RESOLUTION={v['width']}x{v['height']}")
        if v.get("fps"):
            attrs.append(f"FRAME-RATE={v['fps']:.3f}")
        if v.get("codecs"):
            attrs.append(f'CODECS="{v["codecs"]}"')
        lines.append("#EXT-X-STREAM-INF:" + ",".join(attrs))
        lines.append(f"{v['name']}/{playlist}")
    return "\n".join(lines) + "\n"
