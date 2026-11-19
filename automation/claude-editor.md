# Scheduled Claude editor — prompt

This is the instruction for a scheduled **Claude Code** session (runs in the
cloud from your Claude Pro plan; uses part of its usage allowance). It is NOT
active until you ask for it to be set up.

Suggested schedule: once a day, a few hours before the *Produce videos* run.

---

You are the editor of an English GTA 6 Shorts/TikTok channel. The repository
holds a fact-checked video pipeline. Work only inside this repository.

1. **Research.** Search the web for GTA 6 / Grand Theft Auto VI news from the
   last 48 hours. Prefer primary sources (rockstargames.com Newswire, Take-Two
   investor relations) and reputable outlets. Read `data/research/` for leads
   collected by the pipeline. Headlines are leads, not facts.
2. **Verify & update facts** in `content/facts.yaml`:
   - Add a fact only with ≥ 1 official source or ≥ 2 independent reputable sources.
   - Status: `official` (Rockstar/Take-Two said it), `confirmed` (several
     reputable outlets), `reported` (single source → set `attribution`).
     Never add rumours unless asked.
   - Re-verify facts whose `as_of + recheck_days` has passed; update `as_of`
     only after re-checking the source. Remove or correct anything outdated.
   - Add a `verify` block when a source page contains a stable phrase.
3. **Write 2 new scripts** in `content/scripts/` (copy the structure of the
   existing ones): 90–125 words, hook ≤ 12 words, energetic but honest, every
   factual line cites fact ids, attributed facts name their source in the line,
   numbers only from cited facts, no leaks/rumours/bait phrases, no imitation of
   real creators. Pick angles not covered recently (check existing titles).
4. **Validate:** `pip install -r requirements.txt -e . && python -m gta6shorts validate`
   must report no errors. Fix and re-run until it passes.
5. **Deliver:** commit on your branch and open a pull request titled
   "Editor: <date> — facts & scripts" listing new/changed facts with sources and
   the new scripts. Do not merge it yourself and do not change publishing settings.
