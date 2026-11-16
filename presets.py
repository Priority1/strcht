"""Translation presets: per video, the source language and the translation model the console should start with.

presets.json (PRESETS_FILE) → {video name: {"source", "model", "detectable"}}. A preset the console could not run
is corrected here, with a log line, rather than sent: Delta2 takes a fixed source from DELTA2_SOURCES only
(https://platform.palabra.ai/docs/speech-to-speech/recommended-settings-delta2), everything else gets `auto`.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

DELTA2_SOURCES = {"en", "de", "es", "fr", "it", "nl", "pt", "ru"}
MODELS = {"delta2", "auto"}
_CODE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,10})?$")


def _log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def clean(name: str, raw) -> dict | None:
    """One preset as the beacon carries it, or None when there is nothing usable."""
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "auto").strip().lower()
    if source != "auto" and not _CODE.match(source):
        _log(f"preset {name}: source {source!r} is not a language code; using auto")
        source = "auto"
    detectable = [str(x).strip().lower() for x in raw.get("detectable") or [] if _CODE.match(str(x).strip().lower())]
    model = str(raw.get("model") or "").strip().lower()
    fits = "delta2" if source in DELTA2_SOURCES else "auto"
    if model not in MODELS:
        model = fits
    elif model == "delta2" and fits != "delta2":
        _log(f"preset {name}: delta2 needs a fixed source from {sorted(DELTA2_SOURCES)}, not {source!r}; using auto")
        model = "auto"
    return {"sourceLanguage": source, "detectableLanguages": detectable if source == "auto" else [], "translationModel": model}


def load(path: Path) -> dict[str, dict]:
    """{video name: preset}; empty when the file is missing or broken (said once per load)."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _log(f"presets {path}: {e}")
        return {}
    if not isinstance(data, dict):
        _log(f"presets {path}: expected an object of video name → preset")
        return {}
    out = {}
    for name, raw in data.items():
        if name.startswith("_"):
            continue
        p = clean(name, raw)
        if p:
            out[name] = p
    return out
