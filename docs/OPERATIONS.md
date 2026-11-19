# Operations — day-to-day use

## Your daily routine (≈ 2 minutes, phone is enough)

1. **GitHub app → Issues → label `review`.** Each issue has a *Watch the video*
   link, a contact sheet, the script with its fact ids, the sources and the QA
   result.
2. Watch it. Then add a label:
   - **`approved`** → recorded; when publishing is ON it uploads right away and
     comments with the YouTube link (then closes the issue).
   - **`rejected`** → the video file is deleted and the issue closed.
3. Glance at the **dashboard** (GitHub Pages link) or the latest *Stats &
   dashboard* run summary for the daily report.

Only the repository owner's labels count, so the public can't approve anything.

## Running things manually

*Actions* tab → pick a workflow → **Run workflow**:

| Workflow | Use it to |
|---|---|
| Produce videos | make videos now (choose how many; untick "open review issues" for a dry run) |
| Publish | publish all approved videos that are still waiting (e.g. after switching publishing on) |
| Stats & dashboard | refresh statistics, report and dashboard (tick *demo* to preview the layout with fake data) |
| CI | re-run tests and the cloud render test |

On a computer with Python 3.11+, FFmpeg and espeak-ng:

```bash
pip install -r requirements.txt && pip install -e .
```

```bash
python -m gta6shorts doctor
```

```bash
python -m gta6shorts validate
```

```bash
python -m gta6shorts render 2026-11-19-gta6-launch-day
```

```bash
python -m gta6shorts report --demo --site /tmp/site
```

## Adding content yourself

- **A fact:** add an entry to `content/facts.yaml` with at least one source URL,
  a status, `as_of` and (if it's someone's claim) an `attribution`. Run
  `python -m gta6shorts validate`.
- **A script:** copy any file in `content/scripts/`, give it a new `id` (and
  file name), keep `status: ready`, cite fact ids on factual lines. The next
  *Produce* run renders it. GitHub's web editor works fine for this, even on a
  phone.
- **Music:** drop a licensed file into `assets/music/`, describe it in
  `assets/music/licenses.yaml`, set `music.source: library`.

## Settings you'll most likely change (`config/settings.yaml`)

| Setting | Meaning |
|---|---|
| `channel.name`, `channel.handle` | your channel identity |
| `voice.preset` | `hype_female`, `hype_male`, `calm_british` (or add your own blend) |
| `production.videos_per_day` | videos per daily run |
| `publishing.*` | the stage switches — see SETUP.md §5 |
| `captions.cap_height`, `captions.y` | caption size and position |

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| Script skipped: "fails fact check" | A fact is stale or a number/attribution is wrong. The run log lists the rule; re-verify the fact (update `as_of`) or fix the line |
| QA failed: loudness / dead air / duration | Usually a very short or very long script; adjust the script length (20–60 s) |
| `token refresh failed … invalid_grant` | YouTube refresh token revoked/expired (e.g. app still in "Testing", or password change) → redo SETUP §2 step 5 and update `YT_REFRESH_TOKEN` |
| Upload came back `private` although you chose public | YouTube applied a restriction (account or API policy). Check YouTube Studio; the issue comment shows the privacy YouTube returned |
| Dashboard 404 | Enable *Settings → Pages → Source: GitHub Actions*, then run *Stats & dashboard* |
| Scheduled runs don't happen | `AUTOMATION_ENABLED` variable missing, or GitHub paused the schedule — re-enable on the workflow page |
| "TikTok issued a new refresh token" | Update the `TIKTOK_REFRESH_TOKEN` secret with the new value from the run log |
| "Could not install FFmpeg from the Ubuntu mirrors" | Ubuntu's package mirrors were unreachable from GitHub's runner. Downloaded packages are cached (refreshed weekly), so this only bites on a cache miss — just re-run the workflow later. Publishing and stats never need FFmpeg |
