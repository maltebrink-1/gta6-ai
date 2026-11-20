from __future__ import annotations

import json

import pytest

from gta6shorts.gate import publish_blockers
from gta6shorts.qa import parse_black, parse_freeze, parse_loudness, parse_silence
from gta6shorts.registry import Registry
from gta6shorts.review import video_id_from_issue
from gta6shorts.tiktok import TikTokClient, TikTokError
from gta6shorts.youtube import YouTubeClient, YouTubeError


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _go(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)

    def post(self, url, **kw):
        return self._go("POST", url, **kw)

    def put(self, url, **kw):
        return self._go("PUT", url, **kw)

    def get(self, url, **kw):
        return self._go("GET", url, **kw)


# ── QA log parsing ───────────────────────────────────────────────────────────
LOG = """
[blackdetect @ 0x1] black_start:0 black_end:0.6 black_duration:0.6
[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 3.2
[silencedetect @ 0x3] silence_start: 10.5
[silencedetect @ 0x3] silence_end: 12.1 | silence_duration: 1.6
[Parsed_ebur128_0 @ 0x4] Summary:

  Integrated loudness:
    I:         -14.2 LUFS
  True peak:
    Peak:       -1.4 dBFS
"""


def test_qa_parsers():
    assert parse_loudness(LOG) == (-14.2, -1.4)
    assert parse_black(LOG) == [(0.0, 0.6)]
    assert parse_freeze(LOG) == [3.2]
    assert parse_silence(LOG) == [(10.5, 12.1)]


# ── Publishing gate ──────────────────────────────────────────────────────────
GOOD_RECORD = {"qa": {"passed": True}, "review": {"decision": "approved"}, "engine": "kokoro", "author": "claude",
               "platforms": {}}


@pytest.fixture
def secrets(monkeypatch):
    for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")


def enable(cfg):
    cfg.data["publishing"]["enabled"] = True
    cfg.data["publishing"]["youtube"]["enabled"] = True


def test_gate_blocks_everything_by_default(cfg, monkeypatch):
    cfg.data["publishing"]["enabled"] = False
    cfg.data["publishing"]["youtube"]["enabled"] = False
    for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    reasons = publish_blockers(GOOD_RECORD, "youtube", cfg, published_today=0, facts_ok=True)
    assert any("master switch" in r for r in reasons)
    assert any("missing secrets" in r for r in reasons)


def test_gate_allows_fully_approved_video(cfg, secrets):
    enable(cfg)
    assert publish_blockers(GOOD_RECORD, "youtube", cfg, published_today=0, facts_ok=True) == []


@pytest.mark.parametrize("patch,expected", [
    ({"qa": {"passed": False}}, "QA"),
    ({"review": {"decision": None}}, "approval"),
    ({"review": {"decision": "rejected"}}, "rejected"),
    ({"engine": "espeak"}, "fallback voice"),
    ({"author": "template"}, "template"),
    ({"platforms": {"youtube": {"id": "abc"}}}, "already published"),
])
def test_gate_blockers(cfg, secrets, patch, expected):
    enable(cfg)
    rec = {**GOOD_RECORD, **patch}
    reasons = publish_blockers(rec, "youtube", cfg, published_today=0, facts_ok=True)
    assert any(expected in r for r in reasons), reasons


def test_gate_daily_cap_and_facts(cfg, secrets):
    enable(cfg)
    assert any("daily cap" in r for r in publish_blockers(GOOD_RECORD, "youtube", cfg, published_today=3, facts_ok=True))
    assert any("re-verification" in r for r in publish_blockers(GOOD_RECORD, "youtube", cfg, published_today=0, facts_ok=False))


def test_auto_mode_needs_no_approval(cfg, secrets):
    enable(cfg)
    cfg.data["publishing"]["mode"] = "auto"
    rec = {**GOOD_RECORD, "review": {"decision": None}}
    assert publish_blockers(rec, "youtube", cfg, published_today=0, facts_ok=True) == []


# ── Registry ─────────────────────────────────────────────────────────────────
def test_registry_lifecycle(tmp_path):
    reg = Registry(tmp_path / "videos.json")
    reg.videos["v1"] = {"id": "v1", "status": "awaiting_review", "platforms": {}, "review": {}}
    reg.decide("v1", "approved", "me")
    assert reg.get("v1")["status"] == "approved"
    reg.mark_published("v1", "youtube", id="yt1", url="u")
    reg.save()
    again = Registry(tmp_path / "videos.json")
    assert again.get("v1")["status"] == "published"
    day = again.get("v1")["platforms"]["youtube"]["published_at"][:10]
    assert again.published_on(day, "youtube") == 1
    with pytest.raises(ValueError):
        again.decide("v1", "maybe")


def test_video_id_marker():
    assert video_id_from_issue("text\n<!-- video-id: 2026-11-19-abc -->\nmore") == "2026-11-19-abc"
    assert video_id_from_issue("no marker") is None


# ── YouTube client (mocked HTTP) ─────────────────────────────────────────────
def test_youtube_body_disclosure_and_schedule():
    body = YouTubeClient.build_body("T" * 150, "d", ["a"], privacy="public", publish_at="2026-11-20T17:00:00Z")
    assert len(body["snippet"]["title"]) == 100
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"]


