"""Platform metadata: description with sources, honest AI disclosure, hashtags."""
from __future__ import annotations

from datetime import date

from .facts import FactBase
from .script import Script

DISCLOSURE = (
    "Narration: AI-generated voice. Visuals: original motion graphics made for this channel — "
    "not gameplay footage. Music: original, generated for this channel."
)
NOT_AFFILIATED = "Fan channel — not affiliated with or endorsed by Rockstar Games or Take-Two Interactive."


def script_sources(script: Script, fb: FactBase) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for fid in script.fact_ids():
        fact = fb.get(fid)
        if not fact:
            continue
        for s in fact.sources[:2]:
            if s.url not in seen:
                seen[s.url] = s.publisher or s.url
    return [(pub, url) for url, pub in seen.items()]


def hashtags(script: Script, limit: int = 3) -> list[str]:
    tags = ["#GTA6", "#GTAVI"]
    for t in script.tags:
        h = "#" + "".join(ch for ch in t.title() if ch.isalnum())
        if h not in tags and len(h) > 3:
            tags.append(h)
    return tags[:limit]


def youtube_description(script: Script, fb: FactBase, checked: date, music_credit: str | None = None,
                        stock_credits: list[str] | None = None) -> str:
    parts = [script.description.strip()] if script.description else []
    sources = script_sources(script, fb)
    if sources:
        parts.append("Sources:\n" + "\n".join(f"• {pub} — {url}" for pub, url in sources))
    disclosure = DISCLOSURE if not music_credit else DISCLOSURE.replace(
        "Music: original, generated for this channel.", f"Music: {music_credit}")
    if stock_credits:
        disclosure = disclosure.replace(
            "Visuals: original motion graphics made for this channel — not gameplay footage.",
            "Visuals: original motion graphics plus stock footage by " + ", ".join(sorted(set(stock_credits)))
            + " — not gameplay footage.")
    parts.append(f"{disclosure}\nFacts checked {checked.isoformat()}. {NOT_AFFILIATED}")
    parts.append(" ".join(hashtags(script) + ["#Shorts"]))
    return "\n\n".join(parts)[:4900]


def tiktok_caption(script: Script) -> str:
    tags = " ".join(hashtags(script, 4))
    text = f"{script.title} {tags} #gaming"
    return text[:2000]
