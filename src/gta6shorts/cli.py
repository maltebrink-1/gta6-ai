"""Command line: `python -m gta6shorts <command>` (used by the GitHub Actions workflows)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import env, has_env, load_config


def today_for(cfg) -> date:
    tz = cfg.get("analytics.timezone", "UTC")
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except Exception:  # noqa: BLE001
        return date.today()


def step_summary(markdown: str) -> None:
    """Append to the GitHub Actions run summary (visible in the GitHub mobile app)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


# ── commands ─────────────────────────────────────────────────────────────────
def cmd_doctor(cfg, args) -> int:
    from .facts import validate_factbase
    from .pipeline import load_factbase
    from .script import load_scripts

    rows = []

    def row(name, ok, detail):
        rows.append((name, ok, detail))

    row("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "install ffmpeg")
    row("espeak-ng (fallback voice)", bool(shutil.which("espeak-ng")), "optional")
    model = cfg.model_dir / cfg.get("voice.kokoro_model", "kokoro-v1.0.onnx")
    row("Kokoro voice model", model.exists(), str(model) if model.exists() else "downloads automatically on first render")
    try:
        import kokoro_onnx  # noqa: F401
        row("kokoro-onnx package", True, "installed")
    except ImportError:
        row("kokoro-onnx package", False, "pip install kokoro-onnx")
    fb = load_factbase(cfg)
    errs = validate_factbase(fb)
    row("fact base", not errs, f"{len(fb)} facts" + (f"; {len(errs)} problems" if errs else ""))
    row("scripts", True, f"{len(load_scripts(cfg.scripts_dir))} in content/scripts")
    row("publishing master switch", True, "ON" if cfg.get("publishing.enabled") else "OFF (safe — Stage 1/2)")
    row("YouTube secrets", has_env("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"), "YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN")
    row("TikTok secrets", has_env("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"), "needs an approved TikTok developer app")
    row("Gemini (optional writer)", has_env("GEMINI_API_KEY"), "GEMINI_API_KEY")
    row("OpenAI-compatible (optional writer)", has_env("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"), "LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
    row("Pexels (optional stock footage)", has_env("PEXELS_API_KEY"), "PEXELS_API_KEY")
    row("GitHub token (review issues)", has_env("GITHUB_TOKEN", "GITHUB_REPOSITORY"), "provided automatically inside GitHub Actions")
    width = max(len(r[0]) for r in rows)
    for name, ok, detail in rows:
        print(f"{'✅' if ok else '⬜'} {name.ljust(width)}  {detail}")
    step_summary("## 🩺 Doctor\n\n| Check | Status | Detail |\n|---|---|---|\n" +
                 "\n".join(f"| {n} | {'✅' if ok else '⬜'} | {d} |" for n, ok, d in rows))
    return 0


def cmd_validate(cfg, args) -> int:
    from .factcheck import check_with_config, errors, format_issues
    from .facts import validate_factbase
    from .pipeline import load_factbase
    from .script import load_scripts

    today = today_for(cfg)
    fb = load_factbase(cfg)
    problems = validate_factbase(fb)
    for p in problems:
        print(f"[FACTS] {p}")
    scripts = load_scripts(cfg.scripts_dir)
    if args.only:
        scripts_to_check = [s for s in scripts if s.id in args.only or (s.path and s.path.name in args.only)]
    else:
        scripts_to_check = scripts
    failed = 0
    report = ["## ✅ Script validation", ""]
    for s in scripts_to_check:
        issues = check_with_config(s, fb, cfg, today=today, history=[o for o in scripts if o.id != s.id],
                                   strict_freshness=args.strict)
        errs = errors(issues)
        failed += bool(errs)
        status = "❌" if errs else ("⚠️" if issues else "✅")
        print(f"{status} {s.id} ({s.word_count} words, ~{s.estimated_seconds():.0f}s)")
        if issues:
            print("   " + format_issues(issues).replace("\n", "\n   "))
        report.append(f"- {status} `{s.id}` — {s.title}" + (f"\n  ```\n  {format_issues(issues)}\n  ```" if issues else ""))
    step_summary("\n".join(report))
    return 1 if (failed or problems) else 0


def cmd_facts_verify(cfg, args) -> int:
    from .facts import verify_facts
    from .pipeline import load_factbase
    from .research import fetch_text

    fb = load_factbase(cfg)
    results = verify_facts(fb, fetch_text, today_for(cfg))
    path = cfg.data_dir / "fact_checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["## 🔎 Fact re-verification", "", "| Fact | Result | Detail |", "|---|---|---|"]
    bad = 0
    for fid, r in sorted(results.items()):
        icon = "✅" if r["ok"] else ("❓" if r["ok"] is None else "❌")
        bad += r["ok"] is False
        lines.append(f"| {fid} | {icon} | {r['detail']} |")
        print(f"{icon} {fid}: {r['detail']}")
    step_summary("\n".join(lines))
    return 1 if bad else 0


def cmd_research(cfg, args) -> int:
    from .research import collect_leads, save_leads

    leads, errs = collect_leads(cfg.get("research.feeds", []), max_items=int(cfg.get("research.max_items", 40)))
    path = save_leads(leads, cfg.data_dir / "research", today_for(cfg))
    print(f"{len(leads)} leads → {path}")
    for e in errs:
        print(f"feed error: {e}")
    step_summary("## 📰 News leads (not facts)\n\n" + "\n".join(f"- [{l['title']}]({l['link']})" for l in leads[:15]))
    return 0


def cmd_write(cfg, args) -> int:
    from .llm import available_backends
    from .pipeline import load_factbase
    from .research import latest_leads
    from .script import load_scripts
    from .writer import write_new_scripts

    fb = load_factbase(cfg)
    today = today_for(cfg)
    names = [b for b in cfg.get("writer.backends", []) if b not in ("queue", "template")]
    backends = [] if args.backend == "template" else available_backends(names if args.backend == "auto" else [args.backend], cfg)
    made, log = write_new_scripts(args.count, fb, cfg, today, load_scripts(cfg.scripts_dir), backends=backends,
                                  allow_template=args.backend in ("auto", "template"),
                                  news=latest_leads(cfg.data_dir / "research"))
    for line in log:
        print(line)
    for s in made:
        path = cfg.scripts_dir / f"{s.id}.yaml"
        if args.dry_run:
            print(json.dumps(s.to_dict(), indent=1, ensure_ascii=False))
        else:
            s.save(path)
            print(f"saved {path}")
    return 0 if made else 1


def cmd_render(cfg, args) -> int:
    from .pipeline import produce

    summary = produce(cfg, count=len(args.ids), today=today_for(cfg), engine_name=args.engine, only=args.ids,
                      open_reviews=False, allow_write=False, strict_facts=args.strict, record=False)
    print(summary.markdown())
    step_summary(summary.markdown())
    return 0 if all(r.get("qa_passed") for r in summary.rendered) and summary.rendered else 1


def cmd_produce(cfg, args) -> int:
    from .pipeline import produce

    count = args.count if args.count is not None else int(cfg.get("production.videos_per_day", 2))
    summary = produce(cfg, count=count, today=today_for(cfg), engine_name=args.engine,
                      open_reviews=not args.no_review, allow_write=not args.no_write)
    print(summary.markdown())
    step_summary(summary.markdown())
    return 0 if all(r.get("qa_passed") for r in summary.rendered) else 1


def cmd_review_label(cfg, args) -> int:
    from .pipeline import handle_review_label

    msg = handle_review_label(cfg, args.issue, args.label, today_for(cfg))
    print(msg)
    step_summary(f"## 🗳️ Review decision on #{args.issue}\n\n{msg}")
    return 0


def cmd_publish_pending(cfg, args) -> int:
    from .pipeline import publish_pending

    lines = publish_pending(cfg, today_for(cfg))
    print("\n".join(lines) or "nothing to publish")
    step_summary("## 🚀 Publish run\n\n" + ("\n".join(f"- {l}" for l in lines) or "Nothing to publish."))
    return 0


def cmd_stats(cfg, args) -> int:
    from .analytics import collect_snapshot, save_snapshot
    from .registry import Registry

    reg = Registry(cfg.data_dir / "videos.json")
    snap = collect_snapshot(cfg, reg, today_for(cfg))
    path = save_snapshot(snap, cfg.data_dir / "stats")
    print(f"snapshot → {path}")
    return 0


def cmd_report(cfg, args) -> int:
    from .analytics import build_metrics, demo_metrics, load_snapshots
    from .dashboard import write_dashboard
    from .pipeline import load_factbase
    from .registry import Registry
    from .report import daily_report

    today = today_for(cfg)
    reg = Registry(cfg.data_dir / "videos.json")
    metrics = demo_metrics(cfg, today) if args.demo else build_metrics(cfg, reg, load_snapshots(cfg.data_dir / "stats"), today)
    site = Path(args.site) if args.site else cfg.path("site")
    index = write_dashboard(metrics, site)
    print(f"dashboard → {index}")
    if not args.demo:
        md = daily_report(metrics, load_factbase(cfg), today)
        out = cfg.path("reports", "daily", f"{today.isoformat()}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(md)
        step_summary(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gta6shorts", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="show what is installed and configured")
    p = sub.add_parser("validate", help="fact-check the fact base and all scripts")
    p.add_argument("--strict", action="store_true", help="treat stale facts as errors (publishing rules)")
    p.add_argument("only", nargs="*", help="script ids or file names to check (default: all)")
    sub.add_parser("facts-verify", help="re-check facts against their source pages")
    sub.add_parser("research", help="collect news headlines as topic leads")
    p = sub.add_parser("write", help="draft new scripts")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--backend", default="auto", choices=["auto", "gemini", "openai", "template"])
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("render", help="render specific scripts (no review issue, for testing)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--strict", action="store_true", help="also require every fact to be freshly verified")
    p.add_argument("--engine", choices=["auto", "kokoro", "espeak"], default=None)
    p = sub.add_parser("produce", help="daily job: pick/write scripts, render, QA, open review issues")
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--engine", choices=["auto", "kokoro", "espeak"], default=None)
    p.add_argument("--no-review", action="store_true")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("review-label", help="handle an approved/rejected label on a review issue")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--label", required=True)
    sub.add_parser("publish-pending", help="Stage 4: publish approved (or, in auto mode, all QA-passed) videos")
    sub.add_parser("stats", help="collect today's YouTube/TikTok statistics")
    p = sub.add_parser("report", help="build the dashboard and the daily report")
    p.add_argument("--demo", action="store_true", help="use clearly-labelled demo numbers")
    p.add_argument("--site", default=None, help="output folder for the dashboard (default: site/)")
    args = parser.parse_args(argv)
    cfg = load_config()
    handler = {
        "doctor": cmd_doctor, "validate": cmd_validate, "facts-verify": cmd_facts_verify, "research": cmd_research,
        "write": cmd_write, "render": cmd_render, "produce": cmd_produce, "review-label": cmd_review_label,
        "publish-pending": cmd_publish_pending, "stats": cmd_stats, "report": cmd_report,
    }[args.cmd]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
