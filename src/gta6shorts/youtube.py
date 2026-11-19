"""YouTube Data API v3 client (uploads + statistics). Free; uses OAuth.

Secrets: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (see docs/SETUP.md).
Scopes:  youtube.upload, youtube.readonly, yt-analytics.readonly (optional).
Quota (Google revision history): uploads have their own bucket since
2026-06-01 (default 100 uploads/day); other calls share 10,000 units/day.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
CHUNK = 8 * 1024 * 1024


class YouTubeError(RuntimeError):
    pass


class YouTubeClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 session: requests.Session | None = None, sleep=time.sleep):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.session = session or requests.Session()
        self.sleep = sleep
        self._token: str | None = None
        self._token_expiry = 0.0

    # ── auth ─────────────────────────────────────────────────────────────────
    def access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = self.session.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token"}, timeout=30)
        if resp.status_code != 200:
            raise YouTubeError(f"token refresh failed (HTTP {resp.status_code}): {resp.text[:200]} — "
                               "is the OAuth app 'In production' and the refresh token still valid?")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}", **(extra or {})}

    # ── upload ───────────────────────────────────────────────────────────────
    @staticmethod
    def build_body(title: str, description: str, tags: list[str], *, category_id: str = "20", privacy: str = "private",
                   made_for_kids: bool = False, synthetic: bool = True, publish_at: str | None = None) -> dict:
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids,
                  "containsSyntheticMedia": synthetic}
        if publish_at:
            status["privacyStatus"] = "private"  # scheduled videos must start private
            status["publishAt"] = publish_at
        return {
            "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                        "categoryId": category_id, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": status,
        }

    def upload(self, video: Path, body: dict, retries: int = 5) -> dict:
        size = video.stat().st_size
        init = self.session.post(
            f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
            headers=self._headers({"Content-Type": "application/json; charset=UTF-8",
                                   "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"}),
            data=json.dumps(body), timeout=60)
        if init.status_code != 200 or "Location" not in init.headers:
            raise YouTubeError(f"upload init failed (HTTP {init.status_code}): {init.text[:300]}")
        url = init.headers["Location"]
        offset, attempt = 0, 0
        with video.open("rb") as fh:
            while offset < size:
                fh.seek(offset)
                chunk = fh.read(CHUNK)
                end = offset + len(chunk) - 1
                try:
                    resp = self.session.put(url, data=chunk, timeout=300, headers=self._headers({
                        "Content-Length": str(len(chunk)), "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {offset}-{end}/{size}"}))
                except requests.RequestException as exc:
                    resp, error = None, str(exc)
                else:
                    error = f"HTTP {resp.status_code}"
                if resp is not None and resp.status_code in (200, 201):
                    return resp.json()
                if resp is not None and resp.status_code == 308:
                    rng = resp.headers.get("Range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    attempt = 0
                    continue
                if resp is not None and resp.status_code not in (500, 502, 503, 504):
                    raise YouTubeError(f"upload failed ({error}): {resp.text[:300]}")
                attempt += 1
                if attempt > retries:
                    raise YouTubeError(f"upload failed after {retries} retries ({error})")
                self.sleep(min(60, 2 ** attempt))
                status = self.session.put(url, timeout=60, headers=self._headers({
                    "Content-Length": "0", "Content-Range": f"bytes */{size}"}))
                if status.status_code in (200, 201):
                    return status.json()
                rng = status.headers.get("Range") if status.status_code == 308 else None
                offset = int(rng.split("-")[1]) + 1 if rng else 0
        raise YouTubeError("upload ended without a response")

    # ── statistics ───────────────────────────────────────────────────────────
    def video_stats(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            resp = self.session.get(f"{API}/videos", params={"part": "statistics,snippet", "id": ",".join(batch)},
                                    headers=self._headers(), timeout=30)
            if resp.status_code != 200:
                raise YouTubeError(f"videos.list failed (HTTP {resp.status_code})")
            for item in resp.json().get("items", []):
                st = item.get("statistics", {})
                out[item["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                                   "comments": int(st.get("commentCount", 0)),
                                   "title": item.get("snippet", {}).get("title", "")}
        return out

    def channel_stats(self) -> dict:
        resp = self.session.get(f"{API}/channels", params={"part": "statistics,snippet", "mine": "true"},
                                headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise YouTubeError(f"channels.list failed (HTTP {resp.status_code})")
        items = resp.json().get("items", [])
        if not items:
            raise YouTubeError("no channel found for this account")
        st = items[0].get("statistics", {})
        return {"channel_id": items[0]["id"], "title": items[0].get("snippet", {}).get("title", ""),
                "subscribers": int(st.get("subscriberCount", 0)), "views": int(st.get("viewCount", 0)),
                "videos": int(st.get("videoCount", 0))}

    def daily_analytics(self, start: str, end: str) -> list[dict]:
        """Per-day views/likes/subscribersGained (needs yt-analytics.readonly scope)."""
        resp = self.session.get(ANALYTICS, headers=self._headers(), timeout=30, params={
            "ids": "channel==MINE", "startDate": start, "endDate": end, "dimensions": "day",
            "metrics": "views,likes,comments,subscribersGained,subscribersLost"})
        if resp.status_code != 200:
            raise YouTubeError(f"analytics failed (HTTP {resp.status_code})")
        data = resp.json()
        cols = [c["name"] for c in data.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in data.get("rows", [])]
