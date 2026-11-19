# Setup guide — what you need to connect

Everything below is **free**. Nothing here needs your PC after the one-time
steps (all of them can be done in a browser, most even on a phone).
Never paste keys or tokens into chats, issues or files — only into
**GitHub → Settings → Secrets and variables → Actions**.

| # | What | Needed for | Time |
|---|---|---|---|
| 1 | GitHub repository settings | Stage 2 (cloud automation, dashboard) | 5 min |
| 2 | YouTube API access | Stage 3 (uploads + statistics) | 20–30 min |
| 3 | TikTok developer app | Stage 3+ (only after TikTok approves it) | 30 min + waiting |
| 4 | Optional extras (Gemini, Pexels, Claude editor) | better automation | 5 min each |

---

## 1. GitHub repository settings

1. **Default branch (recommended).** Because the repository was empty, GitHub
   made `claude/clever-wright-65b2hk` the default branch. Rename it to `main`:
   *Settings → General → Default branch → ✏️ rename*. Nothing in the code
   depends on the name.
2. **Dashboard hosting.** *Settings → Pages → Build and deployment → Source:*
   **GitHub Actions**. The dashboard will appear at
   `https://maltebrink-1.github.io/gta6-ai/` after the next *Stats & dashboard* run.
3. **Switch on the daily schedule (Stage 2).** *Settings → Secrets and variables
   → Actions → Variables → New repository variable*:
   `AUTOMATION_ENABLED` = `true`.
   From then on the *Produce videos* and *Stats & dashboard* workflows run every
   day. Nothing is published — every video lands in a review issue.
4. **Notifications on your phone.** Install the GitHub mobile app and *Watch*
   the repository (Custom → Issues) so each new review issue pings you.

> The repository is **public**, which is what makes GitHub Actions minutes and
> GitHub Pages free. Code, scripts, statistics and rendered review videos are
> visible to anyone; secrets are not.

## 2. YouTube (uploads + statistics)

You need: the YouTube channel (create it first, ideally as a Brand Account),
and a Google Cloud project — free, no credit card.

1. Open <https://console.cloud.google.com/> → *Select project → New project*
   (name e.g. `gta6-shorts`).
2. *APIs & Services → Library*: enable **YouTube Data API v3** and
   **YouTube Analytics API**.
3. *Google Auth Platform* (OAuth consent screen): User type **External**, app
   name e.g. "Shorts uploader", your email as support/developer contact.
   - *Data access → Add scopes*:
     `https://www.googleapis.com/auth/youtube.upload`,
     `https://www.googleapis.com/auth/youtube.readonly`,
     `https://www.googleapis.com/auth/yt-analytics.readonly`
   - *Audience*: add your own Google account as a test user, then click
     **Publish app** (→ "In production"). Without this, Google expires the
     refresh token after 7 days. You don't need Google's verification for a
     personal app; you'll just see an "unverified app" warning that you can
     click through once.
4. *Clients → Create client → Web application*. Under *Authorized redirect URIs*
   add `https://developers.google.com/oauthplayground`. Copy the **Client ID**
   and **Client secret**.
5. Open <https://developers.google.com/oauthplayground>:
   - ⚙️ (top right) → tick *Use your own OAuth credentials* → paste the Client ID
     and secret.
   - In *Step 1*, type the three scopes above (space separated) into the box →
     **Authorize APIs** → sign in and pick **the channel** (brand account) you
     want to upload to → allow.
   - *Step 2* → **Exchange authorization code for tokens** → copy the
     **Refresh token**.
6. GitHub → *Settings → Secrets and variables → Actions → New repository secret*:
   - `YT_CLIENT_ID`
   - `YT_CLIENT_SECRET`
   - `YT_REFRESH_TOKEN`
7. Test it: *Actions → Stats & dashboard → Run workflow*. The run summary and the
   dashboard should now show your channel. No video is uploaded by this.

**Quota (from Google's revision history):** since 2026‑06‑01 uploads have their
own daily bucket (default 100 per project) and since 2025‑12‑04 an upload costs
~100 units instead of 1,600; other calls share 10,000 units/day. Google's
revision history (2 Nov 2026) also says uploads from unverified API projects
are no longer forced to private — our first test upload will confirm it,
because the pipeline reports the privacy status YouTube actually applied. We
start with `privacy: private` anyway so you can check the first uploads.

## 3. TikTok (only after approval)

TikTok's Content Posting API needs an approved developer app, and direct
posting stays limited to private (`SELF_ONLY`) posts until TikTok audits the
app. We never try to get around this.

1. Sign up at <https://developers.tiktok.com/> with the TikTok account that owns
   the channel → *Manage apps → Connect an app*.
2. Add products **Login Kit** and **Content Posting API**; request scopes
   `user.info.basic`, `video.upload` (send to your drafts),
   `user.info.stats` and `video.list` (statistics). `video.publish` (direct
   posting) is optional and needs the audit to post publicly.
3. TikTok asks for a description, a privacy-policy URL and terms URL, and a
   short screen recording. Describe it honestly: "Uploads videos I create to my
   own TikTok account as drafts". (The policy pages can be hosted on the same
   GitHub Pages site — ask me and I'll add them.)
4. While you wait, set `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` as secrets.
5. When the app is approved, tell me — I'll walk you through the one-time login
   that produces `TIKTOK_REFRESH_TOKEN` (valid for a year).

Default mode is **inbox**: the video arrives in your TikTok drafts and you tap
*Post* in the app (~10 seconds on your phone). Videos are flagged as
AI-generated content (`is_aigc`).

## 4. Optional free extras

| Secret | Get it at | What it adds |
|---|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | Automatic script drafts inside GitHub Actions when the queue is empty. Free-tier limits are small and change often; free-tier prompts may be used by Google to improve its products. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | any OpenAI-compatible provider with a free tier | Same as above, provider of your choice |
| `PEXELS_API_KEY` | <https://www.pexels.com/api/> | Real-world Florida/Miami B-roll on lines with a `broll:` query (credited in the description) |

**Scheduled Claude editor (recommended).** A Claude Code session that runs on
a schedule from your Claude Pro plan: it researches GTA 6 news, verifies facts,
updates `content/facts.yaml`, writes 2 new scripts and opens a pull request
for you to merge. Prompt: [`automation/claude-editor.md`](../automation/claude-editor.md).
It uses part of your Claude Pro usage allowance, so I'll only switch it on
when you say so.

## 5. Turning on the stages

| Stage | Switch |
|---|---|
| 2 – testing in the cloud | `AUTOMATION_ENABLED=true` (repository variable) |
| 3 – publishing with approval | YouTube secrets + in `config/settings.yaml`: `publishing.enabled: true`, `publishing.youtube.enabled: true` (keep `privacy: private` for the first uploads) |
| 4 – full automation | `publishing.mode: auto`, `publishing.youtube.privacy: public`. QA-passed videos then publish without approval (max `publishing.max_per_day`) |

See [OPERATIONS.md](OPERATIONS.md) for the day-to-day routine.
