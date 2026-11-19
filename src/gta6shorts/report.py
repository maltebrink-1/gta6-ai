"""Daily performance report (Markdown) — saved to reports/daily/ and shown in the
GitHub Actions run summary (readable in the GitHub mobile app)."""
from __future__ import annotations

from datetime import date

from .facts import FactBase


def compact(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit, div in (("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}".replace(".0", "")
    return f"{int(n):,}"


def _sum(days: list[dict], key: str) -> int:
    return sum(int(d.get(key, 0) or 0) for d in days)


def daily_report(metrics: dict, fb: FactBase, today: date) -> str:
    days = metrics.get("days", [])
    last7, prev7 = days[-7:], days[-14:-7]
    latest = days[-1] if days else {}
    p = metrics["pipeline"]
    stage = metrics["stage"]

    def delta(key: str) -> str:
        a, b = _sum(last7, key), _sum(prev7, key)
        if not prev7:
            return f"{compact(a)}"
        diff = a - b
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "■")
        return f"{compact(a)} ({arrow} {compact(abs(diff))} vs previous 7 days)"

    lines = [f"# 📊 Daily report — {today.isoformat()}", ""]
    if metrics.get("demo"):
        lines += ["> ⚠️ DEMO DATA — real numbers appear once YouTube is connected.", ""]
    lines += [
        "## Today",
        f"- Views gained: **{compact(latest.get('yt_views_gained', 0) + latest.get('tt_views_gained', 0))}** "
        f"(YouTube {compact(latest.get('yt_views_gained', 0))} · TikTok {compact(latest.get('tt_views_gained', 0))})",
        f"- Likes gained: **{compact(latest.get('likes_gained', 0))}** · Comments gained: **{compact(latest.get('comments_gained', 0))}**",
        f"- New subscribers (YouTube): **{compact(latest.get('subs_gained', 0))}** · New followers (TikTok): **{compact(latest.get('followers_gained', 0))}**",
        f"- Videos rendered: **{latest.get('rendered', 0)}** · published: **{latest.get('published', 0)}**",
        "",
        "## Last 7 days",
        f"- YouTube views: {delta('yt_views_gained')}",
        f"- TikTok views: {delta('tt_views_gained')}",
        f"- Subscribers gained: {delta('subs_gained')}",
        f"- Followers gained: {delta('followers_gained')}",
        "",
        "## Totals",
        f"- Subscribers: **{compact(latest.get('yt_subs'))}** · TikTok followers: **{compact(latest.get('tt_followers'))}**",
        f"- Videos created: **{p['created']}** · published: **{p['published']}** · awaiting review: **{p['awaiting_review']}**"
        f" · QA failed: **{p['qa_failed']}** · rejected: **{p['rejected']}**",
        "",
    ]
    best = sorted([v for v in metrics.get("videos", []) if v.get("views")], key=lambda v: v["views"], reverse=True)[:3]
    if best:
        lines += ["## 🏆 Best videos", "| Video | Views | Likes | Comments | Engagement |", "|---|---|---|---|---|"]
        for v in best:
            title = v["title"].replace("|", "/")
            link = f"[{title}]({v['youtube_url']})" if v.get("youtube_url") else title
            lines.append(f"| {link} | {compact(v['views'])} | {compact(v['likes'])} | {compact(v['comments'])} | "
                         f"{v['engagement']:.1%} |")
        lines.append("")

    attention = []
    if p["awaiting_review"]:
        attention.append(f"{p['awaiting_review']} video(s) waiting for your review (issues labelled `review`).")
    if p["qa_failed"]:
        attention.append(f"{p['qa_failed']} video(s) failed QA — see the produce run log.")
    stale = [f.id for f in fb.facts.values() if not f.is_fresh(today, fb.checks)]
    if stale:
        attention.append(f"{len(stale)} fact(s) need re-verification: {', '.join(stale[:8])}")
    for e in metrics.get("errors", []):
        attention.append(f"Stats collection problem: {e}")
    for name, u in metrics.get("usage", {}).items():
        if isinstance(u, dict) and u.get("limit") and u["used"] >= 0.8 * u["limit"]:
            attention.append(f"Free-tier usage high: {name} {u['used']}/{u['limit']}")
    if not stage["publishing_enabled"]:
        attention.append("Publishing is OFF (Stage 1–2). Nothing is uploaded automatically.")
    if not stage.get("youtube_stats"):
        attention.append("YouTube is not connected yet, so views/likes/subscribers are not collected.")
    lines += ["## 👀 Needs your attention", *([f"- {a}" for a in attention] or ["- Nothing — all good."]), ""]
    return "\n".join(lines)
