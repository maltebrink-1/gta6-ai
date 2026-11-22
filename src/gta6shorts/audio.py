"""Audio mixing and mastering with FFmpeg.

voice  → high-pass, compression, presence EQ
music  → quieter, and automatically ducked whenever the voice speaks (side-chain)
sfx    → whooshes on transitions, impact on the hook, pops on cards
master → two-pass EBU R128 loudness normalisation (-14 LUFS, -1.5 dBTP)
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


class AudioError(RuntimeError):
    pass


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AudioError(f"command failed: {' '.join(cmd[:6])} …\n{proc.stderr[-1500:]}")
    return proc


def mix(voice: Path, music: Path | None, sfx: Path | None, out: Path, duration: float,
        music_db: float = -17.0, sfx_db: float = -8.0) -> Path:
    d = f"{duration:.3f}"
    inputs = ["-i", str(voice)]
    graph = [
        "[0:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=70,"
        "acompressor=threshold=-21dB:ratio=3:attack=5:release=90:makeup=2,"
        "equalizer=f=3000:t=q:w=1.1:g=2.5,equalizer=f=220:t=q:w=1.0:g=-1.5,"
        f"apad=whole_dur={d}[voice]",
    ]
    mix_inputs = ["[v1]"]
    graph.append("[voice]asplit=2[v1][vsc]")
    idx = 1
    if music is not None:
        inputs += ["-i", str(music)]
        graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={music_db}dB,apad=whole_dur={d}[mus]")
        graph.append("[mus][vsc]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=400:makeup=1[duck]")
        mix_inputs.append("[duck]")
        idx += 1
    else:
        graph.append("[vsc]anullsink")
    if sfx is not None:
        inputs += ["-i", str(sfx)]
        graph.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={sfx_db}dB,apad=whole_dur={d}[fx]")
        mix_inputs.append("[fx]")
    graph.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0:duration=first,atrim=0:{d}[mix]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
         "-map", "[mix]", "-ar", "48000", "-ac", "2", str(out)])
    return out


def measure_loudness(path: Path, target: float = -14.0, tp: float = -1.5) -> dict:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
         f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not match:
        raise AudioError("could not measure loudness")
    return json.loads(match.group(0))


def normalize(src: Path, out: Path, target: float = -14.0, tp: float = -1.5) -> dict:
    m = measure_loudness(src, target, tp)
    af = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
          "linear=true:print_format=summary,"
          # Safety net: loudnorm can still let short spikes through (and AAC encoding adds
          # overshoot), so a 4x-oversampled limiter keeps true peaks well under -1 dBTP.
          "aresample=192000,alimiter=limit=0.6:attack=1:release=60:level=false,aresample=48000")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", "48000", "-ac", "2", str(out)])
    return m


def true_peak(path: Path) -> float | None:
    """True peak (dBTP) of a file's audio, measured the same way as the QA check."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                           "-f", "null", "-"], capture_output=True, text=True)
    summary = proc.stderr[proc.stderr.rfind("Summary:"):]
    m = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    if not m:
        return None
    return float("-inf") if m.group(1) == "-inf" else float(m.group(1))


def tame_encoded_peaks(video: Path, audio: Path, duration: float, target: float = -1.5,
                       limit_db: float = -4.4, rounds: int = 3, log=print) -> float | None:
    """Make sure the *encoded* video's true peak is under `target` dBTP.

    The AAC encoder can overshoot the limited WAV by several dB (more on newer FFmpeg),
    so we measure the finished file and, if needed, re-encode only the audio with a
    stronger peak limiter. Loudness barely changes because only short peaks are touched.
    """
    tp = true_peak(video)
    level = true_peak(audio)            # peak going INTO the encoder
    if level is None:
        level = limit_db
    for _ in range(rounds):
        if tp is None or tp <= target:
            return tp
        overshoot = max(tp - level, 0.0)   # what the encoder added on top
        limit_db = target - overshoot - 0.3
        level = limit_db
        lin = max(10 ** (limit_db / 20), 0.0625)
        log(f"audio peak {tp:.1f} dBTP after encoding → re-limiting at {limit_db:.1f} dB")
        tmp = video.with_name("video.peakfix.mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-af", f"lowpass=f=16000,aresample=192000,alimiter=limit={lin:.4f}:attack=1:release=60:"
             "level=false,aresample=48000", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
             "-t", f"{duration:.3f}", "-movflags", "+faststart", str(tmp)])
        tmp.replace(video)
        tp = true_peak(video)
    return tp


def trim_library_track(src: Path, out: Path, duration: float) -> Path:
    """Loop/trim a licensed library track to the video length with a fade-out."""
    d = f"{duration:.3f}"
    run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src), "-t", d,
         "-af", f"afade=t=out:st={max(0.0, duration - 1.2):.3f}:d=1.2", "-ar", "48000", "-ac", "2", str(out)])
    return out
