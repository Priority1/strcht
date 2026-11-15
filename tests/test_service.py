"""The whole service on two generated clips: build, beacon, playlists, segments, thumbnails."""
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_live import parse  # noqa: E402

from app import create_app  # noqa: E402
from config import Config  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def clip(path, seconds, size="640x360", rate=30):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
         "-g", str(rate * 2), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", str(path)], check=True)
    old = time.time() - 60
    import os
    os.utime(path, (old, old))                    # the scanner leaves files alone that were written a moment ago


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    clip(d / "Alpha_One.mp4", 6)
    clip(d / "beta two.mp4", 25, size="360x480")
    (d / "description.txt").write_text("Alpha_One:\n- Native german speech\n- Not native english speech\n- duration: 6s\n")
    (d / "notes.txt").write_text("not a video")
    return d


def wait_built(c, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get("/api/streams").json()
        if d["count"] and not d["building"]:
            return d
        time.sleep(0.2)
    raise AssertionError(f"still building: {d}")


@pytest.fixture(scope="module", params=["encode", "copy"])
def client(request, media, tmp_path_factory):
    cfg = Config(media_dir=media, data_dir=tmp_path_factory.mktemp("data"), hi_mode=request.param, workers=2)
    with TestClient(create_app(cfg)) as c:
        c.cfg = cfg
        wait_built(c)
        yield c


def test_beacon(client):
    r = client.get("/pinhole/list")
    assert r.status_code == 200
    recs = r.json()
    assert [x["streamId"] for x in recs] == ["alpha_one", "beta_two"]
    a = recs[0]
    assert a["userName"] == "Alpha_One"
    assert a["masterUrl"] == "http://testserver/hls/alpha_one/master.m3u8"
    assert a["previewUrl"] == "http://testserver/hls/alpha_one/lo/live.m3u8"
    assert a["ipAddress"] == "testserver" and a["port"] == 80
    assert a["languages"] == ["de", "en"] and a["notes"] == ["Native german speech", "Not native english speech"]
    assert 5.9 < a["durationSeconds"] < 6.2 and 0 <= a["positionSeconds"] < a["durationSeconds"]
    assert recs[1]["languages"] == []


def test_beacon_token(media, client):
    cfg = Config(media_dir=media, data_dir=client.cfg.data_dir, hi_mode=client.cfg.hi_mode, pinhole_token="s3cret")
    with TestClient(create_app(cfg)) as c:
        assert c.get("/pinhole/list").status_code == 401
        assert c.get("/pinhole/list", headers={"authorization": "bearer nope"}).status_code == 401
        r = c.get("/pinhole/list", headers={"authorization": "bearer s3cret"})      # as the console sends it
        assert r.status_code == 200 and len(r.json()) == 2
        assert c.get("/api/streams").json()["building"] == 0, "built once: a second start finds everything in place"


def test_master(client):
    r = client.get("/hls/alpha_one/master.m3u8")
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    inf = re.findall(r"#EXT-X-STREAM-INF:BANDWIDTH=(\d+).*?RESOLUTION=(\d+)x(\d+).*\n(\S+)", r.text)
    assert [x[3] for x in inf] == ["hi/live.m3u8", "lo/live.m3u8"]                  # best first
    assert int(inf[0][0]) > int(inf[1][0])
    assert (inf[0][1], inf[0][2]) == ("640", "360")
    assert 'CODECS="avc1.' in r.text and "mp4a.40.2" in r.text
    assert "hi/vod.m3u8" in client.get("/hls/alpha_one/vod.m3u8").text


def test_live_playlist(client):
    now = time.time()
    r = client.get("/hls/alpha_one/lo/live.m3u8")
    assert "no-cache" in r.headers["cache-control"]
    assert "#EXT-X-TARGETDURATION:1\n" in r.text
    segs = parse(r.text)
    assert len(segs) == 12                                                          # the 6 s clip twice: the window spans loops
    assert r.text.count("#EXT-X-DISCONTINUITY\n") in (1, 2)
    assert all(abs(d - 1.0) < 1e-6 for _, _, _, d, _ in segs)
    assert all(pdt + dur <= now + 0.5 for _, _, pdt, dur, _ in segs)
    assert now - (segs[-1][2] + segs[-1][3]) < 1.5, "the newest segment ended less than a segment ago"
    pdts = [s[2] for s in segs]
    assert pdts == sorted(pdts)


def test_hi_cut(client):
    durs = [d for _, _, _, d, _ in parse(client.get("/hls/beta_two/hi/live.m3u8").text)]
    if client.cfg.hi_mode == "encode":
        assert all(abs(d - 1.0) < 1e-6 for d in durs)
        assert "#EXT-X-TARGETDURATION:1\n" in client.get("/hls/beta_two/hi/live.m3u8").text
    else:
        assert all(abs(d - 2.0) < 0.05 for d in durs if d > 1.5) and max(durs) > 1.9   # the source's own GOPs


def test_segments(client):
    text = client.get("/hls/alpha_one/hi/live.m3u8").text
    uri = parse(text)[-1][4]
    assert re.fullmatch(r"\d{5}\.ts\?v=[0-9a-f]{8}", uri)
    r = client.get(f"/hls/alpha_one/hi/{uri}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp2t"
    assert r.content[0] == 0x47 and len(r.content) % 188 == 0                       # MPEG-TS packets
    assert "immutable" in r.headers["cache-control"]
    assert client.get("/hls/alpha_one/hi/00999.ts").status_code == 404
    assert client.get("/hls/alpha_one/zz/00000.ts").status_code == 404
    assert client.get("/hls/nobody/master.m3u8").status_code == 404
    assert client.get("/hls/alpha_one/hi/..%2F..%2Fmeta.json").status_code == 404


def test_every_segment_decodes_with_audio(client):
    """What the console's tap does with a segment: demux MPEG-TS, find AAC."""
    text = client.get("/hls/beta_two/lo/vod.m3u8").text
    assert text.rstrip().endswith("#EXT-X-ENDLIST")
    uris = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert len(uris) == 25
    data = client.get(f"/hls/beta_two/lo/{uris[7]}").content
    out = subprocess.run(["ffprobe", "-v", "error", "-f", "mpegts", "-show_entries", "stream=codec_name", "-of", "csv=p=0", "-"],
                         input=data, capture_output=True).stdout.decode().split()
    assert "h264" in out and "aac" in out


def test_thumbnails(client):
    for path in ("/thumbs/beta_two/live.jpg", "/thumbs/beta_two/poster.jpg", "/thumbs/beta_two/00001.jpg"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8", path
    sb = client.get("/thumbs/beta_two/storyboard.json").json()
    assert [f["n"] for f in sb["frames"]] == [1, 2, 3]
    assert [round(f["t"]) for f in sb["frames"]] == [0, 10, 20]
    assert sb["frames"][1]["url"] == "http://testserver/thumbs/beta_two/00002.jpg"
    vtt = client.get("/thumbs/beta_two/storyboard.vtt").text
    assert vtt.startswith("WEBVTT") and "00:00:10.000 --> 00:00:20.000\nhttp://testserver/thumbs/beta_two/00002.jpg" in vtt
    assert client.get("/thumbs/beta_two/00009.jpg").status_code == 404


def test_api_and_cors(client):
    d = client.get("/api/streams", headers={"origin": "http://localhost:8787"})
    assert d.headers["access-control-allow-origin"] == "*"
    d = d.json()
    assert d["onAir"] == 2 and d["pinholeUrl"] == "http://testserver/pinhole/list"
    s = d["streams"][1]
    assert s["id"] == "beta_two" and s["file"] == "beta two.mp4" and s["jobs"] == {}
    assert [r["name"] for r in s["renditions"]] == ["hi", "lo"]
    assert (s["renditions"][1]["width"], s["renditions"][1]["height"]) == (270, 360)
    assert s["renditions"][0]["mode"] == client.cfg.hi_mode
    assert client.get("/hls/beta_two/lo/live.m3u8", headers={"origin": "http://x"}).headers["access-control-allow-origin"] == "*"
    assert client.get("/healthz").text == "ok"


def test_files_come_and_go(client, media):
    lib = client.app.state.lib
    clip(media / "Gamma.mp4", 3)
    try:
        lib.scan()
        assert "gamma" in lib.streams
        wait_built(client)
        ids = [x["streamId"] for x in client.get("/pinhole/list").json()]
        assert ids == ["alpha_one", "beta_two", "gamma"]
        assert len(parse(client.get("/hls/gamma/lo/live.m3u8").text)) == 12
    finally:
        (media / "Gamma.mp4").unlink()
    lib.scan()
    assert [x["streamId"] for x in client.get("/pinhole/list").json()] == ["alpha_one", "beta_two"]
    assert client.get("/hls/gamma/master.m3u8").status_code == 404


def test_a_player_follows_the_stream_across_a_loop(client):
    """Poll like a live client for longer than the 6 s clip: gapless sequence, every segment fetchable once."""
    seen, t0 = {}, time.time()
    while time.time() - t0 < 8:
        for seq, disc, pdt, dur, uri in parse(client.get("/hls/alpha_one/lo/live.m3u8").text):
            assert seen.setdefault(seq, (disc, round(pdt, 3), uri)) == (disc, round(pdt, 3), uri)
        time.sleep(0.25)
    seqs = sorted(seen)
    assert seqs == list(range(seqs[0], seqs[-1] + 1))
    assert len({d for d, _, _ in seen.values()}) >= 3, "the clip looped while we watched"
    assert 19 <= len(seqs) <= 21                    # 12 at the start + one per second
