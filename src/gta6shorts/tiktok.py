"""TikTok Content Posting API + Display API client.

Only usable once your TikTok developer app is approved. Two modes:
  inbox  – scope video.upload: the video lands in your TikTok inbox/drafts and
           you tap "Post" in the app (works for unaudited apps).
  direct – scope video.publish: posts directly. Until TikTok audits the app,
           direct posts are restricted to private (SELF_ONLY) visibility.
We never try to bypass these restrictions.

Secrets: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

BASE = "https://open.tiktokapis.com/v2"
SINGLE_CHUNK_MAX = 64 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


class TikTokClient:
    def __init__(self, client_key: str, client_secret: str, refresh_token: str, session: requests.Session | None = None):
        self.client_key, self.client_secret, self.refresh_token = client_key, client_secret, refresh_token
        self.session = session or requests.Session()
        self._token: str | None = None
        self.rotated_refresh_token: str | None = None

    def access_token(self) -> str:
        if self._token:
            return self._token
        resp = self.session.post(f"{BASE}/oauth/token/", timeout=30, data={
            "client_key": self.client_key, "client_secret": self.client_secret,
            "grant_type": "refresh_token", "refresh_token": self.refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or "access_token" not in data:
            raise TikTokError(f"token refresh failed (HTTP {resp.status_code}): {str(data)[:200]}")
        self._token = data["access_token"]
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self.refresh_token:
            self.rotated_refresh_token = new_refresh  # caller must tell the owner to update the secret
        return self._token

    def _post(self, path: str, body: dict) -> dict:
        resp = self.session.post(f"{BASE}{path}", data=json.dumps(body), timeout=60, headers={
            "Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json; charset=UTF-8"})
        data = resp.json() if resp.content else {}
        err = (data.get("error") or {})
        if resp.status_code != 200 or err.get("code", "ok") != "ok":
            raise TikTokError(f"{path} failed (HTTP {resp.status_code}): {err.get('code')} {err.get('message', '')}")
        return data.get("data", {})

    def creator_info(self) -> dict:
        return self._post("/post/publish/creator_info/query/", {})

    @staticmethod
    def source_info(size: int) -> dict:
        if size <= SINGLE_CHUNK_MAX:
            return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
        chunk = 10 * 1024 * 1024
        return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": size // chunk}

    def _put_file(self, upload_url: str, video: Path, info: dict) -> None:
        size, chunk, total = info["video_size"], info["chunk_size"], info["total_chunk_count"]
        with video.open("rb") as fh:
            for i in range(total):
                start = i * chunk
                end = size - 1 if i == total - 1 else start + chunk - 1
                fh.seek(start)
                data = fh.read(end - start + 1)
                resp = self.session.put(upload_url, data=data, timeout=300, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                if resp.status_code not in (200, 201, 206):
                    raise TikTokError(f"chunk {i + 1}/{total} upload failed (HTTP {resp.status_code})")

    def upload_to_inbox(self, video: Path) -> str:
        info = self.source_info(video.stat().st_size)
        data = self._post("/post/publish/inbox/video/init/", {"source_info": info})
        self._put_file(data["upload_url"], video, info)
        return data["publish_id"]

    def direct_post(self, video: Path, caption: str, privacy_level: str = "SELF_ONLY", is_aigc: bool = True) -> str:
        creator = self.creator_info()
        options = creator.get("privacy_level_options") or []
        if privacy_level not in options:
            raise TikTokError(f"privacy level {privacy_level} not allowed for this account (allowed: {options})")
        info = self.source_info(video.stat().st_size)
        data = self._post("/post/publish/video/init/", {
            "post_info": {"title": caption[:2200], "privacy_level": privacy_level, "disable_duet": False,
                          "disable_comment": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000,
                          "is_aigc": is_aigc},
            "source_info": info})
        self._put_file(data["upload_url"], video, info)
        return data["publish_id"]

    def publish_status(self, publish_id: str) -> dict:
        return self._post("/post/publish/status/fetch/", {"publish_id": publish_id})

    def user_stats(self) -> dict:
        resp = self.session.get(f"{BASE}/user/info/", timeout=30, params={
            "fields": "open_id,display_name,follower_count,following_count,likes_count,video_count"},
            headers={"Authorization": f"Bearer {self.access_token()}"})
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            raise TikTokError(f"user info failed (HTTP {resp.status_code})")
        u = data.get("data", {}).get("user", {})
        return {"followers": int(u.get("follower_count", 0)), "likes": int(u.get("likes_count", 0)),
                "videos": int(u.get("video_count", 0)), "name": u.get("display_name", "")}

    def video_stats(self, max_count: int = 20) -> dict[str, dict]:
        data = self._post("/video/list/?fields=id,title,create_time,view_count,like_count,comment_count,share_count",
                          {"max_count": max_count})
        return {v["id"]: {"views": int(v.get("view_count", 0)), "likes": int(v.get("like_count", 0)),
                          "comments": int(v.get("comment_count", 0)), "shares": int(v.get("share_count", 0)),
                          "title": v.get("title", "")} for v in data.get("videos", [])}
