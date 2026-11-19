"""News monitoring: collects GTA 6 headlines as *topic leads*.

Headlines are never treated as facts. A lead only becomes usable content after
it is verified and added to content/facts.yaml with sources (by the scheduled
Claude editor session, or by you).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

import requests

KEYWORDS = ("gta 6", "gta vi", "grand theft auto vi", "grand theft auto 6", "rockstar", "leonida", "vice city")


def fetch_text(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "gta6shorts-research/0.1 (+github actions)"})
    resp.raise_for_status()
    return resp.text


def parse_rss(xml_text: str, feed_name: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        source = item.find("source")
        try:
            published = parsedate_to_datetime(pub).date().isoformat() if pub else None
        except (TypeError, ValueError):
            published = None
        items.append({"title": title, "link": link, "published": published, "feed": feed_name,
                      "source": source.text.strip() if source is not None and source.text else ""})
    return items


def relevant(item: dict) -> bool:
    t = item["title"].lower()
    return any(k in t for k in KEYWORDS)


def collect_leads(feeds: list[dict], fetch: Callable[[str], str] = fetch_text, max_items: int = 40) -> tuple[list[dict], list[str]]:
    leads, errors, seen = [], [], set()
    for feed in feeds:
        try:
            for item in parse_rss(fetch(feed["url"]), feed.get("name", feed["url"])):
                key = item["title"].lower()[:90]
                if relevant(item) and key not in seen:
                    seen.add(key)
                    leads.append(item)
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the run
            errors.append(f"{feed.get('name')}: {exc}")
    leads.sort(key=lambda x: x.get("published") or "", reverse=True)
    return leads[:max_items], errors


def save_leads(leads: list[dict], directory: Path, day: date) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.json"
    path.write_text(json.dumps({"date": day.isoformat(), "note": "Topic leads only — NOT verified facts.",
                                "leads": leads}, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def latest_leads(directory: Path) -> list[dict]:
    files = sorted(directory.glob("*.json"))
    if not files:
        return []
    return json.loads(files[-1].read_text(encoding="utf-8")).get("leads", [])
