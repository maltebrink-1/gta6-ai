"""Word timing for captions without a speech-recognition model.

The TTS model gives us audio for each script line but no word timestamps, so
we estimate them:
  1. find the pauses in the audio (low-energy runs),
  2. anchor punctuation (commas, full stops …) to the nearest real pause,
  3. spread the words between anchors in proportion to their spoken length.
This keeps captions within a few frames of the voice for 1–3 word captions.
"""
from __future__ import annotations

import numpy as np

PUNCT_PAUSE = (",", ".", ";", ":", "!", "?", "—", "–")


def frame_rms(audio: np.ndarray, sr: int, frame_s: float = 0.01) -> np.ndarray:
    hop = max(1, int(sr * frame_s))
    n = len(audio) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = audio[: n * hop].reshape(n, hop).astype(np.float64)
    return np.sqrt((frames**2).mean(axis=1))


def speech_bounds(audio: np.ndarray, sr: int, frame_s: float = 0.01, rel: float = 0.05) -> tuple[float, float]:
    rms = frame_rms(audio, sr, frame_s)
    if rms.size == 0 or rms.max() <= 0:
        return 0.0, len(audio) / sr
    thr = max(rel * np.percentile(rms, 95), 1e-4)
    voiced = np.nonzero(rms > thr)[0]
    if voiced.size == 0:
        return 0.0, len(audio) / sr
    return voiced[0] * frame_s, (voiced[-1] + 1) * frame_s


def detect_pauses(
    audio: np.ndarray, sr: int, min_pause: float = 0.05, frame_s: float = 0.01, rel: float = 0.06
) -> list[tuple[float, float]]:
    """Silent runs strictly inside the speech region, as (start, end) seconds."""
    rms = frame_rms(audio, sr, frame_s)
    if rms.size == 0:
        return []
    thr = max(rel * np.percentile(rms, 95), 1e-4)
    silent = rms <= thr
    t0, t1 = speech_bounds(audio, sr, frame_s)
    pauses: list[tuple[float, float]] = []
    i, n = 0, len(silent)
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            start, end = i * frame_s, j * frame_s
            if end - start >= min_pause and start > t0 and end < t1:
                pauses.append((start, end))
            i = j
        else:
            i += 1
    return pauses


def tighten_pauses(audio: np.ndarray, sr: int, max_pause: float = 0.22) -> np.ndarray:
    """Shorten internal pauses longer than ``max_pause`` (a 'jump-cut' for snappier delivery)."""
    pauses = detect_pauses(audio, sr, min_pause=max_pause + 0.02)
    if not pauses:
        return audio
    keep: list[np.ndarray] = []
    cursor = 0
    for a, b in pauses:
        cut_from = int((a + max_pause / 2) * sr)
        cut_to = int((b - max_pause / 2) * sr)
        if cut_to > cut_from > cursor:
            keep.append(audio[cursor:cut_from])
            cursor = cut_to
    keep.append(audio[cursor:])
    return np.concatenate(keep)


def _spread(weights: list[float], t0: float, t1: float, pauses: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Distribute words over [t0, t1] proportionally, skipping over pauses."""
    if not weights:
        return []
    inner = [(max(a, t0), min(b, t1)) for a, b in pauses if b > t0 and a < t1]
    speech = max(1e-6, (t1 - t0) - sum(b - a for a, b in inner))
    total = sum(weights) or 1.0

    def to_real(speech_t: float) -> float:
        real = t0 + speech_t
        for a, b in inner:
            if real >= a:
                real += b - a
        return min(real, t1)

    out, acc = [], 0.0
    for w in weights:
        s = to_real(acc / total * speech)
        acc += w
        e = to_real(acc / total * speech)
        out.append((s, max(e, s + 0.04)))
    return out


def align_words(
    tokens: list[str],
    weights: list[float],
    audio: np.ndarray,
    sr: int,
    tolerance: float = 0.45,
) -> list[tuple[float, float]]:
    """Estimate (start, end) seconds for each token of one spoken line."""
    if not tokens:
        return []
    t0, t1 = speech_bounds(audio, sr)
    pauses = detect_pauses(audio, sr)
    estimate = _spread(weights, t0, t1, [])
    boundaries = [i for i, tok in enumerate(tokens[:-1]) if tok.rstrip("\"')”’").endswith(PUNCT_PAUSE)]

    anchors: list[tuple[int, tuple[float, float]]] = []  # (token index, pause)
    used: set[int] = set()
    last_time = t0
    for idx in boundaries:
        guess = estimate[idx][1]
        best, best_d = None, tolerance
        for k, (a, b) in enumerate(pauses):
            if k in used or a < last_time:
                continue
            d = abs((a + b) / 2 - guess)
            if d < best_d:
                best, best_d = k, d
        if best is not None:
            used.add(best)
            anchors.append((idx, pauses[best]))
            last_time = pauses[best][1]

    result: list[tuple[float, float]] = []
    seg_start_idx, seg_t0 = 0, t0
    for idx, (pa, pb) in anchors + [(len(tokens) - 1, (t1, t1))]:
        seg_weights = weights[seg_start_idx : idx + 1]
        free = [p for k, p in enumerate(pauses) if k not in used and seg_t0 < p[0] < pa]
        result.extend(_spread(seg_weights, seg_t0, pa, free))
        seg_start_idx, seg_t0 = idx + 1, pb
    return result[: len(tokens)]
