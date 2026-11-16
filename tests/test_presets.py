import json
from pathlib import Path

import presets

ROOT = Path(__file__).resolve().parent.parent


def test_delta2_only_for_its_sources():
    assert presets.clean("a", {"source": "en", "model": "delta2"})["translationModel"] == "delta2"
    assert presets.clean("a", {"source": "ja", "model": "delta2"})["translationModel"] == "auto"
    assert presets.clean("a", {"source": "auto", "model": "delta2", "detectable": ["en", "es"]}) == {
        "sourceLanguage": "auto", "detectableLanguages": ["en", "es"], "translationModel": "auto"}


def test_model_left_out_is_the_one_that_fits():
    assert presets.clean("a", {"source": "fr"})["translationModel"] == "delta2"
    assert presets.clean("a", {"source": "ja"})["translationModel"] == "auto"
    assert presets.clean("a", {})["sourceLanguage"] == "auto"


def test_detectable_only_with_auto_and_bad_codes_dropped():
    assert presets.clean("a", {"source": "en", "detectable": ["de"]})["detectableLanguages"] == []
    assert presets.clean("a", {"source": "auto", "detectable": ["en", "not a code", 3]})["detectableLanguages"] == ["en"]
    assert presets.clean("a", {"source": "English!"})["sourceLanguage"] == "auto"


def test_load(tmp_path):
    assert presets.load(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{")
    assert presets.load(tmp_path / "bad.json") == {}
    (tmp_path / "p.json").write_text('{"_about": ["x"], "A": {"source": "ru"}, "B": "nonsense"}')
    assert presets.load(tmp_path / "p.json") == {"A": {"sourceLanguage": "ru", "detectableLanguages": [], "translationModel": "delta2"}}


def test_shipped_presets_are_as_written():
    """presets.json says what it means: nothing in it is corrected on load."""
    raw = {k: v for k, v in json.loads((ROOT / "presets.json").read_text()).items() if not k.startswith("_")}
    loaded = presets.load(ROOT / "presets.json")
    assert set(loaded) == set(raw)
    for name, p in raw.items():
        assert loaded[name] == {"sourceLanguage": p["source"], "detectableLanguages": p.get("detectable", []),
                                "translationModel": p["model"]}, name
