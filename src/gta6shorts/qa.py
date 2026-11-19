"""Automatic quality control for every rendered video.

Technical: resolution, codecs, frame rate, duration, loudness, true peak,
black frames, frozen frames, dead air, hook timing, captions coverage.
Content:   the script is re-run through the fact checker (facts must be fresh).
A video with any failed *error* check can never be published.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .factcheck import check_with_config
from .facts import FactBase
from .script import Script


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    severity: str = "error"  # error | warning


@dataclass
class QAReport:
    video: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    def add(self, name: str, passed: bool, detail: str, severity: str = "error") -> None:
        self.checks.append(Check(name, bool(passed), detail, severity))

    def to_dict(self) -> dict:
        return {"video": self.video, "passed": self.passed, "checks": [asdict(c) for c in self.checks]}

    def summary(self) -> str:
        lines = [f"QA {'PASSED' if self.passed else 'FAILED'} — {sum(c.passed for c in self.checks)}/{len(self.checks)} checks ok"]
        for c in self.checks:
            mark = "✅" if c.passed else ("❌" if c.severity == "error" else "⚠️")
            lines.append(f"{mark} {c.name}: {c.detail}")
        return "\n".join(lines)


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr[-300:]}")
    return json.loads(out.stdout)


def analyze(path: Path) -> str:
    """One FFmpeg pass that runs all detectors; returns its log for parsing."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-vf", "scale=270:480,blackdetect=d=0.4:pix_th=0.06,freezedetect=n=0.001:d=2.5",
         "-af", "ebur128=peak=true,silencedetect=noise=-45dB:d=0.8",
         "-f", "null", "-"],
        capture_output=True, text=True)
    return proc.stderr


def parse_loudness(log: str) -> tuple[float | None, float | None]:
    summary = log[log.rfind("Summary:"):] if "Summary:" in log else log
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)

    def num(m):
        if not m:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))

    return num(i), num(tp)


def parse_black(log: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]


def parse_freeze(log: str) -> list[float]:
    return [float(d) for d in re.findall(r"freeze_duration: ([\d.]+)", log)]


def parse_silence(log: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return list(zip(starts, ends + [float("inf")] * (len(starts) - len(ends))))


def run_qa(video: Path, script: Script, fb: FactBase, cfg, *, today: date, history=(), strict_facts: bool = True) -> QAReport:
    report = QAReport(str(video))
    if not video.exists():
        report.add("file", False, "video file is missing")
        return report
    size_mb = video.stat().st_size / 1e6
    report.add("file size", 0.3 < size_mb < 287, f"{size_mb:.1f} MB")

    info = ffprobe(video)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    W, H = int(cfg.get("production.width", 1080)), int(cfg.get("production.height", 1920))
    fps_target = int(cfg.get("production.fps", 30))
    if v:
        num, den = (int(x) for x in v.get("r_frame_rate", "0/1").split("/"))
        fps = num / den if den else 0
        report.add("resolution", (v["width"], v["height"]) == (W, H), f"{v['width']}x{v['height']} (vertical 9:16)")
        report.add("video codec", v["codec_name"] == "h264" and v.get("pix_fmt") == "yuv420p",
                   f"{v['codec_name']} {v.get('pix_fmt')}")
        report.add("frame rate", abs(fps - fps_target) < 0.01, f"{fps:g} fps")
    else:
        report.add("video stream", False, "no video stream")
    if a:
        report.add("audio", a["codec_name"] == "aac" and int(a.get("sample_rate", 0)) == 48000 and a.get("channels") == 2,
                   f"{a['codec_name']} {a.get('sample_rate')} Hz {a.get('channels')} ch")
    else:
        report.add("audio stream", False, "no audio stream")

    duration = float(info["format"].get("duration", 0))
    lo, hi = float(cfg.get("production.min_seconds", 20)), float(cfg.get("production.max_seconds", 60))
    report.add("duration", lo <= duration <= hi, f"{duration:.1f}s (allowed {lo:.0f}-{hi:.0f}s)")

    log = analyze(video)
    loud, peak = parse_loudness(log)
    target = float(cfg.get("quality.loudness_target", -14.0))
    tol = float(cfg.get("quality.loudness_tolerance", 1.5))
    tp_max = float(cfg.get("quality.true_peak_max", -1.0))
    report.add("loudness", loud is not None and abs(loud - target) <= tol,
               f"{loud} LUFS (target {target}±{tol})")
    report.add("true peak", peak is not None and peak <= tp_max + 0.3, f"{peak} dBTP (max {tp_max})")
    blacks = parse_black(log)
    report.add("no black frames", not blacks, "none" if not blacks else f"black at {blacks}")
    freezes = parse_freeze(log)
    report.add("no frozen picture", not freezes, "none" if not freezes else f"frozen for {freezes}s", "warning")
    max_sil = float(cfg.get("quality.max_silence_seconds", 1.2))
    long_sil = [(s, e) for s, e in parse_silence(log) if min(e, duration) - s > max_sil and s < duration - 1.5]
    report.add("no dead air", not long_sil, "none" if not long_sil else f"silence at {long_sil}")

    words_file = video.parent / "words.json"
    if words_file.exists():
        words = json.loads(words_file.read_text(encoding="utf-8"))["words"]
        first = words[0]["start"] if words else 99
        hook_max = float(cfg.get("quality.hook_max_start_seconds", 0.6))
        report.add("hook starts immediately", first <= hook_max, f"first word at {first:.2f}s (max {hook_max}s)")
        report.add("captions cover every word", len(words) == script.word_count,
                   f"{len(words)} caption words for {script.word_count} script words")
        ordered = all(w["end"] > w["start"] for w in words) and all(
            x["start"] <= y["start"] for x, y in zip(words, words[1:]))
        report.add("caption timing", ordered, "monotonic" if ordered else "out-of-order word timings")
        last_end = words[-1]["end"] if words else 0
        report.add("ending", duration - last_end < 2.5, f"{duration - last_end:.1f}s after the last word", "warning")
    else:
        report.add("captions", False, "words.json missing")

    issues = check_with_config(script, fb, cfg, today=today, history=history, strict_freshness=strict_facts)
    errs = [i for i in issues if i.level == "error"]
    report.add("fact check", not errs, "all claims sourced, attributed and fresh" if not errs else
               "; ".join(str(e) for e in errs[:4]))
    warns = [i for i in issues if i.level == "warning"]
    if warns:
        report.add("script warnings", False, "; ".join(str(w) for w in warns[:4]), "warning")
    return report
