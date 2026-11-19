from __future__ import annotations

from datetime import date

from conftest import TODAY, make_script
from gta6shorts.factcheck import check_script, check_with_config, errors
from gta6shorts.facts import load_facts, validate_factbase
from gta6shorts.script import load_scripts
from gta6shorts.textutil import extract_numbers


def codes(issues, level="error"):
    return {i.code for i in issues if i.level == level}


def test_clean_script_passes(fb):
    s = make_script([{"text": "It holds a 99 on Metacritic.", "facts": ["meta99"]}])
    assert errors(check_script(s, fb, today=TODAY)) == []


def test_wrong_number_is_caught(fb):
    s = make_script([{"text": "It holds a 98 on Metacritic.", "facts": ["meta99"]}])
    assert "number_mismatch" in codes(check_script(s, fb, today=TODAY))


def test_number_without_fact_is_caught(fb):
    s = make_script([{"text": "The map has 500 missions.", "kind": "opinion"}])
    assert "unsourced_number" in codes(check_script(s, fb, today=TODAY))


def test_claim_without_fact_is_caught(fb):
    s = make_script([{"text": "The map is the biggest ever.", "kind": "claim"}])
    assert "unsourced_claim" in codes(check_script(s, fb, today=TODAY))


def test_unknown_fact_is_caught(fb):
    s = make_script([{"text": "Something true.", "facts": ["nope"]}])
    assert "unknown_fact" in codes(check_script(s, fb, today=TODAY))


def test_attribution_required(fb):
    bad = make_script([{"text": "Trailer 2 hit 475 million views in 24 hours.", "facts": ["views"]}])
    good = make_script([{"text": "Rockstar says Trailer 2 hit 475 million views in 24 hours.", "facts": ["views"]}])
    assert "missing_attribution" in codes(check_script(bad, fb, today=TODAY))
    assert errors(check_script(good, fb, today=TODAY)) == []


def test_reported_fact_needs_attribution(fb):
    bad = make_script([{"text": "The controls are clunky.", "facts": ["rep"]}])
    good = make_script([{"text": "Some reviewers say the controls are clunky.", "facts": ["rep"]}])
    assert "missing_attribution" in codes(check_script(bad, fb, today=TODAY))
    assert errors(check_script(good, fb, today=TODAY)) == []


def test_rumors_blocked_by_default(fb):
    s = make_script([{"text": "A rumor claims PC arrives in 2027.", "facts": ["pc_rumor"]}])
    assert "rumor_blocked" in codes(check_script(s, fb, today=TODAY))


def test_rumor_must_be_labelled_when_allowed(fb):
    bad = make_script([{"text": "PC arrives in 2027.", "facts": ["pc_rumor"]}])
    good = make_script([{"text": "An unconfirmed rumor says PC could arrive in 2027.", "facts": ["pc_rumor"]}])
    assert "rumor_as_fact" in codes(check_script(bad, fb, today=TODAY, allow_rumors=True))
    assert errors(check_script(good, fb, today=TODAY, allow_rumors=True)) == []


def test_overclaim_on_rumor(fb):
    s = make_script([{"text": "It's confirmed, the rumor says PC in 2027.", "facts": ["pc_rumor"]}])
    assert "overclaim" in codes(check_script(s, fb, today=TODAY, allow_rumors=True))


def test_leak_language_needs_fact(fb):
    s = make_script([{"text": "An insider just leaked the map.", "kind": "opinion"}])
    assert "leak_language" in codes(check_script(s, fb, today=TODAY))


def test_banned_and_profanity(fb):
    s = make_script([{"text": "Free download link in bio, damn shit.", "kind": "opinion"}])
    c = codes(check_script(s, fb, today=TODAY))
    assert {"banned_phrase", "profanity"} <= c


def test_millions_rounding_allowed(fb):
    s = make_script([{"text": "The first trailer got over 90 million views in 24 hours.", "facts": ["yt_record"]}])
    assert errors(check_script(s, fb, today=TODAY)) == []


def test_structural_and_name_numbers_ignored():
    assert extract_numbers("Here are 3 things about GTA 6 on PS5 and Xbox Series X|S") == set()
    assert extract_numbers("Number 1: Red Dead Redemption 2 and GTA IV") == set()
    assert extract_numbers("It costs $79.99 and got 90,421,491 views") == {"79.99", "90421491"}


def test_title_numbers_checked(fb):
    s = make_script([{"text": "It holds a 99 on Metacritic.", "facts": ["meta99"]}], title="GTA 6 scored 100")
    assert "number_mismatch" in codes(check_script(s, fb, today=TODAY))


def test_card_numbers_checked(fb):
    s = make_script([{"text": "It holds a 99 on Metacritic.", "facts": ["meta99"],
                      "card": {"type": "stat", "big": "97", "small": "METASCORE"}}])
    assert "number_mismatch" in codes(check_script(s, fb, today=TODAY))


def test_stale_fact_warns_then_blocks_for_publishing(fb):
    s = make_script([{"text": "An old fact from 2020.", "facts": ["old"]}])
    assert "stale_fact" in codes(check_script(s, fb, today=TODAY), "warning")
    assert "stale_fact" in codes(check_script(s, fb, today=TODAY, strict_freshness=True))


def test_failed_recheck_makes_fact_stale(fb):
    fb.checks = {"meta99": {"checked": "2026-11-19", "ok": False}}
    s = make_script([{"text": "It holds a 99 on Metacritic.", "facts": ["meta99"]}])
    assert "stale_fact" in codes(check_script(s, fb, today=TODAY, strict_freshness=True))


def test_length_limits(fb):
    s = make_script([], filler=False)
    assert "too_short" in codes(check_script(s, fb, today=TODAY))


def test_similar_scripts_rejected(fb):
    a = make_script([{"text": "It holds a 99 on Metacritic.", "facts": ["meta99"]}], sid="a-video")
    b = make_script([{"text": "It holds a 99 on Metacritic.", "facts": ["meta99"]}], sid="b-video", title="Other")
    assert "too_similar" in codes(check_script(b, fb, today=TODAY, history=[a]))


def test_repository_fact_base_is_valid(cfg):
    fb = load_facts(cfg.facts_file)
    assert validate_factbase(fb) == []
    assert len(fb) >= 15


def test_repository_scripts_have_no_errors(cfg):
    fb = load_facts(cfg.facts_file)
    scripts = load_scripts(cfg.scripts_dir)
    assert scripts, "expected sample scripts in content/scripts"
    for s in scripts:
        history = [o for o in scripts if o.id != s.id]
        issues = check_with_config(s, fb, cfg, today=date(2026, 11, 19), history=history)
        assert errors(issues) == [], f"{s.id}: {errors(issues)}"
