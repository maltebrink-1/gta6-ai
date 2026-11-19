"""Script writers.

Order of preference (config ``writer.backends``):
  queue     – scripts already committed to content/scripts (Claude / you)
  gemini    – Gemini free tier
  openai    – any OpenAI-compatible free endpoint
  template  – offline writer that only rephrases the fact base

Every draft goes through the same fact checker; drafts with errors are dropped.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from .facts import Fact, FactBase
from .factcheck import Issue, check_with_config, errors, format_issues
from .llm import LLMBackend, LLMError, extract_json
from .script import Card, Line, Script, slugify

# ── Offline template writer ──────────────────────────────────────────────────
HOOKS = {
    "release": ["Everything you need to know about the GTA 6 launch, fast.",
                "GTA 6 is out, so let's get you up to speed."],
    "reviews": ["The GTA 6 reviews are in, and they're kind of unbelievable.",
                "Critics just gave their verdict on GTA 6."],
    "trailers": ["GTA 6 was breaking records before it even launched.",
                 "The GTA 6 hype had actual world records attached."],
    "characters": ["Here's who you actually play as in GTA 6.",
                   "Quick question: do you know who GTA 6's protagonists really are?"],
    "world": ["Welcome to Leonida. Here's what you need to know.",
              "GTA 6's world, explained in seconds."],
    "history": ["GTA 6 didn't come out of nowhere. Here's the backstory.",
                "A little GTA history that makes GTA 6 hit different."],
}
HOOK_TEXT = {
    "release": "LAUNCH DAY RUNDOWN", "reviews": "THE REVIEWS ARE IN", "trailers": "RECORD BREAKER",
    "characters": "WHO YOU PLAY AS", "world": "WELCOME TO LEONIDA", "history": "GTA HISTORY",
}
TITLES = {
    "release": ["GTA 6 Launch: What You Need to Know", "GTA 6 Is Out: The Quick Rundown"],
    "reviews": ["What Critics Are Saying About GTA 6", "GTA 6 Reviews in Under a Minute"],
    "trailers": ["The Records GTA 6 Broke Before Launch", "GTA 6's Hype, by the Numbers"],
    "characters": ["Who You Play as in GTA 6", "GTA 6's Protagonists, Explained"],
    "world": ["GTA 6's World, Explained", "Welcome to Leonida"],
    "history": ["The GTA History Behind GTA 6", "How GTA Got Here"],
}
OPINIONS = [
    "Honestly, that's exactly the kind of detail that makes this game feel huge.",
    "I did not see that coming, and I doubt most people did either.",
    "That is wild when you actually stop and think about it.",
    "And honestly? That might be the most impressive part.",
]
CTAS = [
    "Which part surprised you most? Tell me below, and follow for daily GTA 6 news.",
    "Did you know all of that? Let me know, and follow for more fact-checked GTA 6 content.",
    "What should I cover next? Drop it in the comments and follow for more.",
]
TRANSITIONS = ["Now get this.", "But here's the crazy part.", "And it gets better.", "Okay, one more."]
RELATED = {
    "world": ["characters", "history"],
    "characters": ["world"],
    "trailers": ["reviews", "release"],
    "reviews": ["release", "trailers"],
    "release": ["reviews", "history"],
    "history": ["release", "trailers"],
}


def _fact_line(fact: Fact, rng: random.Random) -> Line:
    text = rng.choice(fact.say) if fact.say else fact.claim
    return Line(text=text, kind="claim", facts=[fact.id])


def fact_usage(history: Sequence[Script]) -> Counter:
    return Counter(fid for s in history for fid in s.fact_ids())


class TemplateWriter:
    name = "template"

    def __init__(self, fb: FactBase, cfg, today: date):
        self.fb, self.cfg, self.today = fb, cfg, today

    def draft(self, history: Sequence[Script], seed: int = 0, category: str | None = None) -> Script:
        rng = random.Random(seed)
        usable = self.fb.usable(allow_rumors=False, today=self.today, fresh_only=True)
        groups: dict[str, list[Fact]] = {}
        for f in usable:
            groups.setdefault(f.category, []).append(f)
        usage = fact_usage(history)
        if category is None:
            # least-used category first, so the channel rotates topics
            ranked = sorted(groups, key=lambda c: (sum(usage[f.id] for f in groups[c]) / len(groups[c]), rng.random()))
            category = ranked[0]
        facts = sorted(groups[category], key=lambda f: (usage[f.id], rng.random()))[:4]
        if len(facts) < 3:  # top up from closely related topics only, so the video stays coherent
            related = RELATED.get(category, [])
            extra = [f for f in usable if f not in facts and f.category in related]
            extra.sort(key=lambda f: (related.index(f.category), usage[f.id], rng.random()))
            facts += extra[: 3 - len(facts)]

        lines = [Line(text=rng.choice(HOOKS.get(category, HOOKS["history"])), kind="hook", facts=[facts[0].id])]
        for i, fact in enumerate(facts):
            if i == 2:
                lines.append(Line(text=rng.choice(TRANSITIONS), kind="transition"))
            lines.append(_fact_line(fact, rng))
        lines.append(Line(text=rng.choice(OPINIONS), kind="opinion"))
        lines.append(Line(text=rng.choice(CTAS), kind="cta"))
        title = rng.choice(TITLES.get(category, TITLES["history"]))
        return Script(
            id=f"{self.today.isoformat()}-{slugify(title)}-{seed % 1000}",
            title=title,
            hook_text=HOOK_TEXT.get(category, "GTA 6 FACTS"),
            lines=lines,
            description=f"{title} — fact-checked, with sources below.",
            tags=["GTA 6", "GTA VI", "Grand Theft Auto VI", "Rockstar Games", "gaming news"],
            format="list",
            author="template",
            created=self.today.isoformat(),
            status="ready",
        )


# ── LLM writer ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You write short-form video scripts (YouTube Shorts / TikTok) for an English GTA 6 news & facts channel.
Narrator persona: {persona}

HARD RULES (a script that breaks any rule is thrown away):
1. Use ONLY the facts in FACTS. Every line that states a fact lists the ids it relies on in "facts".
2. Never invent numbers, dates, names, features, prices or quotes. Every number you write must appear in a cited fact.
3. If a fact has an "attribution", name it in that line (e.g. "Rockstar says ...").
4. Facts with status "reported" must be attributed in the line ("according to ...", "reportedly").
5. Opinions must sound like opinions ("honestly", "I think") and contain no numbers unless they cite the fact.
6. Never mention leaks, insiders, rumors, piracy, downloads, giveaways, "link in bio", or use profanity.
7. Do not imitate any real streamer, YouTuber or celebrity, and do not use their catchphrases.
8. Never claim to be a real person. Do not say the footage is gameplay.

STYLE: energetic and punchy like a hyped friend, short sentences, talk to "you". The first line is a hook of at most 12 words.
Total 90-125 words across 6-8 lines. End with a question to the viewer plus a follow call-to-action.
Optional "card" per line shows big on-screen text: {{"type": "stat|text|date", "big": "<=12 chars", "small": "<=28 chars"}} (numbers in cards must come from that line's facts).

Return ONLY JSON:
{{"title": "<=70 chars, no clickbait lies", "hook_text": "2-4 WORDS UPPERCASE", "format": "news|list|explainer|timeline|spotlight|versus|qna",
 "lines": [{{"text": "...", "kind": "hook|claim|opinion|question|transition|cta", "facts": ["fact_id"], "card": null}}],
 "description": "1-2 sentences", "tags": ["5-10 tags"]}}"""


