# Costs & free-tier limits

**Total extra cost: 0 DKK.** Everything runs on free tiers or open-source
software. The table lists each limit, how we stay under it and how it's
monitored.

| Service | Free allowance (as researched Nov 2026) | Our usage | Monitoring |
|---|---|---|---|
| GitHub Actions | Standard runners are free and unlimited for **public** repositories | ~3–5 min per video, ~2 min per stats run | Run history; dashboard shows "unlimited (public repo)" |
| GitHub artifacts / releases | Artifacts are kept 7–14 days (set in workflows); release assets are deleted after publish/reject | ~15 MB per video | — |
| GitHub Pages | Free for public repositories | 1 small static page | — |
| YouTube Data API | Uploads: own bucket, 100/day default (since 2026‑06‑01). Other calls: 10,000 units/day | ≤ 3 uploads/day, ~2–10 units/day for stats | `data/usage.json` → dashboard meters (warning at 60 %, critical at 85 %) |
| YouTube Analytics API | Free with the same Google project | 1 request/day | — |
| TikTok APIs | Free; app approval required; rate-limited per user | ≤ 3 posts/day | Errors are reported in the run summary |
| Kokoro‑82M voice | Open model (Apache‑2.0), runs on the runner's CPU | ~10 s per video | — |
| FFmpeg, espeak-ng, Python libs | Open source | — | — |
| Gemini API (optional) | Free tier exists but limits are small and change often (one report: ~20 requests/day on a Flash model, Oct 2026) | ≤ 2–4 requests/day | `gemini_requests` meter; falls back to the next writer on HTTP 429 |
| Pexels API (optional) | 200 requests/hour, 20,000/month | ≤ 10/day | `pexels_requests` in `data/usage.json` |
| Claude Code scheduled session | Included in Claude Pro **usage limits** (not unlimited) | 1 short session/day (proposal) | Your Claude usage page |

## Things that are **not** free (and what we do instead)

| Wish | Why not | Free alternative used |
|---|---|---|
| Claude API calls from GitHub Actions | Claude Pro doesn't include API credits | Scheduled Claude Code session (included in Pro) + free LLM tiers + templates |
| GitHub Models | Retired by GitHub on 2026‑07‑30 | Gemini free tier / OpenAI-compatible free endpoints |
| Premium AI voices (ElevenLabs etc.) | Paid for commercial use | Kokoro‑82M (Apache‑2.0) |
| Licensed music libraries | Subscription | Original music generated per video |
| Gameplay footage | Not allowed by your rules (and no console in the cloud) | Original motion graphics, optional free-licence stock clips |

## If something hits a limit

- **YouTube quota:** the publishing gate stops at `publishing.max_per_day`; a
  failed upload is marked `publish_failed` and retried by the next run.
- **Gemini 429:** the writer moves on to the next backend (template).
- **Scheduled workflows** in public repositories are paused by GitHub after 60
  days without repository activity. The daily commits from the workflows should
  keep the repository active; if GitHub ever pauses a schedule anyway, re-enable
  it with one click on the workflow's page in the *Actions* tab.
