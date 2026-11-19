"""Renders a real video with FFmpeg and runs QA on it (marked slow: ~1 minute)."""
from __future__ import annotations

import json
import shutil

import pytest

from conftest import TODAY
from gta6shorts.cli import main
from gta6shorts.facts import load_facts
from gta6shorts.qa import run_qa
from gta6shorts.render import render_script
from gta6shorts.script import Script
from gta6shorts.tts import EspeakEngine, make_engine

SCRIPT = {
    "id": "2026-11-19-e2e-test", "title": "End-to-end test video", "hook_text": "GTA 6 IS OUT", "format": "news",
    "lines": [
        {"text": "GTA 6 is finally out, and the reviews are wild.", "kind": "hook", "facts": ["gta6_release_date"]},
        {"text": "It launched with a 99 on Metacritic. That ties the highest score the site has ever given.",
         "kind": "claim", "facts": ["gta6_metacritic_99"], "card": {"type": "stat", "big": "99", "small": "METASCORE"}},
        {"text": "Right now it's on PlayStation 5 and Xbox Series X and S, and Rockstar still hasn't announced a PC release date.",
         "kind": "claim", "facts": ["gta6_platforms", "gta6_no_pc_date"]},
        {"text": "Are you starting as Lucia or Jason? Tell me below, and follow for daily GTA 6 news.", "kind": "cta",
         "facts": ["gta6_protagonists"]},
    ],
    "description": "test", "tags": ["GTA 6"],
}


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_and_qa(cfg, tmp_path):
    try:
        engine = make_engine(cfg, download=False)  # Kokoro if the model is cached locally …
    except Exception:  # noqa: BLE001
        engine = None
    if engine is None or engine.name != "kokoro":
        if not shutil.which("espeak-ng"):
            pytest.skip("no TTS engine available")
        engine = EspeakEngine()  # … otherwise the fallback voice
    cfg.data["production"]["min_seconds"] = 8  # short test clip
    cfg.data["production"]["x264_preset"] = "veryfast"
    fb = load_facts(cfg.facts_file)
    script = Script.from_dict(SCRIPT)
    result = render_script(script, fb, cfg, engine, tmp_path, today=TODAY, log=lambda *_: None)
    assert result.video.exists()
    for name in ("thumb.jpg", "contact_sheet.jpg", "captions.ass", "captions.srt", "metadata.json", "words.json"):
        assert (result.out_dir / name).exists(), name
    meta = json.loads((result.out_dir / "metadata.json").read_text())
    assert "AI-generated voice" in meta["description"] and "Sources:" in meta["description"]
    qa = run_qa(result.video, script, fb, cfg, today=TODAY)
    assert qa.passed, qa.summary()


def test_cli_validate_and_doctor(capsys):
    assert main(["validate"]) == 0
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "fact base" in out and "publishing master switch" in out
