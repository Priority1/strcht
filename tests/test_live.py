"""The wall-clock playlist: what a client polling it over several loops must see."""
import re

import live

EPOCH = 1_000_000.0


def parse(text):
    """Media playlist → (media sequence, [(seq, discontinuity number, pdt, dur, uri)])."""
    seq = int(re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text).group(1))
    disc = int(re.search(r"#EXT-X-DISCONTINUITY-SEQUENCE:(\d+)", text).group(1))
    out, pdt, dur = [], None, None
    for ln in text.splitlines():
        if ln == "#EXT-X-DISCONTINUITY":
            disc += 1
        elif ln.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            from datetime import datetime
            pdt = datetime.fromisoformat(ln.split(":", 1)[1].replace("Z", "+00:00")).timestamp()
        elif ln.startswith("#EXTINF:"):
            dur = float(ln.split(":")[1].rstrip(","))
        elif ln and not ln.startswith("#"):
            out.append((seq, disc, pdt, dur, ln))
            seq += 1
    return out


def test_timeline():
    tl = live.Timeline.of([1.0, 1.0, 0.5])
    assert tl.starts == (0.0, 1.0, 2.0) and tl.ends == (1.0, 2.0, 2.5)
    assert tl.n == 3 and tl.total == 2.5 and tl.target == 1
    assert live.Timeline.of([2.008, 1.993]).target == 2
    assert live.Timeline.of([1.001] * 3).target == 1          # rounds to nearest, like the tag is defined
    assert live.Timeline.of([0.04]).target == 1


def test_nothing_before_the_first_segment_ends():
    tl = live.Timeline.of([1.0, 1.0])
    assert live.live_window(tl, 2.0, EPOCH, EPOCH - 5, 6) == []
    assert live.live_window(tl, 2.0, EPOCH, EPOCH + 0.5, 6) == []
    assert [s.seq for s in live.live_window(tl, 2.0, EPOCH, EPOCH + 1.0, 6)] == [0]
    assert "#EXT-X-MEDIA-SEQUENCE:0" in live.render_live([], 1)


def test_a_segment_is_listed_exactly_when_it_ends():
    tl = live.Timeline.of([1.0, 1.0, 1.0, 0.5])
    period = 3.6                                              # the recording is 0.1 s longer than its video
    first_seen = {}
    t = EPOCH + 2 * period - 1
    while t < EPOCH + 6 * period:
        w = live.live_window(tl, period, EPOCH, t, 5)
        assert [s.seq for s in w] == list(range(w[0].seq, w[0].seq + len(w))), "consecutive, no gaps"
        assert len(w) == 5
        for s in w:
            assert s.pdt + s.dur <= t + 1e-3, "nothing from the future"
            first_seen.setdefault(s.seq, t)
        t += 0.01
    seqs = sorted(first_seen)
    assert seqs == list(range(seqs[0], seqs[-1] + 1)), "every segment was on the list once"
    for seq in seqs[5:]:                                      # the ones that appeared while we watched
        loop, idx = divmod(seq, tl.n)
        due = EPOCH + loop * period + tl.ends[idx]
        assert 0 <= first_seen[seq] - due < 0.011, f"segment {seq} listed {first_seen[seq] - due:+.3f}s off its end"


def test_sequence_and_discontinuity_numbers_never_depend_on_the_window():
    tl = live.Timeline.of([1.0, 1.0, 1.0])
    seen = {}
    t = EPOCH + 30
    while t < EPOCH + 50:
        segs = live.live_window(tl, 3.0, EPOCH, t, 4)
        for seq, disc, pdt, dur, uri in parse(live.render_live(segs, tl.target)):
            loop, idx = divmod(seq, tl.n)
            assert disc == loop, "the discontinuity number of a segment is its loop"
            assert uri == f"{idx:05d}.ts"
            assert abs(pdt - (EPOCH + loop * 3.0 + tl.starts[idx])) < 1e-3
            assert seen.setdefault(seq, (disc, pdt, dur, uri)) == (disc, pdt, dur, uri)
        t += 0.25
    assert len(seen) > 15


def test_discontinuity_tag_only_between_loops():
    tl = live.Timeline.of([1.0, 1.0, 1.0])
    text = live.render_live(live.live_window(tl, 3.0, EPOCH, EPOCH + 9.0, 3), 1)     # exactly one loop: 0,1,2
    assert "#EXT-X-DISCONTINUITY\n" not in text and "#EXT-X-DISCONTINUITY-SEQUENCE:2" in text
    text = live.render_live(live.live_window(tl, 3.0, EPOCH, EPOCH + 10.0, 3), 1)    # 1,2 of loop 2 + 0 of loop 3
    assert text.count("#EXT-X-DISCONTINUITY\n") == 1
    assert text.index("#EXT-X-DISCONTINUITY\n") > text.index("00002.ts")


def test_a_rendition_longer_than_the_period_stays_in_order():
    tl = live.Timeline.of([1.0, 1.0, 1.05])                   # 50 ms past the period
    last = -1
    t = EPOCH + 3
    while t < EPOCH + 20:
        w = live.live_window(tl, 3.0, EPOCH, t, 4)
        assert [s.seq for s in w] == list(range(w[0].seq, w[0].seq + len(w)))
        assert w[-1].seq >= last
        last = w[-1].seq
        t += 0.05


def test_position():
    assert live.position(10.0, EPOCH, EPOCH + 25.5) == (2, 5.5)
    assert live.position(10.0, EPOCH, EPOCH - 3) == (0, 0.0)


def test_iso():
    assert live.iso(1767225600.0) == "2026-01-01T00:00:00.000Z"
    assert live.iso(1767225600.9996) == "2026-01-01T00:00:01.000Z"
    assert live.iso(1767225601.25) == "2026-01-01T00:00:01.250Z"


def test_vod_and_master():
    tl = live.Timeline.of([1.0, 1.0, 0.5])
    vod = live.render_vod(tl, lambda i: f"{i:05d}.ts?v=x")
    assert vod.count("#EXTINF") == 3 and vod.rstrip().endswith("#EXT-X-ENDLIST") and "00002.ts?v=x" in vod
    m = live.render_master([
        {"name": "hi", "bandwidth": 5e6, "avg_bandwidth": 4e6, "width": 1920, "height": 1080, "fps": 30, "codecs": "avc1.640028,mp4a.40.2"},
        {"name": "lo", "bandwidth": 6e5, "avg_bandwidth": 5e5, "width": 640, "height": 360, "fps": 30, "codecs": ""},
    ], "live.m3u8")
    assert 'BANDWIDTH=5000000,AVERAGE-BANDWIDTH=4000000,RESOLUTION=1920x1080,FRAME-RATE=30.000,CODECS="avc1.640028,mp4a.40.2"\nhi/live.m3u8' in m
    assert "RESOLUTION=640x360,FRAME-RATE=30.000\nlo/live.m3u8" in m
