"""Free-tier usage tracking (data/usage.json), so we never silently exceed limits."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

LIMIT_KEYS = {
    "youtube_uploads": "limits.youtube_uploads_per_day",
    "youtube_units": "limits.youtube_units_per_day",
    "gemini_requests": "limits.gemini_requests_per_day",
}


class Usage:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def add(self, key: str, amount: int = 1, day: date | None = None) -> int:
        d = (day or date.today()).isoformat()
        bucket = self.data.setdefault(d, {})
        bucket[key] = bucket.get(key, 0) + amount
        return bucket[key]

    def get(self, key: str, day: date | None = None) -> int:
        return self.data.get((day or date.today()).isoformat(), {}).get(key, 0)

    def warnings(self, cfg, day: date | None = None) -> list[str]:
        out = []
        for key, setting in LIMIT_KEYS.items():
            limit = int(cfg.get(setting, 0) or 0)
            used = self.get(key, day)
            if limit and used >= 0.8 * limit:
                out.append(f"{key}: {used}/{limit} used today ({used / limit:.0%})")
        return out

    def save(self, keep_days: int = 60) -> None:
        for k in sorted(self.data)[:-keep_days]:
            del self.data[k]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
