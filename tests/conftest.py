from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml

from gta6shorts.config import Config, load_config
from gta6shorts.facts import FactBase, _parse_fact
from gta6shorts.script import Script

REPO = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 19)

FIXTURE_FACTS = textwrap.dedent(
    """
    facts:
      - id: meta99
        claim: "GTA VI holds a 99 Metascore on Metacritic."
        status: confirmed
        as_of: 2026-11-19
        recheck_days: 7
        sources: [{url: "https://example.com/a", publisher: A}, {url: "https://example.com/b", publisher: B}]
      - id: views
        claim: "Rockstar says Trailer 2 reached about 475 million views in 24 hours."
        status: official
        attribution: Rockstar
        as_of: 2026-11-19
        sources: [{url: "https://example.com/c", publisher: C}]
      - id: yt_record
        claim: "The first trailer got 90,421,491 YouTube views in 24 hours."
        status: confirmed
        as_of: 2026-11-19
        sources: [{url: "https://example.com/d", publisher: D}]
      - id: pc_rumor
        claim: "A rumor claims a PC version in 2027."
        status: rumor
        as_of: 2026-11-19
        sources: [{url: "https://example.com/e", publisher: E}]
      - id: rep
        claim: "Some reviewers criticized clunky controls."
        status: reported
        attribution: reviewers
        as_of: 2026-11-19
        sources: [{url: "https://example.com/f", publisher: F}]
      - id: old
        claim: "An old fact from 2020."
        status: confirmed
        as_of: 2020-01-01
        recheck_days: 30
        sources: [{url: "https://example.com/g", publisher: G}]
    """
)


@pytest.fixture
def fb() -> FactBase:
    raw = yaml.safe_load(FIXTURE_FACTS)
    return FactBase([_parse_fact(f, 14) for f in raw["facts"]])


@pytest.fixture
def cfg() -> Config:
    return load_config(REPO)


FILLER = (
    "This game looks absolutely massive and everybody is talking about it right now online. "
    "The hype is real and the community is going wild over every single new detail that appears."
)


def make_script(lines, sid="2026-11-19-test-video", title="A Test Video", hook_text="TEST HOOK", filler=True) -> Script:
    raw_lines = [{"text": "You will not believe this GTA news.", "kind": "hook"}]
    raw_lines += lines
    if filler:
        raw_lines.append({"text": FILLER, "kind": "opinion"})
    raw_lines.append({"text": "Follow for more GTA updates every day.", "kind": "cta"})
    return Script.from_dict({"id": sid, "title": title, "hook_text": hook_text, "lines": raw_lines})
