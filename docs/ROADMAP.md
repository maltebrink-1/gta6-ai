# Roadmap & status

Legend: ✅ done and tested · 🧪 built, tested with mocks only (needs your account) · ⬜ to do · ⏸️ waiting for you

## Stage 1 — Foundation ✅

- ✅ Project structure, configuration, documentation
- ✅ Verified fact base (22 facts, launch day 2026‑11‑19) + strict fact checker
- ✅ Script writers: committed queue, Gemini/OpenAI-compatible (🧪 mocked), offline templates
- ✅ Free neural narration (Kokoro‑82M) + espeak fallback + caption timing
- ✅ Captions, hook, fact cards, source credits, progress bar (ASS/libass)
- ✅ Six procedural background styles, five colour themes, transitions
- ✅ Original music + SFX, ducking, −14 LUFS mastering
- ✅ FFmpeg render 1080×1920 / 30 fps / H.264 + AAC
- ✅ Automatic QA (16 checks)
- 🧪 Review queue (GitHub issues + release) — mocked tests only; first real run when you enable Stage 2
- ✅ Publishing gate (everything OFF by default)
- 🧪 YouTube upload + statistics client
- 🧪 TikTok inbox/direct client
- ✅ Statistics model, daily report, mobile-friendly dashboard (demo data)
- ✅ GitHub Actions CI incl. a real cloud render (verified) · 🧪 produce / publish / stats workflows written, not run yet (they open issues and commit data, so they wait for your go)
- ✅ 87 automated tests

## Stage 2 — Testing ⏸️

- ✅ First 5 test videos rendered (launch day, PC wait, trailer records, Lucia & Jason, GTA timeline)
- ✅ Cloud render verified in GitHub Actions (Kokoro voice, QA 16/16, ~2 min per video on the free runner)
- ⏸️ You review the test videos and pick the narrator voice / look
- ⏸️ Set `AUTOMATION_ENABLED=true` → daily produce + stats in the cloud, review issues on your phone
- ⏸️ Enable GitHub Pages for the dashboard
- ⬜ Decide on the scheduled Claude editor session (research + new scripts via pull request)

## Stage 3 — Publishing ⏸️

- ⏸️ Connect YouTube (SETUP §2)
- ⬜ First upload as **private**, with your approval; verify title/description/AI label in YouTube Studio
- ⬜ Switch `privacy: public` once you're happy
- ⏸️ TikTok developer app approval → inbox mode (drafts)

## Stage 4 — Full automation ⬜

- ⬜ `publishing.mode: auto` after a successful Stage 3 period
- ⬜ Real statistics in the dashboard; weekly review of best performers
- ⬜ Ideas: A/B test hooks, schedule uploads at peak times (`publishAt`), optional ASR check of the narration, TikTok audit for direct posting
