"""Script linter: makes sure every video is accurate, attributed and original.

Errors block rendering/publishing. Warnings are shown in the review issue.
The rules are deliberately strict — a missed video costs nothing, a false
claim costs the channel's credibility.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .facts import TRUSTED, FactBase
from .script import CARD_TYPES, FORMATS, ID_RE, KINDS, Script
from .textutil import extract_numbers, similarity

RUMOR_MARKERS = re.compile(r"\b(rumou?r|rumou?red|unconfirmed|not confirmed|allegedly|leak(?:ed|s)?|reportedly)\b", re.I)
ATTRIBUTION_MARKERS = re.compile(r"\b(according to|reportedly|reports?|reported|says|said|claims?|per)\b", re.I)
OVERCLAIM = re.compile(r"\b(confirmed|official(?:ly)?|guaranteed|proven)\b", re.I)
LEAK_WORDS = re.compile(r"\b(leak(?:ed|s)?|insider)\b", re.I)

BANNED_PHRASES = [
    r"free download", r"download (?:gta|it) (?:6 )?(?:for )?free", r"\bgiveaway\b", r"free gta",
    r"\bapk\b", r"mod menu", r"\bhack(?:s|ed)?\b", r"\bcrack(?:ed)?\b", r"link in bio",
    r"pc download", r"\bpirat(?:e|ed|ing)\b",
]
PROFANITY_STEMS = ["fuck", "shit", "bitch", "cunt", "whore", "nigg", "fag", "retard", "slut"]
_BANNED_RE = re.compile("|".join(BANNED_PHRASES), re.I)
_PROFANITY_RE = re.compile(r"\b(" + "|".join(PROFANITY_STEMS) + r")\w*", re.I)


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"line {self.line + 1}: " if self.line is not None else ""
        return f"[{self.level.upper()}] {self.code}: {where}{self.message}"


def _attribution_present(attribution: str, text: str) -> bool:
    a = attribution.lower().strip()
    t = text.lower()
    if a in t:
        return True
    stem = re.sub(r"(ers|ers'|s)$", "", a)
    return len(stem) >= 4 and stem in t


def check_script(
    script: Script,
    fb: FactBase,
    *,
    today: date,
    allow_rumors: bool = False,
    min_seconds: float = 20,
    max_seconds: float = 60,
    words_per_second: float = 2.5,
    history: Iterable[Script] = (),
    similarity_error: float = 0.45,
    similarity_warning: float = 0.30,
    strict_freshness: bool = False,
) -> list[Issue]:
    issues: list[Issue] = []

    def add(level: str, code: str, msg: str, line: int | None = None) -> None:
        issues.append(Issue(level, code, msg, line))

    # ── Structure ────────────────────────────────────────────────────────────
    if not ID_RE.match(script.id):
        add("error", "bad_id", f"id {script.id!r} must be lowercase words joined by hyphens")
    if not script.title:
        add("error", "no_title", "title is required")
    elif len(script.title) > 100:
        add("error", "title_too_long", f"title is {len(script.title)} chars (YouTube max 100)")
    elif len(script.title) > 70:
        add("warning", "title_long", f"title is {len(script.title)} chars; Shorts titles read best under 70")
    if not script.hook_text:
        add("error", "no_hook_text", "hook_text (big on-screen hook) is required")
    elif len(script.hook_text) > 30:
        add("warning", "hook_text_long", "hook_text should be ≤ 30 characters so it fits on screen")
    if len(script.lines) < 3:
        add("error", "too_few_lines", "a script needs at least 3 lines (hook, body, call-to-action)")
    if script.lines and script.lines[0].kind != "hook":
        add("warning", "no_hook_first", "the first line should be the hook (kind: hook)")
    if script.format not in FORMATS:
        add("warning", "unknown_format", f"format {script.format!r} is not one of {FORMATS}")
    if sum(len(t) + 1 for t in script.tags) > 450:
        add("error", "tags_too_long", "tags exceed YouTube's 500-character limit")
    if len(script.description) > 3500:
        add("error", "description_too_long", "description must stay under 3500 chars (sources are appended)")

    script_fact_texts: list[str] = []

    # ── Lines ────────────────────────────────────────────────────────────────
    for i, line in enumerate(script.lines):
        if not line.text:
            add("error", "empty_line", "line text is empty", i)
            continue
        if line.kind not in KINDS:
            add("error", "bad_kind", f"kind {line.kind!r} must be one of {KINDS}", i)
        if line.card and line.card.type not in CARD_TYPES:
            add("error", "bad_card", f"card type {line.card.type!r} must be one of {CARD_TYPES}", i)

        facts = []
        for fid in line.facts:
            fact = fb.get(fid)
            if fact is None:
                add("error", "unknown_fact", f"fact {fid!r} is not in content/facts.yaml", i)
            else:
                facts.append(fact)
        if line.kind == "claim" and not line.facts:
            add("error", "unsourced_claim", "claim lines must cite at least one fact id", i)

        allowed: set[str] = set()
        for fact in facts:
            allowed |= fact.numbers
            script_fact_texts.extend(fact.texts)

        texts_to_check = [("text", line.text)]
        if line.card:
            texts_to_check.append(("card", f"{line.card.big} {line.card.small}"))
        for label, text in texts_to_check:
            nums = extract_numbers(text)
            if nums and not facts:
                add("error", "unsourced_number", f"{label} contains numbers {sorted(nums)} but cites no fact", i)
            elif nums - allowed:
                add("error", "number_mismatch",
                    f"{label} uses {sorted(nums - allowed)} which the cited facts do not contain", i)

        for fact in facts:
            if fact.status == "rumor":
                if not allow_rumors:
                    add("error", "rumor_blocked", f"fact {fact.id!r} is a rumor and rumors are disabled", i)
                elif not RUMOR_MARKERS.search(line.text):
                    add("error", "rumor_as_fact", f"fact {fact.id!r} is a rumor; the line must say so", i)
            if fact.attribution:
                if not _attribution_present(fact.attribution, line.text):
                    add("error", "missing_attribution",
                        f"fact {fact.id!r} must be attributed to {fact.attribution!r} in the line", i)
            elif fact.status == "reported" and not ATTRIBUTION_MARKERS.search(line.text):
                add("error", "missing_attribution", f"fact {fact.id!r} is 'reported'; say who reported it", i)
            if not fact.is_fresh(today, fb.checks):
                level = "error" if strict_freshness else "warning"
                add(level, "stale_fact",
                    f"fact {fact.id!r} needs re-verification (older than {fact.recheck_days} days or failed re-check)", i)

        if OVERCLAIM.search(line.text) and facts and not all(f.status in TRUSTED for f in facts):
            add("error", "overclaim", "says 'confirmed/official' but cites non-confirmed facts", i)
        if OVERCLAIM.search(line.text) and not facts:
            add("error", "overclaim", "says 'confirmed/official' without citing a fact", i)
        if LEAK_WORDS.search(line.text) and not any(
            f.status == "rumor" or LEAK_WORDS.search(" ".join(f.texts)) for f in facts
        ):
            add("error", "leak_language", "mentions leaks/insiders without a fact about that leak", i)
        if _BANNED_RE.search(line.text):
            add("error", "banned_phrase", "contains a scam/bait phrase that breaks platform rules", i)
        if _PROFANITY_RE.search(line.text):
            add("error", "profanity", "contains profanity or a slur", i)

    # ── Title / hook numbers must come from cited facts ─────────────────────
    from .textutil import allowed_numbers

    all_allowed = allowed_numbers(script_fact_texts)
    for label, text in (("title", script.title), ("hook_text", script.hook_text)):
        bad = extract_numbers(text) - all_allowed
        if bad:
            add("error", "number_mismatch", f"{label} uses {sorted(bad)} which no cited fact contains")
        if _BANNED_RE.search(text) or _PROFANITY_RE.search(text):
            add("error", "banned_phrase", f"{label} contains a banned phrase")

    # ── Length ───────────────────────────────────────────────────────────────
    est = script.estimated_seconds(words_per_second)
    if est < min_seconds:
        add("error", "too_short", f"~{est:.0f}s of narration; minimum is {min_seconds:.0f}s")
    elif est > max_seconds:
        add("error", "too_long", f"~{est:.0f}s of narration; maximum is {max_seconds:.0f}s")

    # ── Originality ──────────────────────────────────────────────────────────
    for other in history:
        if other.id == script.id:
            continue
        if other.title.strip().lower() == script.title.strip().lower():
            add("error", "duplicate_title", f"same title as {other.id}")
        sim = similarity(script.spoken_text, other.spoken_text)
        if sim >= similarity_error:
            add("error", "too_similar", f"{sim:.0%} similar to {other.id}; write a fresher angle")
        elif sim >= similarity_warning:
            add("warning", "similar", f"{sim:.0%} similar to {other.id}")

    return issues


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def format_issues(issues: Iterable[Issue]) -> str:
    issues = list(issues)
    if not issues:
        return "✓ no issues"
    return "\n".join(str(i) for i in issues)


def check_with_config(script: Script, fb: FactBase, cfg, *, today: date, history=(), strict_freshness=False) -> list[Issue]:
    """check_script() with thresholds taken from config/settings.yaml."""
    return check_script(
        script,
        fb,
        today=today,
        allow_rumors=bool(cfg.get("facts.allow_rumors", False)),
        min_seconds=float(cfg.get("production.min_seconds", 20)),
        max_seconds=float(cfg.get("production.max_seconds", 60)),
        words_per_second=float(cfg.get("production.words_per_second", 2.5)),
        history=history,
        similarity_error=float(cfg.get("quality.similarity_error", 0.45)),
        similarity_warning=float(cfg.get("quality.similarity_warning", 0.30)),
        strict_freshness=strict_freshness,
    )
