"""The publishing safety gate. Every upload must pass ALL of these checks.

Nothing is published unless you have explicitly switched publishing on in
config/settings.yaml AND the platform is enabled AND credentials exist AND the
video passed QA AND (in review mode) you approved it — plus daily caps,
fresh facts and voice/script quality rules.
"""
from __future__ import annotations

from .config import env

CREDENTIALS = {
    "youtube": ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"),
}


def publish_blockers(record: dict, platform: str, cfg, *, published_today: int, facts_ok: bool) -> list[str]:
    """Return the reasons this video may NOT be published (empty list = allowed)."""
    reasons: list[str] = []
    if platform not in CREDENTIALS:
        return [f"unknown platform {platform!r}"]
    if not cfg.get("publishing.enabled", False):
        reasons.append("publishing.enabled is false (master switch — Stage 3 not switched on)")
    if not cfg.get(f"publishing.{platform}.enabled", False):
        reasons.append(f"publishing.{platform}.enabled is false")
    missing = [name for name in CREDENTIALS[platform] if not env(name)]
    if missing:
        reasons.append(f"missing secrets: {', '.join(missing)}")
    if not (record.get("qa") or {}).get("passed"):
        reasons.append("video did not pass QA")
    mode = cfg.get("publishing.mode", "review")
    decision = (record.get("review") or {}).get("decision")
    if decision == "rejected":
        reasons.append("video was rejected in review")
    elif mode != "auto" and decision != "approved":
        reasons.append("waiting for your approval (add the 'approved' label to its review issue)")
    if record.get("engine") == "espeak" and not cfg.get("voice.allow_espeak_for_publishing", False):
        reasons.append("uses the robotic fallback voice (test quality only)")
    if record.get("author") == "template" and not cfg.get("writer.allow_template_for_publishing", False):
        reasons.append("template-written script (review-only by policy)")
    if (record.get("platforms") or {}).get(platform, {}).get("id") or \
            (record.get("platforms") or {}).get(platform, {}).get("publish_id"):
        reasons.append(f"already published to {platform}")
    cap = int(cfg.get("publishing.max_per_day", 3))
    if published_today >= cap:
        reasons.append(f"daily cap reached ({published_today}/{cap})")
    if not facts_ok:
        reasons.append("facts need re-verification before publishing")
    return reasons
