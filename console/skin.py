"""The translation console, run from its own checkout, shown without the names it was written for.

The console is another project. Nothing of it is copied or changed here: this module imports the console's
ASGI app and puts one layer around it. For every name in SKIN_HIDE (comma separated, any case):

  - pages, scripts and styles are rewritten on their way out — the name becomes a neutral word (`neutral`);
  - JSON files lose the options that are that name (`prune`): a model, a voice … the viewer could pick;
  - the sign-in prompt (`WWW-Authenticate` realm) and whatever the console prints get the same treatment.

API responses and WebSocket frames pass through untouched: they are data, not wording.

    cd <console checkout>
    PYTHONPATH=<this directory> SKIN_HIDE=<name>[,<name>…] python -m uvicorn skin:create --factory
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

TEXT_TYPES = {"text/html", "text/css", "text/javascript", "application/javascript", "application/json", "text/plain"}
UNCONDITIONAL = (b"if-none-match", b"if-modified-since", b"range")     # always the whole file: it is rewritten
STALE = (b"content-length", b"etag", b"last-modified")                  # … so these describe another body


def names_of(text: str | None) -> list[str]:
    return [n.strip() for n in (text or "").split(",") if n.strip()]


def neutral(text: str, names: list[str]) -> str:
    """Replace each name by what it stands for in the console: where the streams come from.

    `Name's` → `the source's` · `Name` → `the source` · `NAME` → `LIVE` · `name` (and any other case) → `live`
    """
    for name in names:
        low = name.lower()
        cap, up = low.capitalize(), low.upper()
        for apostrophe in ("'", "’"):
            text = text.replace(f"{cap}{apostrophe}s", f"the source{apostrophe}s")
        text = text.replace(cap, "the source").replace(up, "LIVE")
        text = re.sub(re.escape(low), "live", text, flags=re.IGNORECASE)
    return text


def prune(value, names: list[str]):
    """The same JSON without list items and keys that are one of the names."""
    hidden = {n.lower() for n in names}
    if isinstance(value, list):
        return [prune(v, names) for v in value if not (isinstance(v, str) and v.lower() in hidden)]
    if isinstance(value, dict):
        return {k: prune(v, names) for k, v in value.items() if k.lower() not in hidden}
    return value


def rewrite(body: bytes, content_type: str, names: list[str]) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    if content_type == "application/json":
        try:
            text = json.dumps(prune(json.loads(text), names), ensure_ascii=False)
        except ValueError:
            pass
    return neutral(text, names).encode("utf-8")


class Skin:
    """ASGI layer: static text of the wrapped app goes out through `rewrite`."""

    def __init__(self, app, names: list[str]):
        self.app = app
        self.names = names

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.names:
            return await self.app(scope, receive, send)
        static = not scope["path"].startswith("/api/") and scope["method"] == "GET"
        if static:
            scope = dict(scope, headers=[(k, v) for k, v in scope["headers"] if k not in UNCONDITIONAL])
            scope["extensions"] = {k: v for k, v in (scope.get("extensions") or {}).items() if k != "http.response.pathsend"}
        held = {"start": None, "type": "", "chunks": []}

        async def out(msg):
            if msg["type"] == "http.response.start":
                headers = [(k, neutral(v.decode("latin-1"), self.names).encode("latin-1") if k == b"www-authenticate" else v)
                           for k, v in msg.get("headers", [])]
                msg = dict(msg, headers=headers)
                ctype = dict(headers).get(b"content-type", b"").decode("latin-1").split(";")[0].strip().lower()
                if static and ctype in TEXT_TYPES:
                    held["start"], held["type"] = msg, ctype      # sent with the body, once its length is known
                    return
            elif msg["type"] == "http.response.body" and held["start"] is not None:
                held["chunks"].append(msg.get("body", b""))
                if msg.get("more_body"):
                    return
                body = rewrite(b"".join(held["chunks"]), held["type"], self.names)
                start = held["start"]
                headers = [(k, v) for k, v in start["headers"] if k not in STALE] + [(b"content-length", str(len(body)).encode())]
                await send(dict(start, headers=headers))
                await send({"type": "http.response.body", "body": body})
                return
            await send(msg)

        await self.app(scope, receive, out)


class _Neutral:
    """A text stream that never prints the names (the console's own log lines, tracebacks)."""

    def __init__(self, stream, names):
        self._stream, self._names = stream, names

    def write(self, s):
        return self._stream.write(neutral(s, self._names) if isinstance(s, str) else s)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def create():
    """uvicorn factory. The working directory is the console's checkout; `app` is its app.py."""
    names = names_of(os.getenv("SKIN_HIDE"))
    if names:
        sys.stdout, sys.stderr = _Neutral(sys.stdout, names), _Neutral(sys.stderr, names)
        for lg in (logging.getLogger(), *(logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access"))):
            for h in lg.handlers:
                if isinstance(h, logging.StreamHandler):
                    h.setStream(_Neutral(h.stream, names))
    import app as console  # noqa: PLC0415  (after the streams are wrapped: it logs while it loads)

    return Skin(console.app, names)
