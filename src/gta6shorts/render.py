"""Turn a script into a finished 1080x1920 video.

script ─► narration (TTS + word timing) ─► timeline (one segment per line)
       ─► backgrounds (procedural / stock) ─► ASS graphics & captions
       ─► music + sfx + mastering ─► FFmpeg: xfade transitions, grain, vignette,
          burned-in captions, H.264/AAC ─► thumbnail, contact sheet, SRT, metadata
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .audio import mix, normalize, tame_encoded_peaks, trim_library_track
from .captions import build_ass, build_srt
from .facts import FactBase
from .metadata import tiktok_caption, youtube_description
from .music import build_sfx_track, load_library, synth_track, write_wav
from .script import Script
from .styles import Theme, pick_theme, stable_int
from .tts import Narration, narrate
from .visuals import render_clip


class RenderError(RuntimeError):
    pass


@dataclass
class Segment:
    start: float
    end: float
    style: str
    clip_duration: float = 0.0
    offset: float = 0.0  # xfade offset of the transition INTO this segment
    transition: str = "fade"
    source: str = "procedural"
    path: Path | None = None


@dataclass
class RenderResult:
    script_id: str
    out_dir: Path
    video: Path
    duration: float
    theme: str
    engine: str
    voice: str
    files: dict[str, str] = field(default_factory=dict)
    seconds_taken: float = 0.0


def plan_timeline(line_starts: list[float], duration: float, transition: float) -> list[tuple[float, float, float, float]]:
    """Segment boundaries and xfade parameters.

    Returns (start, end, clip_duration, offset) per segment, where segment k
    covers [start, end) of the final video. Clip k must be ``clip_duration``
    long and fades in at ``offset`` (centered on the boundary). Total = duration.
    """
    bounds = [0.0] + [max(0.0, s - 0.06) for s in line_starts[1:]] + [duration]
    # merge segments shorter than 1 s into their neighbour (no flicker)
    merged = [bounds[0]]
    for b in bounds[1:-1]:
        if b - merged[-1] >= 1.0:
            merged.append(b)
    if duration - merged[-1] < 1.0 and len(merged) > 1:
        merged.pop()
    merged.append(duration)
    half = transition / 2
    n = len(merged) - 1
    out = []
    for k in range(n):
        start, end = merged[k], merged[k + 1]
        if n == 1:
            clip, offset = duration, 0.0
        elif k == 0:
            clip, offset = end + half, 0.0
        elif k == n - 1:
            clip, offset = duration - start + half, start - half
        else:
            clip, offset = (end - start) + transition, start - half
        out.append((start, end, round(clip, 3), round(offset, 3)))
    return out


def _zoom_filter(idx: int, w: int, h: int, fps: int, punch: float = 0.07) -> str:
    z = f"(1+{punch}*max(0\\,1-t/0.45))"
    return (f"[{idx}:v]scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame:flags=bicubic,"
            f"crop={w}:{h},setsar=1,fps={fps},format=yuv420p[v{idx}]")


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def render_script(
    script: Script,
    fb: FactBase,
    cfg,
    engine,
    out_root: Path,
    *,
    today: date,
    previous_theme: str | None = None,
    stock_provider=None,
    keep_intermediates: bool = False,
    log=print,
) -> RenderResult:
    t_start = time.time()
    W, H = int(cfg.get("production.width", 1080)), int(cfg.get("production.height", 1920))
    fps = int(cfg.get("production.fps", 30))
    T = float(cfg.get("visuals.transition_seconds", 0.30))
    scale = float(cfg.get("visuals.background_render_scale", 0.5))
    out = out_root / script.id
    if out.exists():
        shutil.rmtree(out)
    work = out / "work"
    work.mkdir(parents=True)

    theme: Theme = pick_theme(cfg.styles, script.id, script.theme, previous_theme)
    seed = stable_int(script.id)
    log(f"[{script.id}] theme={theme.name} font={theme.font}")

    # 1. narration --------------------------------------------------------
    narration: Narration = narrate(script, engine, line_gap=float(cfg.get("voice.line_gap", 0.10)))
    narration.save(work / "narration.wav", out / "words.json")
    duration = round(narration.duration + float(cfg.get("production.tail_seconds", 0.7)), 3)
    log(f"[{script.id}] narration {narration.duration:.1f}s via {narration.engine} ({narration.voice})")

    # 2. timeline + backgrounds -------------------------------------------
    plan = plan_timeline([s for s, _ in narration.lines], duration, T)
    segments: list[Segment] = []
    styles = theme.backgrounds
    for k, (start, end, clip, offset) in enumerate(plan):
        style = "streaks" if k == 0 and "streaks" in styles else styles[(k + seed) % len(styles)]
        if k > 0 and segments and style == segments[-1].style and len(styles) > 1:
            style = styles[(k + seed + 1) % len(styles)]
        trans = theme.transitions[(k + seed) % len(theme.transitions)]
        segments.append(Segment(start, end, style, clip, offset, trans))

    line_of_segment = []
    for seg in segments:
        idx = max(i for i, (s, _) in enumerate(narration.lines) if s - 0.07 <= seg.start or i == 0)
        line_of_segment.append(idx)

    bw, bh = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2
    for k, seg in enumerate(segments):
        clip_path = work / f"bg_{k:02d}.mp4"
        query = script.lines[line_of_segment[k]].broll if line_of_segment[k] < len(script.lines) else None
        if stock_provider and query:
            try:
                got = stock_provider.clip(query, seg.clip_duration, clip_path, seed=seed + k)
            except Exception as exc:  # noqa: BLE001 - fall back to procedural art
                log(f"[{script.id}] stock footage failed ({exc}); using procedural background")
                got = None
            if got:
                seg.path, seg.source = got, "stock"
                continue
        render_clip(seg.style, theme.palette, seg.clip_duration + 0.05, clip_path, seed=seed + k, fps=fps,
                    w=bw, h=bh, time_offset=seg.start)
        seg.path = clip_path
    log(f"[{script.id}] {len(segments)} background segments: {[s.style for s in segments]}")

    # 3. captions & graphics ---------------------------------------------
    ass_text = build_ass(script, narration, theme, fb, cfg, cfg.fonts_dir, duration)
    (out / "captions.ass").write_text(ass_text, encoding="utf-8")
    (out / "captions.srt").write_text(build_srt(narration.words), encoding="utf-8")

    # 4. audio ------------------------------------------------------------
    music_path, music_credit = None, None
    source = cfg.get("music.source", "generated")
    if source == "generated":
        track, info = synth_track(duration, seed=seed % 100000)
        music_path = write_wav(work / "music.wav", track)
        music_info = {"type": "generated", "bpm": info.bpm, "key": info.key, "progression": info.progression}
    elif source == "library":
        tracks = load_library(cfg.path(cfg.get("music.library_manifest", "assets/music/licenses.yaml")))
        if tracks:
            t = tracks[seed % len(tracks)]
            manifest_dir = cfg.path(cfg.get("music.library_manifest")).parent
            music_path = trim_library_track(manifest_dir / t["file"], work / "music.wav", duration)
            music_credit = f"\"{t['title']}\" by {t['artist']} ({t['license']}) {t.get('attribution', '')}".strip()
            music_info = {"type": "library", **{k: t[k] for k in ("title", "artist", "license", "source_url")}}
        else:
            music_info = {"type": "none", "reason": "no licensed tracks in manifest"}
    else:
        music_info = {"type": "none"}

    sfx_path = None
    if cfg.get("music.sfx", True):
        whooshes = [max(0.0, s.offset - 0.12) for s in segments[1:]]
        pops = [narration.lines[i][0] + 0.05 for i, line in enumerate(script.lines)
                if i > 0 and i < len(narration.lines) and (line.card or line.kind == "cta")]
        sfx = build_sfx_track(duration, impacts=[0.0], whooshes=whooshes, pops=pops, seed=seed)
        sfx_path = write_wav(work / "sfx.wav", sfx)

    mix(work / "narration.wav", music_path, sfx_path, work / "mix.wav", duration,
        music_db=float(cfg.get("music.volume_db", -17)))
    loud = normalize(work / "mix.wav", work / "final_audio.wav", float(cfg.get("quality.loudness_target", -14.0)))

    # 5. final video --------------------------------------------------------
    inputs: list[str] = []
    graph: list[str] = []
    for k, seg in enumerate(segments):
        inputs += ["-i", str(seg.path)]
        graph.append(_zoom_filter(k, W, H, fps))
    last = "v0"
    for k, seg in enumerate(segments[1:], start=1):
        graph.append(f"[{last}][v{k}]xfade=transition={seg.transition}:duration={T}:offset={seg.offset:.3f}[x{k}]")
        last = f"x{k}"
    fonts_rel = os.path.relpath(cfg.fonts_dir, out)
    graph.append(f"[{last}]noise=alls=5:allf=t+u,vignette=angle=PI/5,"
                 f"ass=filename=captions.ass:fontsdir={fonts_rel},format=yuv420p,trim=0:{duration:.3f}[vout]")
    audio_idx = len(segments)
    inputs += ["-i", str(work / "final_audio.wav")]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", f"{audio_idx}:a",
        "-c:v", "libx264", "-preset", str(cfg.get("production.x264_preset", "medium")), "-crf", "19",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}",
        "-movflags", "+faststart", "video.mp4",
    ]
    proc = subprocess.run(cmd, cwd=out, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RenderError(f"final render failed:\n{proc.stderr[-2500:]}")
    video = out / "video.mp4"
    tame_encoded_peaks(video, work / "final_audio.wav", duration,
                       target=float(cfg.get("quality.true_peak_max", -1.0)) - 0.5,
                       loudness=float(cfg.get("quality.loudness_target", -14.0)), log=log)

    # 6. extras ------------------------------------------------------------
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", "video.mp4", "-frames:v", "1",
                    "-q:v", "3", "thumb.jpg"], cwd=out, check=True)
    step = max(duration / 12, 0.5)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "video.mp4", "-vf",
                    f"fps=1/{step:.3f},scale=270:480,tile=4x3:padding=6:color=black", "-frames:v", "1",
                    "-q:v", "4", "contact_sheet.jpg"], cwd=out, check=True)

    meta = {
        "script_id": script.id,
        "title": script.title,
        "description": youtube_description(script, fb, today, music_credit,
                                           getattr(stock_provider, "credits", None) if stock_provider else None),
        "stock_credits": list(getattr(stock_provider, "credits", []) or []) if stock_provider else [],
        "tiktok_caption": tiktok_caption(script),
        "tags": script.tags,
        "duration": duration,
        "theme": theme.name,
        "font": theme.font,
        "voice": {"engine": narration.engine, "voice": narration.voice},
        "music": music_info,
        "segments": [{"start": s.start, "end": s.end, "style": s.style, "source": s.source,
                      "transition": s.transition} for s in segments],
        "loudness_measured_before_norm": {k: loud[k] for k in ("input_i", "input_tp", "input_lra")},
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fact_ids": script.fact_ids(),
        "author": script.author,
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    if not keep_intermediates:
        shutil.rmtree(work, ignore_errors=True)
    files = {name: str(out / name) for name in ("video.mp4", "thumb.jpg", "contact_sheet.jpg", "captions.ass",
                                                "captions.srt", "metadata.json", "words.json")}
    took = time.time() - t_start
    log(f"[{script.id}] rendered {duration:.1f}s video in {took:.0f}s → {video}")
    return RenderResult(script.id, out, video, duration, theme.name, narration.engine, narration.voice, files, took)
