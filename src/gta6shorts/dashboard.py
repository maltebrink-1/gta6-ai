"""Builds the static dashboard (site/index.html + site/metrics.json).

The page is self-contained (no external scripts, works offline and on phones),
so it can be hosted for free on GitHub Pages or simply opened as a file.
"""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def render_dashboard(metrics: dict) -> str:
    payload = json.dumps(metrics, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")  # can't break out of the <script> tag
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__METRICS__*/", payload)


def write_dashboard(metrics: dict, site_dir: Path) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False), encoding="utf-8")
    index = site_dir / "index.html"
    index.write_text(render_dashboard(metrics), encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    static = Path(__file__).resolve().parents[2] / "site_static"
    if static.is_dir():  # legal pages, TikTok connect/callback pages, verification files
        for f in static.rglob("*"):
            if f.is_file():
                dest = site_dir / f.relative_to(static)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read_bytes())
    return index