@dataclass
class DraftResult:
    script: Script | None
    issues: list[Issue]
    backend: str
    attempts: int
    error: str | None = None


def _facts_payload(facts: Sequence[Fact]) -> list[dict]:
    return [
        {"id": f.id, "claim": f.claim, "status": f.status, "attribution": f.attribution, "category": f.category}
        for f in facts
    ]


def pick_topic(fb: FactBase, history: Sequence[Script], today: date, rng: random.Random) -> tuple[str, list[Fact]]:
    usable = fb.usable(allow_rumors=False, today=today, fresh_only=True)
    usage = fact_usage(history)
    groups: dict[str, list[Fact]] = {}
    for f in usable:
        groups.setdefault(f.category, []).append(f)
    category = min(groups, key=lambda c: (sum(usage[f.id] for f in groups[c]) / len(groups[c]), rng.random()))
    focus = list(groups[category])
    others = [f for f in usable if f.category != category]
    rng.shuffle(others)
    return category, focus + others[:6]


class LLMWriter:
    def __init__(self, backend: LLMBackend, fb: FactBase, cfg, today: date):
        self.backend, self.fb, self.cfg, self.today = backend, fb, cfg, today

    def _to_script(self, data: dict) -> Script:
        lines = []
        for item in data.get("lines", []):
            card = item.get("card")
            lines.append(Line(
                text=str(item.get("text", "")).strip(),
                kind=str(item.get("kind", "claim")),
                facts=[str(x) for x in item.get("facts") or []],
                card=Card(type=str(card.get("type", "text")), big=str(card.get("big", "")), small=str(card.get("small", "")))
                if isinstance(card, dict) else None,
            ))
        title = str(data.get("title", "")).strip()
        return Script(
            id=f"{self.today.isoformat()}-{slugify(title)}",
            title=title,
            hook_text=str(data.get("hook_text", "")).strip().upper(),
            lines=lines,
            description=str(data.get("description", "")).strip(),
            tags=[str(t) for t in data.get("tags") or []][:12],
            format=str(data.get("format", "explainer")),
            author=self.backend.name,
            created=self.today.isoformat(),
            status="ready",
        )

    def draft(self, history: Sequence[Script], seed: int = 0, news: Sequence[dict] = ()) -> DraftResult:
        rng = random.Random(seed)
        category, facts = pick_topic(self.fb, history, self.today, rng)
        system = SYSTEM_PROMPT.format(persona=self.cfg.get("channel.persona", "energetic GTA fan"))
        payload = {
            "angle": f"Focus on the '{category}' facts. Pick a fresh angle.",
            "FACTS": _facts_payload(facts),
            "AVOID_REPEATING_THESE_TITLES": [s.title for s in list(history)[-15:]],
            "NEWS_HEADLINES_FOR_CONTEXT_ONLY_NOT_FACTS": [n.get("title") for n in list(news)[:10]],
        }
        user = json.dumps(payload, ensure_ascii=False, indent=1)
        rounds = 1 + int(self.cfg.get("writer.max_repair_rounds", 1))
        last_issues: list[Issue] = []
        for attempt in range(1, rounds + 1):
            try:
                reply = self.backend.complete(system, user, temperature=float(self.cfg.get("writer.temperature", 0.9)))
                script = self._to_script(extract_json(reply))
            except LLMError as exc:
                return DraftResult(None, last_issues, self.backend.name, attempt, str(exc))
            issues = check_with_config(script, self.fb, self.cfg, today=self.today, history=history, strict_freshness=True)
            if not errors(issues):
                return DraftResult(script, issues, self.backend.name, attempt)
            last_issues = issues
            user = (
                f"{user}\n\nYOUR PREVIOUS SCRIPT:\n{json.dumps(script.to_dict(), ensure_ascii=False)}\n\n"
                f"IT FAILED THE FACT CHECK. Fix every problem and return the full JSON again:\n{format_issues(errors(issues))}"
            )
        return DraftResult(None, last_issues, self.backend.name, rounds, "fact check failed after repair")


