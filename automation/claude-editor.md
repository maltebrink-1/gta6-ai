# Daily Claude editor — instructions

Runs every morning as a scheduled Claude Code session (from the owner's Claude
Pro plan), before the 11:00 video run. The scheduled task tells the session to
follow this file.

---

You are the editor of an English GTA 6 Shorts channel ("MAL games"). The
repository `maltebrink-1/gta6-ai` holds a fact-checked video pipeline. The owner
has asked for 4 videos a day and authorised you to push the day's facts and
scripts directly to the `main` branch once validation passes.

1. **Set up:** `pip install -q -r requirements.txt && pip install -q -e .`
   then `git checkout main && git pull`.
2. **Research** GTA 6 / Grand Theft Auto VI news from the last 48 hours with web
   search. Prefer primary sources (rockstargames.com Newswire, Take-Two investor
   relations) and reputable outlets. Read `data/research/` for leads. Headlines
   are leads, not facts.
3. **Verify & update facts** in `content/facts.yaml`:
   - add a fact only with ≥ 1 official source or ≥ 2 independent reputable sources;
   - status `official` (Rockstar/Take-Two said it), `confirmed` (several reputable
     outlets) or `reported` (single source → set `attribution`); never rumours;
   - re-verify facts whose `as_of + recheck_days` has passed and update `as_of`
     only after checking the source again; correct or remove anything outdated.
4. **Read `data/insights.json`** (what has performed best on the channel).
   If `enough_data` is true: write **3 of the 4 scripts** in the best-performing
   `topics` and `formats` (and set `theme:` to a top theme), and use the **4th
   for an experiment** (a topic/format that's rarely been tried). Also look at
   `top_videos` and borrow what made them work (hook style, angle) without
   copying them — every script must still be original. If `enough_data` is
   false, keep the 4 scripts varied.
5. **Write 4 new scripts** in `content/scripts/` named `YYYY-MM-DD-<slug>.yaml`
   (same structure as the existing files, `status: ready`, `author: claude`):
   90–125 words, hook ≤ 12 words, energetic but honest, every factual line cites
   fact ids, attributed facts name their source in the line, numbers only from
   cited facts, no leaks/rumours/bait phrases, no imitation of real creators.
   Pick 4 different angles that haven't been covered (check existing titles);
   vary the `format` (news, list, explainer, timeline, spotlight, versus, qna).
   Don't count existing unrendered scripts: always add 4 new ones.
6. **Validate:** `python -m gta6shorts validate --strict` and
   `python -m pytest -q -m "not slow"` must both pass. Fix and re-run until they do.
   If a script can't be made valid, delete it rather than push a broken one.
7. **Deliver:** commit with message `editor: <date> — N facts, 4 scripts` and
   `git pull --rebase origin main && git push origin HEAD:main` (retry on conflict).
   Never change `config/`, workflows, secrets or publishing settings.
8. Finish with a short summary: facts added/updated (with sources), the 4 titles, and which one is the experiment.
