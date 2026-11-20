from __future__ import annotations

from datetime import date

from gta6shorts.facts import load_facts
from gta6shorts.insights import build_insights, weekly_report
from gta6shorts.registry import Registry


def _reg(tmp_path, n):
    reg = Registry(tmp_path / "videos.json")
    facts = ["gta6_metacritic_99", "trailer1_record", "gta6_protagonists"]
    for i in range(n):
        reg.videos[f"v{i}"] = {"id": f"v{i}", "title": f"Video {i}", "format": "list" if i % 2 else "news",
                               "theme": "neon_night", "duration": 40, "fact_ids": [facts[i % 3]],
                               "platforms": {"youtube": {"id": f"y{i}", "published_at": "2026-11-20T12:00:00+00:00"}}}
    snap = {"youtube": {"videos": {f"y{i}": {"views": (1000 if i % 3 == 1 else 100), "likes": 10, "comments": 1}
                                   for i in range(n)}}}
    return reg, snap


def test_insights_rank_best_topic(cfg, tmp_path):
    reg, snap = _reg(tmp_path, 9)
    ins = build_insights(reg, snap, load_facts(cfg.facts_file), date(2026, 11, 27))
    assert ins["enough_data"] is True
    assert ins["topics"][0]["name"] == "trailers"          # the 1000-view videos
    assert ins["top_videos"][0]["views"] == 1000
    assert "trailers" in ins["advice"]
    assert "Weekly learnings" in weekly_report(ins, date(2026, 11, 29))


def test_insights_too_little_data(cfg, tmp_path):
    reg, snap = _reg(tmp_path, 3)
    ins = build_insights(reg, snap, load_facts(cfg.facts_file), date(2026, 11, 21))
    assert ins["enough_data"] is False and "too early" in ins["advice"]
