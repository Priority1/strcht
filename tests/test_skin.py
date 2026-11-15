"""console/skin.py: a console written for "Acme" must come out without that name."""
import json
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "console"))
import skin  # noqa: E402

NAMES = ["acme"]


def test_neutral():
    n = lambda s: skin.neutral(s, NAMES)  # noqa: E731
    assert n("<title>ACME ▸ PALABRA // stream console</title>") == "<title>LIVE ▸ PALABRA // stream console</title>"
    assert n("video   acme hls → this browser") == "video   live hls → this browser"
    assert n("straight from Acme's CDN, never through our server") == "straight from the source's CDN, never through our server"
    assert n("the offset of Acme’s encoder clock") == "the offset of the source’s encoder clock"
    assert n("One Acme stream ↔ one session") == "One the source stream ↔ one session"
    assert n("warning: ACME_TOKEN not set · aCmE") == "warning: LIVE_TOKEN not set · live"
    assert n("nothing to see") == "nothing to see"
    assert skin.neutral("Acme and Globex's, GLOBEX", ["acme", "Globex"]) == "the source and the source's, LIVE"
    assert skin.neutral("Acme", []) == "Acme"


def test_names_of():
    assert skin.names_of(" acme, Globex ,,") == ["acme", "Globex"]
    assert skin.names_of(None) == [] and skin.names_of("") == []


def test_prune_drops_the_options_that_are_a_name():
    rules = {"asr_models": ["auto", "alpha", "acme"], "tts": {"models": ["Acme", "beta"], "acme": {"x": 1}}, "n": 3, "s": "acme"}
    assert skin.prune(rules, NAMES) == {"asr_models": ["auto", "alpha"], "tts": {"models": ["beta"]}, "n": 3, "s": "acme"}
    out = json.loads(skin.rewrite(json.dumps(rules).encode(), "application/json", NAMES))
    assert out == {"asr_models": ["auto", "alpha"], "tts": {"models": ["beta"]}, "n": 3, "s": "live"}
    assert skin.rewrite(b"\xff\xfe binary", "text/plain", NAMES) == b"\xff\xfe binary"
    assert skin.rewrite(b"{not json: Acme", "application/json", NAMES) == b"{not json: the source"


def console(tmp_path):
    """A stand-in for the console: static pages, an API, a sign-in prompt, a WebSocket."""
    (tmp_path / "index.html").write_text("<title>ACME ▸ console</title><p>Acme's streams, acme hls</p>" + "x" * 200_000)
    (tmp_path / "app.js").write_text("// Acme\nconst a = 'acme'; // ▸\n")
    (tmp_path / "rules.json").write_text(json.dumps({"models": ["auto", "acme"]}))
    (tmp_path / "pic.bin").write_bytes(b"acme\x00\x01")
    app = FastAPI()

    @app.get("/api/streams")
    def streams():
        return JSONResponse({"streams": [{"userName": "Acme fan"}]})

    @app.get("/secret")
    def secret():
        return Response("Authentication required.\n", 401, {"WWW-Authenticate": 'Basic realm="acme-palabra console"'})

    @app.websocket("/api/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        await sock.send_text(await sock.receive_text())
        await sock.close()

    app.mount("/", StaticFiles(directory=str(tmp_path), html=True))
    return app


def test_pages_scripts_and_option_lists_come_out_without_the_name(tmp_path):
    c = TestClient(skin.Skin(console(tmp_path), NAMES))
    r = c.get("/")
    assert r.status_code == 200 and r.text.startswith("<title>LIVE ▸ console</title><p>the source's streams, live hls</p>")
    assert int(r.headers["content-length"]) == len(r.content) and "etag" not in r.headers
    assert c.get("/app.js").text == "// the source\nconst a = 'live'; // ▸\n"
    assert c.get("/rules.json").json() == {"models": ["auto"]}
    for path in ("/", "/index.html", "/app.js", "/rules.json"):
        assert "acme" not in c.get(path).text.lower(), path


def test_a_cached_copy_is_never_confirmed(tmp_path):
    plain = TestClient(console(tmp_path))
    etag = plain.get("/app.js").headers["etag"]
    assert plain.get("/app.js", headers={"if-none-match": etag}).status_code == 304        # what the console does itself
    c = TestClient(skin.Skin(console(tmp_path), NAMES))
    r = c.get("/app.js", headers={"if-none-match": etag, "range": "bytes=0-5"})
    assert r.status_code == 200 and r.text == "// the source\nconst a = 'live'; // ▸\n"


def test_data_passes_untouched(tmp_path):
    c = TestClient(skin.Skin(console(tmp_path), NAMES))
    assert c.get("/api/streams").json() == {"streams": [{"userName": "Acme fan"}]}         # a stream may be called anything
    assert c.get("/pic.bin").content == b"acme\x00\x01"                                    # not text: not ours to rewrite
    with c.websocket_connect("/api/ws") as ws:
        ws.send_text("Acme said hello")
        assert ws.receive_text() == "Acme said hello"                                      # what was said is what was said


def test_sign_in_prompt(tmp_path):
    r = TestClient(skin.Skin(console(tmp_path), NAMES)).get("/secret")
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Basic realm="live-palabra console"'
    assert r.text == "Authentication required.\n"


def test_no_names_no_skin(tmp_path):
    c = TestClient(skin.Skin(console(tmp_path), []))
    assert c.get("/app.js").text == "// Acme\nconst a = 'acme'; // ▸\n" and "etag" in c.get("/app.js").headers


def test_log_lines(capsys):
    out = skin._Neutral(sys.stdout, NAMES)
    print("12:00:00", "acme▸palabra console  http://localhost:8788", file=out, flush=True)
    assert capsys.readouterr().out == "12:00:00 live▸palabra console  http://localhost:8788\n"
