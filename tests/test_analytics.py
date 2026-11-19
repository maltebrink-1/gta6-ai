from __future__ import annotations

import json
from datetime import date

from conftest import TODAY
from gta6shorts.analytics import build_metrics, demo_metrics
from gta6shorts.dashboard import render_dashboard, write_dashboard
from gta6shorts.facts import load_facts
from gta6shorts.registry import Registry
from gta6shorts.report import compact, daily_report
from gta6shorts.research import parse_rss, relevant


def snap(day, yt_views, subs, tt_views=0, fol=0):
    return {"date": day, "youtube": {"channel": {"subscribers": subs}, "videos": {"v1": {"views": yt_views, "likes": yt_views // 10, "comments": 1}}},
            "tiktok": {"user": {"followers": fol}, "videos": {"t1": {"views": tt_views, "likes": 0, "comments": 0}}}}


def make_reg(tmp_path):
    reg = Registry(tmp_path / "videos.json")
    reg.videos["v-1"] = {"id": "v-1", "title": "First <script>", "rendered_at": "2026-11-18T10:00:00Z", "status": "published",
                         "qa": {"passed": True}, "platforms": {"youtube": {"id": "v1", "url": "https://youtube.com/shorts/v1",
                                                                            "published_at": "2026-11-18T17:00:00Z"}}}
    reg.videos["v-2"] = {"id": "v-2", "title": "Second", "rendered_at": "2026-11-19T10:00:00Z", "status": "awaiting_review",
                         "qa": {"passed": True}, "platforms": {}}
    return reg


def test_metrics_daily_gains(cfg, tmp_path):
    reg = make_reg(tmp_path)
    m = build_metrics(cfg, reg, [snap("2026-11-18", 100, 10, 50, 5), snap("2026-11-19", 250, 14, 80, 9)], TODAY)
    d18, d19 = m["days"]
    assert d19["yt_views_gained"] == 150 and d19["tt_views_gained"] == 30
    assert d19["subs_gained"] == 4 and d19["followers_gained"] == 4
    assert d18["published"] == 1 and d19["rendered"] == 1
    assert m["pipeline"] == {"created": 2, "published": 1, "awaiting_review": 1, "approved": 0, "rejected": 0,
                             "qa_failed": 0, "qa_pass_rate": 1.0}
    best = [v for v in m["videos"] if v["views"]]
    assert best[0]["views"] == 250 and best[0]["engagement"] > 0


def test_report_mentions_attention_items(cfg, tmp_path):
    reg = make_reg(tmp_path)
    m = build_metrics(cfg, reg, [snap("2026-11-18", 100, 10), snap("2026-11-19", 250, 14)], TODAY)
    md = daily_report(m, load_facts(cfg.facts_file), TODAY)
    assert "Daily report — 2026-11-19" in md
    assert "waiting for your review" in md
    assert "Publishing is OFF" in md


def test_dashboard_escapes_untrusted_titles(cfg, tmp_path):
    reg = make_reg(tmp_path)
    reg.videos["v-1"]["title"] = "</script><img src=x onerror=alert(1)>"
    m = build_metrics(cfg, reg, [snap("2026-11-19", 1, 1)], TODAY)
    html = render_dashboard(m)
    assert "</script><img" not in html
    start = html.index('<script id="metrics" type="application/json">') + len('<script id="metrics" type="application/json">')
    payload = html[start: html.index("</script>", start)]
    titles = {v["id"]: v["title"] for v in json.loads(payload)["videos"]}
    assert titles["v-1"].startswith("</script>")  # data survives intact, but can't break out of the tag


def test_demo_dashboard_is_labelled(cfg, tmp_path):
    m = demo_metrics(cfg, date(2026, 11, 19))
    assert m["demo"] is True and m["stage"]["youtube_stats"] is False
    index = write_dashboard(m, tmp_path / "site")
    assert index.exists() and (tmp_path / "site" / "metrics.json").exists()
    assert "Demo data" in index.read_text()


def test_compact_numbers():
    assert compact(950) == "950" and compact(12_900) == "12.9K" and compact(4_200_000) == "4.2M" and compact(None) == "—"


def test_rss_parsing_and_relevance():
    xml = """<rss><channel>
      <item><title>GTA 6 launch breaks records</title><link>https://n/1</link><pubDate>Thu, 19 Nov 2026 09:00:00 GMT</pubDate>
        <source url="https://pub">Pub</source></item>
      <item><title>Unrelated cooking news</title><link>https://n/2</link></item>
    </channel></rss>"""
    items = parse_rss(xml, "feed")
    assert items[0]["published"] == "2026-11-19" and items[0]["source"] == "Pub"
    assert [relevant(i) for i in items] == [True, False]
