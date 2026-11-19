"""The verified fact base: loading, validation, freshness and automatic re-checks."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from .textutil import allowed_numbers

STATUSES = ("official", "confirmed", "reported", "rumor")
TRUSTED = ("official", "confirmed")


@dataclass
class Source:
    url: str
    publisher: str = ""
    date: str | None = None
    official: bool = False


@dataclass
class Fact:
    id: str
    claim: str
    status: str
    category: str = "general"
    as_of: date | None = None
    recheck_days: int = 14
    sources: list[Source] = field(default_factory=list)
    say: list[str] = field(default_factory=list)
    attribution: str | None = None
    verify: dict[str, Any] | None = None

    @property
    def texts(self) -> list[str]:
        return [self.claim, *self.say]

    @property
    def numbers(self) -> set[str]:
        return allowed_numbers(self.texts)

    @property
    def primary_source(self) -> Source | None:
        official = [s for s in self.sources if s.official]
        return (official or self.sources or [None])[0]

    def last_verified(self, checks: dict[str, Any] | None = None) -> date | None:
        best = self.as_of
        record = (checks or {}).get(self.id)
        if record and record.get("ok") and record.get("checked"):
            checked = _to_date(record["checked"])
            if checked and (best is None or checked > best):
                best = checked
        return best

    def is_fresh(self, today: date, checks: dict[str, Any] | None = None) -> bool:
        record = (checks or {}).get(self.id)
        if record and record.get("ok") is False:
            return False  # automatic re-check found the source no longer supports it
        verified = self.last_verified(checks)
        if verified is None:
            return False
        return (today - verified).days <= self.recheck_days


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class FactBase:
    def __init__(self, facts: list[Fact], checks: dict[str, Any] | None = None):
        self.facts: dict[str, Fact] = {}
        self.duplicates: list[str] = []
        for f in facts:
            if f.id in self.facts:
                self.duplicates.append(f.id)
            self.facts[f.id] = f
        self.checks = checks or {}

    def __contains__(self, fact_id: str) -> bool:
        return fact_id in self.facts

    def __getitem__(self, fact_id: str) -> Fact:
        return self.facts[fact_id]

    def __len__(self) -> int:
        return len(self.facts)

    def get(self, fact_id: str) -> Fact | None:
        return self.facts.get(fact_id)

    def usable(self, *, allow_rumors: bool = False, today: date | None = None, fresh_only: bool = False) -> list[Fact]:
        out = []
        for f in self.facts.values():
            if f.status == "rumor" and not allow_rumors:
                continue
            if fresh_only and today and not f.is_fresh(today, self.checks):
                continue
            out.append(f)
        return out

    def by_category(self) -> dict[str, list[Fact]]:
        groups: dict[str, list[Fact]] = {}
        for f in self.facts.values():
            groups.setdefault(f.category, []).append(f)
        return groups


def _parse_fact(raw: dict[str, Any], default_recheck: int) -> Fact:
    sources = [
        Source(
            url=str(s.get("url", "")),
            publisher=str(s.get("publisher", "")),
            date=str(s["date"]) if s.get("date") else None,
            official=bool(s.get("official", False)),
        )
        for s in raw.get("sources") or []
    ]
    return Fact(
        id=str(raw.get("id", "")).strip(),
        claim=str(raw.get("claim", "")).strip(),
        status=str(raw.get("status", "")).strip(),
        category=str(raw.get("category", "general")),
        as_of=_to_date(raw.get("as_of")),
        recheck_days=int(raw.get("recheck_days", default_recheck)),
        sources=sources,
        say=[str(s) for s in raw.get("say") or []],
        attribution=raw.get("attribution"),
        verify=raw.get("verify"),
    )


def load_checks(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def load_facts(path: Path, checks_path: Path | None = None, default_recheck: int = 14) -> FactBase:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    facts = [_parse_fact(item, default_recheck) for item in raw.get("facts") or []]
    checks = load_checks(checks_path) if checks_path else {}
    return FactBase(facts, checks)


def validate_factbase(fb: FactBase) -> list[str]:
    """Structural problems in the fact base (empty list = valid)."""
    errors: list[str] = []
    for dup in fb.duplicates:
        errors.append(f"duplicate fact id: {dup}")
    for f in fb.facts.values():
        where = f"fact '{f.id or '?'}'"
        if not f.id or not f.id.replace("_", "").isalnum():
            errors.append(f"{where}: id must be letters/digits/underscores")
        if f.status not in STATUSES:
            errors.append(f"{where}: status must be one of {STATUSES}")
        if len(f.claim) < 10:
            errors.append(f"{where}: claim is missing or too short")
        if not f.sources:
            errors.append(f"{where}: at least one source is required")
        for s in f.sources:
            if not s.url.startswith("https://"):
                errors.append(f"{where}: source url must start with https:// ({s.url!r})")
        if f.status == "official" and not (f.attribution or any(s.official for s in f.sources) or len(f.sources) >= 2):
            errors.append(f"{where}: 'official' needs an official source, an attribution, or 2+ sources")
        if f.status == "confirmed" and len(f.sources) < 1:
            errors.append(f"{where}: 'confirmed' needs sources")
        if f.status == "reported" and not f.attribution:
            errors.append(f"{where}: 'reported' facts must name an attribution (who reported it)")
        if f.as_of is None:
            errors.append(f"{where}: as_of date is required")
        if f.verify:
            if not str(f.verify.get("url", "")).startswith("https://"):
                errors.append(f"{where}: verify.url must be https")
            if not f.verify.get("contains"):
                errors.append(f"{where}: verify.contains must list text to look for")
    return errors


def verify_facts(
    fb: FactBase,
    fetch: Callable[[str], str],
    today: date,
) -> dict[str, Any]:
    """Re-check facts that have a ``verify`` block. Returns updated check records.

    ``fetch(url) -> page text``. Network/HTTP errors are recorded as
    ``ok: None`` (unknown) so a flaky site never silently "confirms" a fact.
    """
    results: dict[str, Any] = dict(fb.checks)
    page_cache: dict[str, str | Exception] = {}
    for f in fb.facts.values():
        if not f.verify:
            continue
        url = f.verify["url"]
        if url not in page_cache:
            try:
                page_cache[url] = fetch(url)
            except Exception as exc:  # noqa: BLE001 - we record any failure
                page_cache[url] = exc
        page = page_cache[url]
        if isinstance(page, Exception):
            results[f.id] = {"checked": today.isoformat(), "ok": None, "detail": f"fetch failed: {page}"[:300]}
            continue
        normalized = " ".join(page.split()).lower()
        missing = [c for c in f.verify["contains"] if " ".join(str(c).split()).lower() not in normalized]
        results[f.id] = {
            "checked": today.isoformat(),
            "ok": not missing,
            "detail": "all phrases found" if not missing else f"missing on page: {missing}",
        }
    return results
