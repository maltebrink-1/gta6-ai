"""Narration: free neural TTS (Kokoro-82M, Apache-2.0) with an espeak-ng fallback.

The channel voice is a fixed *blend* of Kokoro's built-in voices, so it is an
original synthetic voice — it does not clone or imitate any real person.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .align import align_words, speech_bounds, tighten_pauses
from .script import Script
from .textutil import syllable_weight, tts_normalize

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MIN_MODEL_BYTES = {"kokoro-v1.0.onnx": 250_000_000, "kokoro-v1.0.int8.onnx": 60_000_000, "voices-v1.0.bin": 20_000_000}


class TTSError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    start: float
    end: float
    line: int


@dataclass
class Narration:
    audio: np.ndarray
    sample_rate: int
    words: list[Word]
    lines: list[tuple[float, float]]
    engine: str
    voice: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return len(self.audio) / self.sample_rate

    def save(self, wav_path: Path, json_path: Path) -> None:
        sf.write(str(wav_path), self.audio, self.sample_rate, subtype="PCM_16")
        json_path.write_text(json.dumps({
            "engine": self.engine, "voice": self.voice, "sample_rate": self.sample_rate,
            "duration": round(self.duration, 3), "lines": self.lines,
            "words": [asdict(w) for w in self.words],
        }, indent=1), encoding="utf-8")


# ── Engines ──────────────────────────────────────────────────────────────────
class KokoroEngine:
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, model_path: Path, voices_path: Path, blend: dict[str, float], speed: float = 1.1,
                 sentence_pause: float = 0.16):
        from kokoro_onnx import Kokoro  # optional dependency

        self.kokoro = Kokoro(str(model_path), str(voices_path))
        total = sum(blend.values()) or 1.0
        self.style = sum(self.kokoro.get_voice_style(v) * (w / total) for v, w in blend.items())
        self.voice_label = "+".join(f"{v}:{w:g}" for v, w in blend.items())
        self.speed = speed
        self.sentence_pause = sentence_pause

    def synth(self, text: str) -> np.ndarray:
        audio, sr = self.kokoro.create(
            text, voice=self.style, speed=self.speed, lang="en-us",
            sentence_pause=self.sentence_pause, clause_pause=0.05,
        )
        if sr != self.sample_rate:
            raise TTSError(f"unexpected Kokoro sample rate {sr}")
        return np.asarray(audio, dtype=np.float32)

    def weight(self, token: str) -> float:
        try:
            phonemes = self.kokoro.tokenizer.phonemize(token, "en-us")
            n = len([p for p in phonemes if p.isalpha() or p in "ˈˌːəɪʊæɑɔɛʌ"])
            return max(1.0, float(n)) if n else syllable_weight(token)
        except Exception:  # noqa: BLE001 - weighting is best-effort
            return syllable_weight(token)


class EspeakEngine:
    """Robotic but dependable fallback (used for tests and when models are missing)."""

    name = "espeak"
    sample_rate = 22050

    def __init__(self, voice: str = "en-us+m3", wpm: int = 180):
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.binary:
            raise TTSError("espeak-ng is not installed (apt-get install espeak-ng)")
        self.voice, self.wpm = voice, wpm
        self.voice_label = voice

    def synth(self, text: str) -> np.ndarray:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "line.wav"
            subprocess.run([self.binary, "-v", self.voice, "-s", str(self.wpm), "-w", str(out), text],
                           check=True, capture_output=True)
            audio, sr = sf.read(str(out), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        self.sample_rate = sr
        return audio

    def weight(self, token: str) -> float:
        return syllable_weight(token)


def ensure_kokoro_models(model_dir: Path, model_name: str, voices_name: str, download: bool = True) -> tuple[Path, Path]:
    model_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in (model_name, voices_name):
        path = model_dir / name
        if not path.exists() or path.stat().st_size < MIN_MODEL_BYTES.get(name, 1):
            if not download:
                raise TTSError(f"Kokoro file missing: {path}")
            tmp = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(KOKORO_RELEASE + name, tmp)  # noqa: S310 - fixed https URL
            if tmp.stat().st_size < MIN_MODEL_BYTES.get(name, 1):
                raise TTSError(f"download of {name} looks truncated")
            tmp.replace(path)
        paths.append(path)
    return paths[0], paths[1]


def make_engine(cfg, engine: str | None = None, preset: str | None = None, download: bool = True):
    choice = engine or cfg.get("voice.engine", "auto")
    preset_name = preset or cfg.get("voice.preset", "hype_female")
    preset_cfg = cfg.get(f"voice.presets.{preset_name}") or {}
    if choice in ("auto", "kokoro"):
        try:
            model, voices = ensure_kokoro_models(
                cfg.model_dir, cfg.get("voice.kokoro_model", "kokoro-v1.0.onnx"),
                cfg.get("voice.kokoro_voices", "voices-v1.0.bin"), download=download)
            return KokoroEngine(model, voices, preset_cfg.get("blend", {"af_heart": 1.0}),
                                float(preset_cfg.get("speed", 1.1)), float(cfg.get("voice.sentence_pause", 0.16)))
        except Exception as exc:  # noqa: BLE001
            if choice == "kokoro":
                raise TTSError(f"Kokoro unavailable: {exc}") from exc
    return EspeakEngine()


# ── Narration ────────────────────────────────────────────────────────────────
def display_tokens(text: str) -> list[str]:
    return [t for t in text.split() if t.strip()]


def _trim(audio: np.ndarray, sr: int, pad: float = 0.02) -> np.ndarray:
    t0, t1 = speech_bounds(audio, sr)
    a = max(0, int((t0 - pad) * sr))
    b = min(len(audio), int((t1 + pad) * sr))
    return audio[a:b] if b > a else audio


def narrate(script: Script, engine, line_gap: float = 0.10, lead_in: float = 0.05, max_pause: float = 0.22) -> Narration:
    sr = engine.sample_rate
    pieces: list[np.ndarray] = [np.zeros(int(lead_in * sr), dtype=np.float32)]
    cursor = lead_in
    words: list[Word] = []
    lines: list[tuple[float, float]] = []
    for idx, line in enumerate(script.lines):
        audio = _trim(engine.synth(tts_normalize(line.text)), engine.sample_rate)
        audio = tighten_pauses(audio, engine.sample_rate, max_pause)
        sr = engine.sample_rate
        tokens = display_tokens(line.text)
        weights = [engine.weight(tts_normalize(tok)) for tok in tokens]
        for tok, (s, e) in zip(tokens, align_words(tokens, weights, audio, sr)):
            words.append(Word(tok, round(cursor + s, 3), round(cursor + e, 3), idx))
        dur = len(audio) / sr
        lines.append((round(cursor, 3), round(cursor + dur, 3)))
        pieces.append(audio)
        gap = line_gap if idx < len(script.lines) - 1 else 0.0
        pieces.append(np.zeros(int(gap * sr), dtype=np.float32))
        cursor += dur + gap
    full = np.concatenate(pieces).astype(np.float32)
    peak = float(np.max(np.abs(full))) or 1.0
    full = full / peak * 0.89  # consistent headroom before mastering
    return Narration(full, sr, words, lines, engine.name, getattr(engine, "voice_label", ""))
