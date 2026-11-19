from __future__ import annotations

import numpy as np
import pytest

from gta6shorts.align import align_words, detect_pauses, tighten_pauses
from gta6shorts.captions import CaptionBuilder, build_srt, caption_word, rounded_rect, ts
from gta6shorts.music import build_sfx_track, load_library, synth_track
from gta6shorts.render import plan_timeline
from gta6shorts.styles import ass_color, pick_theme, safe_text
from gta6shorts.textutil import tts_normalize
from gta6shorts.tts import Word
from gta6shorts.visuals import STYLES, make_style

SR = 24000


def bursts(spec):
    """Synthetic 'speech': list of (seconds, is_voice)."""
    parts = []
    rng = np.random.default_rng(0)
    for dur, voiced in spec:
        n = int(dur * SR)
        parts.append(rng.standard_normal(n).astype(np.float32) * 0.3 if voiced else np.zeros(n, np.float32))
    return np.concatenate(parts)


def test_detect_pauses_finds_internal_silence_only():
    audio = bursts([(0.2, False), (1.0, True), (0.3, False), (1.0, True), (0.2, False)])
    pauses = detect_pauses(audio, SR)
    assert len(pauses) == 1
    a, b = pauses[0]
    assert 1.15 < a < 1.25 and 1.45 < b < 1.55


def test_alignment_snaps_comma_to_pause():
    audio = bursts([(1.0, True), (0.3, False), (1.0, True)])
    tokens = ["first", "part,", "second", "part"]
    times = align_words(tokens, [1, 1, 1, 1], audio, SR)
    assert len(times) == 4
    assert times[1][1] == pytest.approx(1.0, abs=0.05)  # "part," ends at the pause
    assert times[2][0] == pytest.approx(1.3, abs=0.05)  # "second" starts after it
    assert all(e > s for s, e in times)


def test_tighten_pauses_shortens_long_gaps():
    audio = bursts([(0.5, True), (0.8, False), (0.5, True)])
    out = tighten_pauses(audio, SR, max_pause=0.2)
    assert len(out) / SR == pytest.approx(1.2, abs=0.05)


def test_tts_normalize():
    assert tts_normalize("GTA VI on Xbox Series X|S & PS5") == "GTA 6 on Xbox Series X and S and PS 5"


def test_plan_timeline_math():
    plan = plan_timeline([0.05, 3.0, 6.5, 9.0], 12.0, 0.3)
    assert plan[0][0] == 0.0 and plan[-1][1] == 12.0
    total = plan[0][2]  # simulate the xfade chain length
    for start, end, clip, offset in plan[1:]:
        assert offset <= total - 0.3 + 1e-6, "xfade offset must leave room for the transition"
        total = offset + clip
    assert total == pytest.approx(12.0, abs=1e-3)


def test_plan_timeline_merges_tiny_segments():
    plan = plan_timeline([0.0, 2.0, 2.5, 6.0], 8.0, 0.3)
    starts = [p[0] for p in plan]
    assert all(b - a >= 1.0 for a, b in zip(starts, starts[1:]))


def test_ass_helpers():
    assert ts(3723.456) == "1:02:03.46"
    assert ass_color("#ff8000") == "&H000080FF"
    assert rounded_rect(100, 50, 10).startswith("m 10 0")
    assert caption_word('"record."', True) == "RECORD"


def test_caption_chunks_and_events(cfg):
    theme = pick_theme(cfg.styles, "x", "sunset_drive")
    b = CaptionBuilder(theme, cfg.fonts_dir, cfg)
    words = [Word(w, i * 0.3, i * 0.3 + 0.25, 0) for i, w in enumerate("GTA 6 is finally out, and it is huge.".split())]
    chunks = b.chunk(words)
    assert all(len(c) <= 3 for c in chunks)
    assert [w.text for w in chunks[1]][-1] == "out,"  # breaks after punctuation
    b.captions(words)
    ass = b.render()
    assert "Style: Caption,Anton" in ass
    assert ass.count("Dialogue:") == len(words)
    assert "FINALLY" in ass


def test_safe_text_replaces_missing_glyphs(cfg):
    assert safe_text(cfg.fonts_dir, "Bebas Neue", "2013 → 2015") == "2013 - 2015"
    assert safe_text(cfg.fonts_dir, "Anton", "2013 → 2015") == "2013 → 2015"


def test_srt():
    words = [Word("Hello", 0.0, 0.4, 0), Word("world.", 0.5, 0.9, 0), Word("Next", 2.0, 2.4, 1)]
    srt = build_srt(words)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:00,900\nHello world.")
    assert "2\n00:00:02,000" in srt


def test_theme_never_repeats_previous(cfg):
    for sid in ("a", "b", "c", "d", "e", "f"):
        first = pick_theme(cfg.styles, sid)
        again = pick_theme(cfg.styles, sid, previous=first.name)
        assert again.name != first.name


@pytest.mark.parametrize("name", sorted(STYLES))
def test_background_styles_render(cfg, name):
    palette = cfg.styles["themes"]["neon_night"]["palette"]
    style = make_style(name, palette, seed=1, w=108, h=192)
    a, b = style.frame(0.0), style.frame(0.5)
    assert a.shape == (192, 108, 3)
    assert np.isfinite(a).all()
    assert not np.allclose(a, b), "backgrounds must move"


def test_music_is_deterministic_and_safe():
    a, info = synth_track(6.0, seed=3)
    b, _ = synth_track(6.0, seed=3)
    c, _ = synth_track(6.0, seed=4)
    assert a.shape == (6 * 48000, 2)
    assert np.array_equal(a, b) and not np.array_equal(a[:48000], c[:48000])
    assert np.abs(a).max() <= 0.9 and np.isfinite(a).all()
    assert 90 <= info.bpm <= 120


def test_sfx_track_shape():
    fx = build_sfx_track(5.0, impacts=[0.0], whooshes=[1.0, 4.9], pops=[2.0])
    assert fx.shape == (5 * 48000, 2) and np.abs(fx).max() > 0


def test_music_library_requires_licence(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"x")
    (tmp_path / "licenses.yaml").write_text(
        "tracks:\n"
        "  - {file: song.mp3, title: Song, artist: A, license: CC-BY-4.0, source_url: 'https://x'}\n"
        "  - {file: song.mp3, title: NoLicense, artist: B}\n"
        "  - {file: missing.mp3, title: Gone, artist: C, license: CC0, source_url: 'https://y'}\n")
    tracks = load_library(tmp_path / "licenses.yaml")
    assert [t["title"] for t in tracks] == ["Song"]
