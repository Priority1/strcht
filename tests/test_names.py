"""This repository never names what the demo must not show (CONSOLE_HIDE, from the environment or ./.env)."""
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "data", "__pycache__", ".pytest_cache", "node_modules"}
LOCAL = {".env"}                      # local settings, not in git: the one place that has to name them


def hidden():
    raw = os.getenv("CONSOLE_HIDE") or dotenv_values(ROOT / ".env").get("CONSOLE_HIDE") or ""
    return [n.strip().lower() for n in raw.split(",") if n.strip()]


def files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p.relative_to(ROOT).as_posix() not in LOCAL:
                yield p


def test_no_file_names_them():
    names = hidden()
    if not names:
        pytest.skip("CONSOLE_HIDE is not set")
    found = []
    for p in files():
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(errors="ignore").lower()
        except OSError:
            continue
        for n in names:
            if n in rel.lower():
                found.append(f"{rel}: in the file name")
            for i, line in enumerate(text.splitlines(), 1):
                if n in line:
                    found.append(f"{rel}:{i}")
    assert not found, "named in: " + ", ".join(found[:20])
