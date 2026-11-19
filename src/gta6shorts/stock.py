"""Optional real-world B-roll from Pexels (free API key, free licence).

Pexels licence: free to use and modify, no attribution required (we credit
anyway). Only used when PEXELS_API_KEY is set and a script line has a
`broll:` query. Stock clips are atmosphere (Florida/Miami vibes), never shown
as game footage — the description says so.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

import requests

API = "https://api.pexels.com/videos/search"


class PexelsProvider:
    def __init__(self, api_key: str, cache_dir: Path, session: requests.Session | None = None, w: int = 540, h: int = 960):
        self.api_key, self.cache = api_key, cache_dir
        self.session = session or requests.Session()
        self.w, self.h = w, h
        self.credits: list[str] = []
        self.requests_made = 0
        cache_dir.mkdir(parents=True, exist_ok=True)

    def search(self, query: str) -> list[dict]:
        resp = self.session.get(API, headers={"Authorization": self.api_key}, timeout=30,
                                params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15})
        self.requests_made += 1
        if resp.status_code != 200:
            raise RuntimeError(f"Pexels search failed (HTTP {resp.status_code})")
        return resp.json().get("videos", [])

    @staticmethod
    def pick_file(video: dict) -> dict | None:
        files = [f for f in video.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and f.get("height", 0) > f.get("width", 1)
                 and 960 <= f.get("height", 0) <= 2160]
        files.sort(key=lambda f: abs(f.get("height", 0) - 1920))
        return files[0] if files else None

    def clip(self, query: str, duration: float, out: Path, seed: int = 0) -> Path | None:
        rng = random.Random(seed)
        candidates = [v for v in self.search(query) if v.get("duration", 0) >= min(duration, 4)]
        rng.shuffle(candidates)
        for video in candidates:
            f = self.pick_file(video)
            if not f:
                continue
            src = self.cache / f"pexels-{video['id']}-{f['id']}.mp4"
            if not src.exists():
                with self.session.get(f["link"], stream=True, timeout=120) as resp:
                    if resp.status_code != 200:
                        continue
                    with src.open("wb") as fh:
                        for chunk in resp.iter_content(1 << 20):
                            fh.write(chunk)
            start = max(0.0, rng.uniform(0, max(0.0, video.get("duration", 0) - duration - 0.5)))
            vf = (f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h},"
                  "eq=saturation=1.12:contrast=1.04:brightness=-0.05,fps=30")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(src),
                   "-t", f"{duration:.3f}", "-vf", vf, "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
                   "-pix_fmt", "yuv420p", str(out)]
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                user = video.get("user", {}).get("name", "Pexels contributor")
                self.credits.append(f"{user} (Pexels)")
                return out
        return None
