"""High-level jobs: produce (script → video → QA → review) and publish."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import env
from .factcheck import check_with_config, errors, format_issues
from .facts import FactBase, load_facts
from .gate import publish_blockers
from .llm import available_backends
from .metadata import tiktok_caption
from .qa import run_qa
from .registry import Registry
from .render import render_script
from .research import latest_leads
from .review import GitHub, issue_body, load_meta, video_id_from_issue
from .script import Script, load_scripts
from .stock import PexelsProvider
from .tts import make_engine
from .usage import Usage
from .writer import write_new_scripts


def load_factbase(cfg) -> FactBase:
    return load_facts(cfg.facts_file, cfg.data_dir / "fact_checks.json", int(cfg.get("facts.default_recheck_days", 14)))


@dataclass
class ProduceSummary:
    rendered: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        out = ["## 🎬 Produce run", ""]
        if self.rendered:
            out += ["| Video | Length | QA | Voice | Review |", "|---|---|---|---|---|"]
            for r in self.rendered:
                qa = "✅" if r.get("qa_passed") else f"❌ {'; '.join(r.get('failures', []))[:120]}"
                out.append(f"| {r['title']} | {r.get('duration', '?')} s | {qa} | {r.get('engine')} | "
                           f"{('#' + str(r['issue'])) if r.get('issue') else '—'} |")
        else:
            out.append("No videos rendered.")
        if self.written:
            out += ["", "**New scripts written:** " + ", ".join(self.written)]
        if self.skipped:
            out += ["", "**Skipped:**", *[f"- {s}" for s in self.skipped]]
        if self.log:
            out += ["", "<details><summary>Log</summary>", "", "```", *self.log[-60:], "```", "</details>"]
        return "\n".join(out)


def produce(cfg, *, count: int, today: date, engine_name: str | None = None, only: list[str] | None = None,
            open_reviews: bool = True, allow_write: bool = True, strict_facts: bool = True, record: bool = True,
            log=print) -> ProduceSummary:
    summary = ProduceSummary()

    def say(msg: str) -> None:
        summary.log.append(msg)
        log(msg)

    fb = load_factbase(cfg)
    reg = Registry(cfg.data_dir / "videos.json")
    scripts = load_scripts(cfg.scripts_dir)
    by_id = {s.id: s for s in scripts}
    if only:
        queue = [by_id[i] for i in only if i in by_id]
        summary.skipped += [f"{i}: no such script" for i in only if i not in by_id]
    else:
        queue = [s for s in scripts if s.status == "ready" and s.id not in reg.videos]

    valid: list[Script] = []
    for s in queue:
        history = [o for o in scripts if o.id != s.id]
        errs = errors(check_with_config(s, fb, cfg, today=today, history=history, strict_freshness=strict_facts))
        if errs:
            summary.skipped.append(f"{s.id}: fails fact check — {format_issues(errs)[:300]}")
            say(f"skip {s.id}: {len(errs)} fact-check errors")
        else:
            valid.append(s)

    need = count - len(valid)
    if need > 0 and allow_write and not only:
        names = [b for b in cfg.get("writer.backends", []) if b not in ("queue", "template")]
        backends = available_backends(names, cfg)
        allow_template = "template" in cfg.get("writer.backends", [])
        new, wlog = write_new_scripts(need, fb, cfg, today, scripts, backends=backends,
                                      allow_template=allow_template, news=latest_leads(cfg.data_dir / "research"))
        for line in wlog:
            say(line)
        usage = Usage(cfg.data_dir / "usage.json")
        usage.add("gemini_requests", sum(1 for line in wlog if line.startswith("gemini")), today)
        usage.save()
        for s in new:
            s.save(cfg.scripts_dir / f"{s.id}.yaml")
            summary.written.append(s.id)
            valid.append(s)

    if not valid:
        say("nothing to render")
        return summary

    engine = make_engine(cfg, engine_name)
    say(f"voice engine: {engine.name}")
    stock = None
    if cfg.get("visuals.stock_footage", "auto") == "auto" and env("PEXELS_API_KEY"):
        stock = PexelsProvider(env("PEXELS_API_KEY"), Path.home() / ".cache" / "gta6shorts" / "stock")
    previous_theme = None
    rendered_recs = sorted(reg.videos.values(), key=lambda v: v.get("rendered_at", ""))
    if rendered_recs:
        previous_theme = rendered_recs[-1].get("theme")

    gh = None
    repo, token = env("GITHUB_REPOSITORY"), env("GITHUB_TOKEN")
    if open_reviews and cfg.get("review.github_issues", True) and repo and token:
        gh = GitHub(token, repo)
        gh.ensure_labels()

    for s in valid[:count]:
        entry = {"id": s.id, "title": s.title}
        try:
            history = [o for o in load_scripts(cfg.scripts_dir) if o.id != s.id]
            result = render_script(s, fb, cfg, engine, cfg.out_dir, today=today, previous_theme=previous_theme,
                                   stock_provider=stock, log=say)
            previous_theme = result.theme
            qa = run_qa(result.video, s, fb, cfg, today=today, history=history, strict_facts=strict_facts)
            (result.out_dir / "qa.json").write_text(json.dumps(qa.to_dict(), indent=1), encoding="utf-8")
            meta = load_meta(result.out_dir)
            rec = reg.record_render(s, result, qa, meta) if record else {"id": s.id, **meta}
            if record and s.path:
                rec["script"] = str(Path(s.path).resolve().relative_to(cfg.root.resolve()))
            entry.update(duration=result.duration, engine=result.engine, qa_passed=qa.passed,
                         failures=[f"{c.name}: {c.detail}" for c in qa.failures])
            say(qa.summary().splitlines()[0])
            if gh and qa.passed:
                release = gh.ensure_release(cfg.get("review.release_tag", "review-queue"))
                video_url = gh.upload_asset(release, result.video, f"{s.id}.mp4", "video/mp4")
                sheet_url = gh.upload_asset(release, result.out_dir / "contact_sheet.jpg", f"{s.id}-sheet.jpg", "image/jpeg")
                body = issue_body(rec, s, meta, qa, video_url, sheet_url, bool(cfg.get("publishing.enabled")))
                number = gh.create_issue(f"🎬 Review: {s.title}", body, ["review"])
                reg.set_review(s.id, issue=number, video_url=video_url, sheet_url=sheet_url)
                entry["issue"] = number
        except Exception as exc:  # noqa: BLE001 - one broken video must not stop the others
            entry.update(qa_passed=False, failures=[f"render error: {exc}"[:300]])
            say(f"ERROR {s.id}: {exc}")
        summary.rendered.append(entry)
        if record:
            reg.save()
    if stock:
        usage = Usage(cfg.data_dir / "usage.json")
        usage.add("pexels_requests", stock.requests_made, today)
        usage.save()
    return summary


# ── Publishing ───────────────────────────────────────────────────────────────
def _facts_ok(cfg, record: dict, today: date) -> bool:
    path = cfg.path(record.get("script", ""))
    if not path.exists():
        return False
    script = Script.load(path)
    return not errors(check_with_config(script, load_factbase(cfg), cfg, today=today, strict_freshness=True))


def release_time(cfg, reg: Registry, now=None):
    """When the next YouTube video should go public (UTC ISO), or None for "right now".

    Videos are spaced publishing.youtube.spacing_minutes apart (default 30): the next one
    goes public that many minutes after the latest YouTube release (done or scheduled),
    or immediately if that moment is (almost) here already.
    """
    from datetime import datetime, timedelta, timezone

    gap = timedelta(minutes=int(cfg.get("publishing.youtube.spacing_minutes", 30)))
    now = now or datetime.now(timezone.utc)
    last = None
    for v in reg.videos.values():
        p = (v.get("platforms") or {}).get("youtube") or {}
        if not p.get("id"):
            continue
        stamp = p.get("scheduled_for") or p.get("published_at")
        try:
            t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        last = t if last is None or t > last else last
    if last is None or last + gap <= now + timedelta(minutes=5):
        return None
    return (last + gap).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_one(cfg, reg: Registry, vid: str, platform: str, today: date, gh: GitHub | None, log=print) -> tuple[bool, str]:
    rec = reg.get(vid)
    if rec is None:
        return False, f"unknown video {vid}"
    blockers = publish_blockers(rec, platform, cfg, published_today=reg.published_on(today.isoformat(), platform),
                                facts_ok=_facts_ok(cfg, rec, today))
    if blockers:
        return False, "; ".join(blockers)
    work = cfg.out_dir / vid
    work.mkdir(parents=True, exist_ok=True)
    video = work / "video.mp4"
    if not video.exists():
        url = (rec.get("review") or {}).get("video_url")
        if not (gh and url):
            return False, "video file not available (no local file and no review-queue asset)"
        gh.download(url, video)
    meta_path = work / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    script = Script.load(cfg.path(rec["script"]))
    usage = Usage(cfg.data_dir / "usage.json")
    try:
        if platform == "youtube":
            from .youtube import YouTubeClient

            yt = YouTubeClient(env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET"), env("YT_REFRESH_TOKEN"))
            privacy_setting = cfg.get("publishing.youtube.privacy", "private")
            publish_at = (release_time(cfg, reg)
                          if privacy_setting == "public" else None)
            body = YouTubeClient.build_body(
                meta.get("title", script.title), meta.get("description", script.description), script.tags,
                category_id=str(cfg.get("publishing.youtube.category_id", "20")),
                privacy=privacy_setting,
                made_for_kids=bool(cfg.get("publishing.youtube.made_for_kids", False)),
                synthetic=bool(cfg.get("publishing.youtube.contains_synthetic_media", True)),
                publish_at=publish_at)
            result = yt.upload(video, body)
            usage.add("youtube_uploads", 1, today)
            privacy = result.get("status", {}).get("privacyStatus")
            extra = {"scheduled_for": publish_at} if publish_at else {}
            reg.mark_published(vid, "youtube", id=result["id"], url=f"https://youtube.com/shorts/{result['id']}",
                               privacy="scheduled" if publish_at else privacy, **extra)
            when = f"goes public at {publish_at} (UTC)" if publish_at else f"privacy: {privacy}"
            msg = f"YouTube: https://youtube.com/shorts/{result['id']} ({when})"
        elif platform == "tiktok":
            from .tiktok import TikTokClient

            tt = TikTokClient(env("TIKTOK_CLIENT_KEY"), env("TIKTOK_CLIENT_SECRET"), env("TIKTOK_REFRESH_TOKEN"))
            if cfg.get("publishing.tiktok.mode", "inbox") == "direct":
                pid = tt.direct_post(video, meta.get("tiktok_caption", tiktok_caption(script)),
                                     cfg.get("publishing.tiktok.privacy_level", "SELF_ONLY"),
                                     bool(cfg.get("publishing.tiktok.is_aigc", True)))
                msg = f"TikTok direct post submitted (publish_id {pid})"
            else:
                pid = tt.upload_to_inbox(video)
                msg = f"TikTok: sent to your TikTok inbox/drafts (publish_id {pid}) — open TikTok and tap Post"
            reg.mark_published(vid, "tiktok", publish_id=pid, mode=cfg.get("publishing.tiktok.mode", "inbox"))
            if tt.rotated_refresh_token:
                msg += " ⚠️ TikTok issued a new refresh token: update the TIKTOK_REFRESH_TOKEN secret."
        else:
            return False, f"unknown platform {platform}"
    except Exception as exc:  # noqa: BLE001
        reg.mark_publish_failed(vid, platform, str(exc))
        return False, f"{platform} upload failed: {exc}"
    finally:
        usage.save()
    log(msg)
    return True, msg


def handle_review_label(cfg, issue_number: int, label: str, today: date, log=print) -> str:
    """Called by the publish workflow when you label a review issue."""
    gh = GitHub(env("GITHUB_TOKEN"), env("GITHUB_REPOSITORY"))
    issue = gh.get_issue(issue_number)
    vid = video_id_from_issue(issue.get("body", ""))
    reg = Registry(cfg.data_dir / "videos.json")
    if not vid or vid not in reg.videos:
        return f"issue #{issue_number} is not a known review issue"
    if label == "rejected":
        reg.decide(vid, "rejected", issue.get("user", {}).get("login", ""))
        release = gh.ensure_release(cfg.get("review.release_tag", "review-queue"))
        gh.delete_asset(release, f"{vid}.mp4")
        gh.delete_asset(release, f"{vid}-sheet.jpg")
        reg.save()
        gh.comment(issue_number, "🗑️ Rejected — the video was removed from the review queue and will not be published.")
        gh.close_issue(issue_number, "not_planned")
        return f"{vid} rejected"
    if label != "approved":
        return f"label {label!r} ignored"
    reg.decide(vid, "approved")
    if not cfg.get("publishing.publish_on_approval", False):
        reg.save()
        when = cfg.get("publishing.daily_time", "13:00")
        note = (f"will be uploaded at the next daily publish run ({when} Danish time)."
                if cfg.get("publishing.enabled") else "publishing is still OFF, so nothing will be uploaded yet.")
        gh.comment(issue_number, f"Approved 👍 — {note}")
        return f"{vid} approved; {note}"
    results = []
    for platform in ("youtube", "tiktok"):
        ok, msg = publish_one(cfg, reg, vid, platform, today, gh, log)
        results.append(("✅ " if ok else "⏸️ ") + f"**{platform}** — {msg}")
    reg.save()
    published = any(r.startswith("✅") for r in results)
    gh.comment(issue_number, "Approved 👍\n\n" + "\n".join(results))
    if published:
        gh.add_labels(issue_number, ["published"])
        gh.close_issue(issue_number, "completed")
    return "\n".join(results)


def publish_pending(cfg, today: date, log=print) -> list[str]:
    """Stage 4 (mode: auto) — publish every QA-passed video that isn't published yet."""
    reg = Registry(cfg.data_dir / "videos.json")
    gh = GitHub(env("GITHUB_TOKEN"), env("GITHUB_REPOSITORY")) if env("GITHUB_TOKEN") and env("GITHUB_REPOSITORY") else None
    out = []
    statuses = ("approved", "awaiting_review") if cfg.get("publishing.mode") == "auto" else ("approved",)
    for rec in sorted(reg.by_status(*statuses), key=lambda r: r.get("rendered_at", "")):
        for platform in ("youtube", "tiktok"):
            ok, msg = publish_one(cfg, reg, rec["id"], platform, today, gh, log)
            out.append(f"{rec['id']} → {platform}: {'OK' if ok else 'skipped'} — {msg}")
    reg.save()
    return out
