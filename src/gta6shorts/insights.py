"""Learn what works: which topics, formats, looks and lengths get the most views.

Written to data/insights.json (read by the daily Claude editor) and, on
Sundays, to reports/weekly/<year>-W<week>.md. Views are normalised per day
since upload so new and old videos can be compared fairly.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from statistics import median

from .facts import FactBase
from .registry import Registry

MIN_VIDEOS = 6        # below this the channel has too little data to learn from
MIN_PER_GROUP = 2     # a topic/format needs at least this many videos to be ranked


def _days_live(published_at: str, today: date) -> float:
    try:
        d = datetime.fromisoformat(published_at.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return 1.0
    return max(1.0, float((today - d).days) + 0.5)


def _length_bucket(seconds) -> str:
    if not seconds:
        return "unknown"
    return "short (<35 s)" if seconds < 35 else ("medium (35-45 s)" if seconds <= 45 else "long (>45 s)")


def video_rows(reg: Registry, snapshot: dict | None, fb: FactBase, today: date) -> list[dict]:
    yt_stats = ((snapshot or {}).get("youtube") or {}).get("videos") or {}
    rows = []
    for v in reg.videos.values():
        yt = (v.get("platforms") or {}).get("youtube") or {}
        if not yt.get("id"):
            continue
        st = yt_stats.get(yt["id"], {})
        views = st.get("views", 0)
        days = _days_live(yt.get("published_at", ""), today)
        cats = sorted({fb.get(f).category for f in v.get("fact_ids", []) if fb.get(f)})
        rows.append({
            "id": v["id"], "title": v.get("title", ""), "views": views,
            "likes": st.get("likes", 0), "comments": st.get("comments", 0),
            "views_per_day": round(views / days, 1),
            "engagement": round((st.get("likes", 0) + st.get("comments", 0)) / views, 4) if views else 0.0,
            "format": v.get("format") or "unknown", "theme": v.get("theme") or "unknown",
            "length": _length_bucket(v.get("duration")), "topics": cats or ["unknown"],
        })
    return rows


def _rank(rows: list[dict], key: str, baseline: float) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        values = r[key] if isinstance(r[key], list) else [r[key]]
        for val in values:
            groups.setdefault(val, []).append(r)
    ranked = []
    for name, items in groups.items():
        if len(items) < MIN_PER_GROUP:
            continue
        med = median(x["views_per_day"] for x in items)
        ranked.append({"name": name, "videos": len(items), "median_views_per_day": round(med, 1),
                       "vs_channel": round(med / baseline, 2) if baseline else None,
                       "engagement": round(median(x["engagement"] for x in items), 4)})
    return sorted(ranked, key=lambda g: g["median_views_per_day"], reverse=True)


def build_insights(reg: Registry, snapshot: dict | None, fb: FactBase, today: date) -> dict:
    rows = video_rows(reg, snapshot, fb, today)
    out: dict = {"generated": today.isoformat(), "published_videos": len(rows), "enough_data": len(rows) >= MIN_VIDEOS}
    if not rows:
        out["advice"] = "No published videos with statistics yet — keep variety high."
        return out
    baseline = median(r["views_per_day"] for r in rows) or 0.0
    out["channel_median_views_per_day"] = round(baseline, 1)
    out["top_videos"] = sorted(rows, key=lambda r: r["views_per_day"], reverse=True)[:5]
    out["weakest_videos"] = sorted(rows, key=lambda r: r["views_per_day"])[:3]
    for key, label in (("topics", "topics"), ("format", "formats"), ("theme", "themes"), ("length", "lengths")):
        out[label] = _rank(rows, key, baseline)
    if not out["enough_data"]:
        out["advice"] = (f"Only {len(rows)} published videos — too early to trust patterns "
                         f"(need {MIN_VIDEOS}). Keep variety high.")
    else:
        best = [g["name"] for g in out["topics"][:2]] or ["(no clear winner)"]
        fmt = [g["name"] for g in out["formats"][:2]] or ["(no clear winner)"]
        out["advice"] = (f"Lean into topics {', '.join(best)} and formats {', '.join(fmt)} for 3 of 4 scripts; "
                         "use the 4th for something new.")
    return out


def weekly_report(ins: dict, today: date) -> str:
    y, w, _ = today.isocalendar()
    lines = [f"# 📈 Weekly learnings — {y}-W{w:02d}", "", f"Published videos analysed: **{ins['published_videos']}**", "",
             f"**Advice:** {ins.get('advice', '')}", ""]
    if ins.get("top_videos"):
        lines += ["## Best videos (views per day since upload)", "| Video | Views/day | Views | Engagement |", "|---|---|---|---|"]
        lines += [f"| {r['title'].replace('|', '/')} | {r['views_per_day']} | {r['views']} | {r['engagement']:.1%} |"
                  for r in ins["top_videos"]]
        lines.append("")
    for label in ("topics", "formats", "themes", "lengths"):
        groups = ins.get(label) or []
        if groups:
            lines += [f"## {label.title()}", "| | Videos | Median views/day | vs channel |", "|---|---|---|---|"]
            lines += [f"| {g['name']} | {g['videos']} | {g['median_views_per_day']} | ×{g['vs_channel']} |" for g in groups]
            lines.append("")
    return "\n".join(lines)


def write_insights(ins: dict, data_dir: Path, reports_dir: Path, today: date) -> list[Path]:
    paths = [data_dir / "insights.json"]
    paths[0].write_text(json.dumps(ins, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if today.weekday() == 6:  # Sunday
        y, w, _ = today.isocalendar()
        p = reports_dir / "weekly" / f"{y}-W{w:02d}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(weekly_report(ins, today), encoding="utf-8")
        paths.append(p)
    return paths