def test_youtube_resumable_upload(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 1000)
    session = Session([
        Resp(200, {"access_token": "AT", "expires_in": 3600}),
        Resp(200, {}, {"Location": "https://upload.example/session"}),
        Resp(200, {"id": "VID123", "status": {"privacyStatus": "private"}}),
    ])
    yt = YouTubeClient("cid", "secret", "refresh", session=session, sleep=lambda s: None)
    out = yt.upload(video, YouTubeClient.build_body("t", "d", []))
    assert out["id"] == "VID123"
    init = session.calls[1]
    assert "uploadType=resumable" in init[1]
    assert init[2]["headers"]["X-Upload-Content-Length"] == "1000"
    put = session.calls[2]
    assert put[2]["headers"]["Content-Range"] == "bytes 0-999/1000"
    assert put[2]["headers"]["Authorization"] == "Bearer AT"


def test_youtube_upload_retries_server_errors(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    session = Session([
        Resp(200, {"access_token": "AT", "expires_in": 3600}),
        Resp(200, {}, {"Location": "https://upload.example/s"}),
        Resp(503, {}),
        Resp(308, {}, {"Range": "bytes=0-4"}),
        Resp(201, {"id": "OK"}),
    ])
    yt = YouTubeClient("c", "s", "r", session=session, sleep=lambda s: None)
    assert yt.upload(video, {})["id"] == "OK"
    assert session.calls[-1][2]["headers"]["Content-Range"] == "bytes 5-9/10"


def test_youtube_token_error_is_explained():
    yt = YouTubeClient("c", "s", "r", session=Session([Resp(400, {"error": "invalid_grant"})]))
    with pytest.raises(YouTubeError, match="refresh token"):
        yt.access_token()


def test_youtube_stats_parsing():
    session = Session([
        Resp(200, {"access_token": "AT", "expires_in": 3600}),
        Resp(200, {"items": [{"id": "a", "statistics": {"viewCount": "10", "likeCount": "2", "commentCount": "1"},
                              "snippet": {"title": "A"}}]}),
        Resp(200, {"items": [{"id": "UC1", "snippet": {"title": "Chan"},
                              "statistics": {"subscriberCount": "5", "viewCount": "10", "videoCount": "1"}}]}),
    ])
    yt = YouTubeClient("c", "s", "r", session=session)
    assert yt.video_stats(["a"]) == {"a": {"views": 10, "likes": 2, "comments": 1, "title": "A"}}
    assert yt.channel_stats()["subscribers"] == 5


# ── TikTok client (mocked HTTP) ──────────────────────────────────────────────
def test_tiktok_inbox_upload_and_token_rotation(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"1" * 2048)
    session = Session([
        Resp(200, {"access_token": "TT", "refresh_token": "NEW"}),
        Resp(200, {"data": {"publish_id": "P1", "upload_url": "https://up.example"}, "error": {"code": "ok"}}),
        Resp(201, {}),
    ])
    tt = TikTokClient("key", "secret", "OLD", session=session)
    assert tt.upload_to_inbox(video) == "P1"
    init_body = json.loads(session.calls[1][2]["data"])
    assert init_body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 2048, "chunk_size": 2048, "total_chunk_count": 1}
    assert "/inbox/video/init/" in session.calls[1][1]
    assert session.calls[2][2]["headers"]["Content-Range"] == "bytes 0-2047/2048"
    assert tt.rotated_refresh_token == "NEW"


def test_tiktok_direct_post_respects_allowed_privacy(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"1" * 10)
    session = Session([
        Resp(200, {"access_token": "TT"}),
        Resp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}}),
    ])
    tt = TikTokClient("k", "s", "r", session=session)
    with pytest.raises(TikTokError, match="not allowed"):
        tt.direct_post(video, "caption", privacy_level="PUBLIC_TO_EVERYONE")


def test_tiktok_direct_post_labels_ai_content(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"1" * 10)
    session = Session([
        Resp(200, {"access_token": "TT"}),
        Resp(200, {"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}}),
        Resp(200, {"data": {"publish_id": "P2", "upload_url": "https://up"}, "error": {"code": "ok"}}),
        Resp(201, {}),
    ])
    tt = TikTokClient("k", "s", "r", session=session)
    assert tt.direct_post(video, "caption") == "P2"
    body = json.loads(session.calls[2][2]["data"])
    assert body["post_info"]["is_aigc"] is True and body["post_info"]["privacy_level"] == "SELF_ONLY"


# ── GitHub review queue (mocked HTTP) ────────────────────────────────────────
class GHSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def test_review_queue_upload_and_issue(tmp_path):
    from gta6shorts.review import GitHub

    video = tmp_path / "v.mp4"
    video.write_bytes(b"data")
    session = GHSession([
        Resp(404, {}),                                             # release lookup
        Resp(201, {"id": 7, "tag_name": "review-queue"}),          # create release
        Resp(200, [{"id": 99, "name": "x.mp4"}]),                  # list assets (old copy exists)
        Resp(204, {}),                                             # delete old asset
        Resp(201, {"browser_download_url": "https://dl/x.mp4"}),   # upload
        Resp(201, {"number": 12}),                                 # create issue
    ])
    gh = GitHub("TOKEN", "owner/repo", session=session)
    release = gh.ensure_release("review-queue")
    assert json.loads(json.dumps(session.calls[1][2]["json"]))["prerelease"] is True
    assert gh.upload_asset(release, video, "x.mp4", "video/mp4") == "https://dl/x.mp4"
    method, url, kw = session.calls[4]
    assert method == "POST" and url.startswith("https://uploads.github.com/repos/owner/repo/releases/7/assets")
    assert kw["params"] == {"name": "x.mp4"} and kw["headers"]["Content-Type"] == "video/mp4"
    assert session.calls[3][0] == "DELETE"
    assert gh.create_issue("t", "b", ["review"]) == 12
    assert session.calls[5][2]["json"]["labels"] == ["review"]
    assert all(c[2]["headers"]["Authorization"] == "Bearer TOKEN" for c in session.calls if "headers" in c[2])