def write_new_scripts(
    count: int,
    fb: FactBase,
    cfg,
    today: date,
    history: Sequence[Script],
    backends: Sequence[LLMBackend] = (),
    allow_template: bool = True,
    news: Sequence[dict] = (),
    seed: int | None = None,
) -> tuple[list[Script], list[str]]:
    """Draft ``count`` new scripts. Returns (scripts, log lines)."""
    seed = seed if seed is not None else int(today.strftime("%Y%m%d"))
    made: list[Script] = []
    log: list[str] = []
    for n in range(count):
        hist = list(history) + made
        script = None
        for backend in backends:
            result = LLMWriter(backend, fb, cfg, today).draft(hist, seed=seed + n, news=news)
            if result.script:
                script = result.script
                log.append(f"{backend.name}: drafted '{script.title}' in {result.attempts} attempt(s)")
                break
            log.append(f"{backend.name}: no usable draft ({result.error}); {len(errors(result.issues))} errors")
        if script is None and allow_template:
            tw = TemplateWriter(fb, cfg, today)
            for attempt in range(12):
                candidate = tw.draft(hist, seed=seed + n * 100 + attempt)
                issues = check_with_config(candidate, fb, cfg, today=today, history=hist, strict_freshness=True)
                if not errors(issues):
                    script = candidate
                    log.append(f"template: drafted '{script.title}'")
                    break
            else:
                log.append("template: could not produce a valid, non-repetitive script")
        if script:
            made.append(script)
    return made, log
