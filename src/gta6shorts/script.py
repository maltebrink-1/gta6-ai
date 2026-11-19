"""Video script model (one YAML file per video in content/scripts/)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

KINDS = ("hook", "claim", "opinion", "question", "cta", "transition")
CARD_TYPES = ("stat", "text", "versus", "date")
FORMATS = ("news", "list", "explainer", "timeline", "spotlight", "versus", "qna", "myth")
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Card:
    type: str = "text"
    big: str = ""
    small: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "big": self.big, "small": self.small}


@dataclass
class Line:
    text: str
    kind: str = "claim"
    facts: list[str] = field(default_factory=list)
    card: Card | None = None
    broll: str | None = None  # optional stock-footage search query

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text, "kind": self.kind}
        if self.facts:
            d["facts"] = list(self.facts)
        if self.card:
            d["card"] = self.card.to_dict()
        if self.broll:
            d["broll"] = self.broll
        return d


@dataclass
class Script:
    id: str
    title: str
    hook_text: str
    lines: list[Line]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    format: str = "explainer"
    author: str = "human"
    created: str | None = None
    theme: str | None = None
    status: str = "ready"  # draft | ready | archived
    notes: str = ""
    path: Path | None = field(default=None, repr=False, compare=False)

    @property
    def spoken_text(self) -> str:
        return " ".join(line.text.strip() for line in self.lines)

    @property
    def word_count(self) -> int:
        return len(self.spoken_text.split())

    def fact_ids(self) -> list[str]:
        seen: list[str] = []
        for line in self.lines:
            for fid in line.facts:
                if fid not in seen:
                    seen.append(fid)
        return seen

    def estimated_seconds(self, words_per_second: float = 2.5) -> float:
        return self.word_count / max(words_per_second, 0.1)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "created": self.created,
            "author": self.author,
            "format": self.format,
            "title": self.title,
            "hook_text": self.hook_text,
        }
        if self.theme:
            d["theme"] = self.theme
        d["lines"] = [line.to_dict() for line in self.lines]
        d["description"] = self.description
        d["tags"] = list(self.tags)
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any], path: Path | None = None) -> "Script":
        lines = []
        for item in raw.get("lines") or []:
            if isinstance(item, str):
                item = {"text": item}
            card = item.get("card")
            lines.append(
                Line(
                    text=str(item.get("text", "")).strip(),
                    kind=str(item.get("kind", "claim")),
                    facts=[str(f) for f in item.get("facts") or []],
                    card=Card(**{k: str(v) for k, v in card.items() if k in ("type", "big", "small")}) if card else None,
                    broll=item.get("broll"),
                )
            )
        return cls(
            id=str(raw.get("id", "")).strip(),
            title=str(raw.get("title", "")).strip(),
            hook_text=str(raw.get("hook_text", "")).strip(),
            lines=lines,
            description=str(raw.get("description", "") or "").strip(),
            tags=[str(t) for t in raw.get("tags") or []],
            format=str(raw.get("format", "explainer")),
            author=str(raw.get("author", "human")),
            created=str(raw["created"]) if raw.get("created") else None,
            theme=raw.get("theme"),
            status=str(raw.get("status", "ready")),
            notes=str(raw.get("notes", "") or ""),
            path=path,
        )

    @classmethod
    def load(cls, path: Path) -> "Script":
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path=path)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True, width=100)
        path.write_text(text, encoding="utf-8")
        self.path = path
        return path


def load_scripts(directory: Path) -> list[Script]:
    if not directory.exists():
        return []
    return [Script.load(p) for p in sorted(directory.glob("*.yaml"))]


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "video"
