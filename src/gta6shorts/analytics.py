"""Statistics collection (YouTube / TikTok) and the metrics behind the dashboard
and daily report. Snapshots are stored per day in data/stats/YYYY-MM-DD.json.
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import env, has_env
from .registry import Registry
from .usage import Usage


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── Collection ───────────────────────────────────────────────────────────────
def collect_snapshot(cfg, reg: Registry, today: date, log=print) -> dict:
    snap: dict = {"date": today.isoformat(), "collected_at": now_iso(), "youtube": None, "tiktok": None, "errors": []}
    usage = Usage(cfg.data_dir / "usage.json")
    if has_env("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        from .youtube import YouTubeClient

        try:
            yt = YouTubeClient(env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN"))
            ids = [v["platforms"]["youtube"]["id"] for v in reg.videos.values()
                   if (v.get("platforms") or {}).get("youtube", {}).get("id")]
            snap["youtube"] = {"channel": yt.channel_stats(), "videos": yt.video_stats(ids) if ids else {}}
            usage.add("youtube_units", 1 + math.ceil(len(ids) / 50), today)
            try:
                start = (today - timedelta(days=7)).isoformat()
                snap["youtube"]["daily"] = yt.daily_analytics(start, today.isoformat())
            except Exception as exc:  # noqa: BLE001 - analytics scope is optional
                snap["errors"].append(f"youtube analytics: {exc}")
        except Exception as exc:  # noqa: BLE001
            snap["errors"].append(f"youtube: {exc}")
    if has_env("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN") and cfg.get("publishing.tiktok.enabled"):
        from .tiktok import TikTokClient

        try:
            tt = TikTokClient(env("TIKTOK_CLIENT_KEY"), env("TIKTOK_CLIENT_SECRET"), env("TIKTOK_REFRESH_TOKEN"))
            snap["tiktok"] = {"user": tt.user_stats(), "videos": tt.video_stats()}
        except Exception as exc:  # noqa: BLE001
            snap["errors"].append(f"tiktok: {exc}")
    usage.save()
    for e in snap["errors"]:
        log(f"stats warning: {e}")
    return snap


def save_snapshot(snap: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snap['date']}.json"
    path.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_snapshots(directory: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


# ── Metrics ──────────────────────────────────────────────────────────────────
def _totals(snap: dict) -> dict:
    yt = snap.get("youtube") or {}
    tt = snap.get("tiktok") or {}
    ytv = (yt.get("videos") or {}).values()
    ttv = (tt.get("videos") or {}).values()
    return {
        "yt_views": sum(v.get("views", 0) for v in ytv),
        "tt_views": sum(v.get("views", 0) for v in ttv),
        "likes": sum(v.get("likes", 0) for v in ytv) + sum(v.get("likes", 0) for v in ttv),
        "comments": sum(v.get("comments", 0) for v in ytv) + sum(v.get("comments", 0) for v in ttv),
        "yt_subs": (yt.get("channel") or {}).get("subscribers", 0),
        "tt_followers": (tt.get("user") or {}).get("followers", 0),
    }


GAIN_KEYS = {"yt_views": "yt_views_gained", "tt_views": "tt_views_gained", "likes": "likes_gained",
             "comments": "comments_gained", "yt_subs": "subs_gained", "tt_followers": "followers_gained"}


PLAIN_REASONS = {
    "true peak": "The sound was too loud in places (it could crackle on phones).",
    "loudness": "The overall volume was too quiet or too loud.",
    "duration": "The video was too short or too long for a Short.",
    "no black frames": "Part of the picture was black.",
    "no dead air": "There was a long silence.",
    "hook starts immediately": "The voice didn't start right away (weak hook).",
    "captions cover every word": "Some words were missing from the subtitles.",
    "caption timing": "The subtitles were out of sync.",
    "fact check": "A fact couldn't be confirmed as up to date, so it wasn't safe to post.",
    "resolution": "The picture size was wrong.",
    "video codec": "The video file format was wrong.",
    "audio": "The audio format was wrong.",
    "file": "The video file was missing.",
}


def why_not_posted(v: dict) -> str | None:
    """A plain-English reason a video isn't (yet) on YouTube, or None if it's fine."""
    from .registry import MAX_ATTEMPTS

    yt = (v.get("platforms") or {}).get("youtube") or {}
    if yt.get("id"):
        return None
    if v.get("status") == "qa_failed":
        names = [f.split(":", 1)[0] for f in (v.get("qa") or {}).get("failures", [])]
        reasons = [PLAIN_REASONS.get(n, n) for n in names] or ["It failed an automatic quality check."]
        attempts = int(v.get("attempts", 1))
        nxt = (" It will be re-made automatically at the next 11:00 run." if attempts < MAX_ATTEMPTS
               else f" It was tried {attempts} times, so it has been dropped.")
        return " ".join(reasons) + nxt
    if (v.get("review") or {}).get("decision") == "rejected":
        return "You rejected it."
    if yt.get("last_error"):
        return "The YouTube upload failed: " + str(yt["last_error"])[:200] + " It will be retried at the next publish run."
    return None


def build_metrics(cfg, reg: Registry, snapshots: list[dict], today: date, demo: bool = False) -> dict:
    rendered_per_day: dict[str, int] = {}
    published_per_day: dict[str, int] = {}
    for v in reg.videos.values():
        d = str(v.get("rendered_at", ""))[:10]
        if d:
            rendered_per_day[d] = rendered_per_day.get(d, 0) + 1
        for p in (v.get("platforms") or {}).values():
            pd = str(p.get("published_at", ""))[:10]
            if pd and (p.get("id") or p.get("publish_id")):
                published_per_day[pd] = published_per_day.get(pd, 0) + 1

    dates = sorted({s["date"] for s in snapshots} | set(rendered_per_day) | set(published_per_day))
    by_date = {s["date"]: _totals(s) for s in snapshots}
    days, prev = [], None
    for d in dates:
        tot = by_date.get(d)
        if tot is None and prev is not None:
            tot = dict(prev)  # no snapshot that day: carry totals forward, zero gains
        tot = tot or {k: 0 for k in GAIN_KEYS}
        row = {"date": d, **tot}
        for k, g in GAIN_KEYS.items():
            row[g] = max(0, tot[k] - prev[k]) if prev is not None else 0
        row["rendered"] = rendered_per_day.get(d, 0)
        row["published"] = published_per_day.get(d, 0)
        days.append(row)
        prev = tot

    latest = snapshots[-1] if snapshots else {}
    yt_videos = ((latest.get("youtube") or {}).get("videos") or {})
    tt_videos = ((latest.get("tiktok") or {}).get("videos") or {})
    videos = []
    for v in sorted(reg.videos.values(), key=lambda r: r.get("rendered_at", ""), reverse=True):
        yt = (v.get("platforms") or {}).get("youtube", {})
        st = yt_videos.get(yt.get("id", ""), {}) if yt else {}
        tt_id = (v.get("platforms") or {}).get("tiktok", {}).get("video_id")
        tst = tt_videos.get(tt_id, {}) if tt_id else {}
        views = st.get("views", 0) + tst.get("views", 0)
        likes = st.get("likes", 0) + tst.get("likes", 0)
        comments = st.get("comments", 0) + tst.get("comments", 0)
        videos.append({
            "id": v["id"], "title": v.get("title", v["id"]), "rendered_at": v.get("rendered_at", ""),
            "status": v.get("status"), "duration": v.get("duration"), "qa_passed": (v.get("qa") or {}).get("passed"),
            "author": v.get("author"), "youtube_url": yt.get("url"), "tiktok": bool((v.get("platforms") or {}).get("tiktok")),
            "review_issue": (v.get("review") or {}).get("issue"),
            "why_not_posted": why_not_posted(v),
            "video_url": (v.get("review") or {}).get("video_url"),
            "sheet_url": (v.get("review") or {}).get("sheet_url"),
            "qa_failures": (v.get("qa") or {}).get("failures", []),
            "qa_warnings": (v.get("qa") or {}).get("warnings", []),
            "attempts": v.get("attempts", 1 if v.get("rendered_at") else 0),
            "scheduled_for": ((v.get("platforms") or {}).get("youtube") or {}).get("scheduled_for"),
            "theme": v.get("theme"), "format": v.get("format"),
            "views": views, "likes": likes, "comments": comments,
            "engagement": round((likes + comments) / views, 4) if views else 0.0,
        })

    statuses = [v.get("status") for v in reg.videos.values()]
    published = sum(1 for v in reg.videos.values() if any(
        p.get("id") or p.get("publish_id") for p in (v.get("platforms") or {}).values()))
    qa_known = [v for v in reg.videos.values() if v.get("qa")]
    usage = Usage(cfg.data_dir / "usage.json")
    return {
        "generated_at": now_iso(),
        "demo": demo,
        "repo": env("GITHUB_REPOSITORY") or cfg.get("channel.repository", "maltebrink-1/gta6-ai"),
        "channel": {"name": cfg.get("channel.name"), "handle": cfg.get("channel.handle")},
        "stage": {
            "publishing_enabled": bool(cfg.get("publishing.enabled")),
            "mode": cfg.get("publishing.mode"),
            "youtube": bool(cfg.get("publishing.youtube.enabled")) and has_env("YT_REFRESH_TOKEN"),
            "youtube_stats": bool((latest.get("youtube") or {}).get("channel")),
            "tiktok": bool(cfg.get("publishing.tiktok.enabled")) and has_env("TIKTOK_REFRESH_TOKEN"),
        },
        "days": days,
        "videos": videos,
        "pipeline": {
            "created": len(reg.videos),
            "published": published,
            "awaiting_review": statuses.count("awaiting_review"),
            "approved": statuses.count("approved"),
            "rejected": statuses.count("rejected"),
            "qa_failed": statuses.count("qa_failed"),
            "qa_pass_rate": round(sum(1 for v in qa_known if v["qa"].get("passed")) / len(qa_known), 3) if qa_known else None,
        },
        "usage": {
            "youtube_uploads": {"used": usage.get("youtube_uploads", today), "limit": int(cfg.get("limits.youtube_uploads_per_day", 100))},
            "youtube_units": {"used": usage.get("youtube_units", today), "limit": int(cfg.get("limits.youtube_units_per_day", 10000))},
            "gemini_requests": {"used": usage.get("gemini_requests", today), "limit": int(cfg.get("limits.gemini_requests_per_day", 20))},
            "actions": "Unlimited — public repository on standard GitHub-hosted runners",
        },
        "errors": latest.get("errors", []) if latest else [],
    }


def demo_metrics(cfg, today: date, days: int = 35, seed: int = 7) -> dict:
    """Clearly-labelled synthetic numbers so the dashboard can be previewed before launch."""
    rng = random.Random(seed)
    reg = Registry(Path("/nonexistent/videos.json"))
    snapshots = []
    yt_v = tt_v = likes = comments = subs = fol = 0
    titles = ["GTA 6 Is Finally Out", "Why GTA 6 Isn't on PC (Yet)", "The Trailer That Broke the Internet",
              "Meet Lucia & Jason", "The Whole GTA Story in Under a Minute", "What Critics Are Saying",
              "Welcome to Leonida", "GTA 6's Long Road", "Rockstar's Highest-Rated Game", "Vice City, Then and Now"]
    vid_stats = {}
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        n_new = 2 if i % 3 else 1
        for k in range(n_new):
            vid = f"demo-{i:02d}-{k}"
            reg.videos[vid] = {"id": vid, "title": titles[(i * 2 + k) % len(titles)], "rendered_at": f"{d}T10:0{k}:00Z",
                               "status": "published", "duration": 40, "qa": {"passed": True}, "author": "claude",
                               "platforms": {"youtube": {"id": vid, "url": "https://youtube.com/shorts/demo",
                                                         "published_at": f"{d}T17:00:00Z"}}}
            vid_stats[vid] = {"views": 0, "likes": 0, "comments": 0}
        growth = 1 + i / 6
        for vid, st in vid_stats.items():
            gain = int(rng.expovariate(1 / (120 * growth)))
            st["views"] += gain
            st["likes"] += int(gain * rng.uniform(0.03, 0.07))
            st["comments"] += int(gain * rng.uniform(0.002, 0.01))
        yt_v = sum(s["views"] for s in vid_stats.values())
        tt_new = int(yt_v * 0.35)
        likes = sum(s["likes"] for s in vid_stats.values())
        comments = sum(s["comments"] for s in vid_stats.values())
        subs += int(rng.uniform(4, 25) * growth)
        fol += int(rng.uniform(2, 18) * growth)
        snapshots.append({"date": d.isoformat(), "youtube": {"channel": {"subscribers": subs},
                                                             "videos": {k: dict(v) for k, v in vid_stats.items()}},
                          "tiktok": {"user": {"followers": fol}, "videos": {"tt": {"views": tt_new, "likes": 0, "comments": 0}}}})
    m = build_metrics(cfg, reg, snapshots, today, demo=True)
    m["stage"]["youtube_stats"] = False  # demo numbers never mean a real connection
    m["usage"]["youtube_uploads"]["used"] = 2
    m["usage"]["youtube_units"]["used"] = 7
    m["usage"]["gemini_requests"]["used"] = 3
    return m
