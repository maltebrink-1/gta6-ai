"""data/videos.json — the single source of truth for every video's lifecycle.

rendered → qa_failed | awaiting_review → approved | rejected → published
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = ("qa_failed", "awaiting_review", "approved", "rejected", "published", "publish_failed")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


MAX_ATTEMPTS = 2   # a video that fails QA is re-made once, then dropped


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"videos": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8")) or {"videos": {}}
            self.data.setdefault("videos", {})

    @property
    def videos(self) -> dict[str, dict]:
        return self.data["videos"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    def get(self, vid: str) -> dict | None:
        return self.videos.get(vid)

    def by_status(self, *statuses: str) -> list[dict]:
        return [v for v in self.videos.values() if v.get("status") in statuses]

    def record_render(self, script, result, qa, metadata: dict | None = None) -> dict:
        rec = self.videos.get(script.id, {"id": script.id, "created_at": now_iso(), "platforms": {}, "review": {}})
        rec.update({
            "title": script.title,
            "script": f"content/scripts/{script.id}.yaml",
            "author": script.author,
            "format": script.format,
            "rendered_at": now_iso(),
            "duration": round(result.duration, 2),
            "theme": result.theme,
            "engine": result.engine,
            "voice": result.voice,
            "render_seconds": round(result.seconds_taken, 1),
            "attempts": int(rec.get("attempts", 1 if rec.get("rendered_at") else 0)) + 1,
            "qa": {
                "passed": qa.passed,
                "failures": [f"{c.name}: {c.detail}" for c in qa.failures],
                "warnings": [f"{c.name}: {c.detail}" for c in qa.warnings],
            },
            "status": "awaiting_review" if qa.passed else "qa_failed",
        })
        if metadata:
            rec["fact_ids"] = metadata.get("fact_ids", [])
        rec.setdefault("review", {})["decision"] = None
        self.videos[script.id] = rec
        return rec

    def set_review(self, vid: str, **fields) -> dict:
        rec = self.videos[vid]
        rec.setdefault("review", {}).update(fields)
        return rec

    def decide(self, vid: str, decision: str, by: str = "") -> dict:
        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        rec = self.videos[vid]
        if rec.get("status") == "published":
            return rec
        rec.setdefault("review", {}).update({"decision": decision, "decided_at": now_iso(), "decided_by": by})
        rec["status"] = decision
        return rec

    def mark_published(self, vid: str, platform: str, **fields) -> dict:
        rec = self.videos[vid]
        rec.setdefault("platforms", {})[platform] = {"published_at": now_iso(), **fields}
        rec["status"] = "published"
        return rec

    def mark_publish_failed(self, vid: str, platform: str, error: str) -> dict:
        rec = self.videos[vid]
        rec.setdefault("platforms", {}).setdefault(platform, {})["last_error"] = error[:500]
        rec["status"] = "publish_failed"
        return rec

    def published_on(self, day: str, platform: str | None = None) -> int:
        count = 0
        for v in self.videos.values():
            for name, p in (v.get("platforms") or {}).items():
                if platform and name != platform:
                    continue
                if str(p.get("published_at", "")).startswith(day) and (p.get("id") or p.get("publish_id")):
                    count += 1
        return count
