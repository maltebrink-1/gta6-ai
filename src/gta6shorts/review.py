"""Human review queue on GitHub (works from the GitHub mobile app, PC can stay off).

Every rendered video is attached to the "review-queue" pre-release and gets a
review issue showing the video link, contact sheet, script, sources and QA.
You decide by adding a label:  approved  or  rejected.
Only people with write/triage access can add labels, so strangers can't
approve anything even though the repository is public.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
LABELS = {
    "review": ("fbca04", "Rendered video waiting for your decision"),
    "approved": ("0e8a16", "Approved — publish when publishing is enabled"),
    "rejected": ("b60205", "Rejected — will not be published"),
    "published": ("1d76db", "Published to at least one platform"),
}
VIDEO_ID_RE = re.compile(r"<!--\s*video-id:\s*([a-z0-9-]+)\s*-->")


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str, repo: str, session: requests.Session | None = None):
        self.repo = repo
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}

    def _req(self, method: str, url: str, ok=(200, 201), **kw):
        resp = self.session.request(method, url, headers={**self.headers, **kw.pop("headers", {})}, timeout=120, **kw)
        if resp.status_code not in ok:
            raise GitHubError(f"{method} {url} → HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    def ensure_labels(self) -> None:
        for name, (color, desc) in LABELS.items():
            self._req("POST", f"{API}/repos/{self.repo}/labels", ok=(201, 422),
                      json={"name": name, "color": color, "description": desc})

    def ensure_release(self, tag: str) -> dict:
        resp = self.session.get(f"{API}/repos/{self.repo}/releases/tags/{tag}", headers=self.headers, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        body = ("Videos waiting for review. Files here are temporary: they are deleted after "
                "publishing or rejection. Not affiliated with Rockstar Games.")
        return self._req("POST", f"{API}/repos/{self.repo}/releases",
                         json={"tag_name": tag, "name": "Review queue", "body": body, "prerelease": True}).json()

    def upload_asset(self, release: dict, path: Path, name: str, content_type: str) -> str:
        self.delete_asset(release, name)
        with path.open("rb") as fh:
            resp = self._req("POST", f"{UPLOADS}/repos/{self.repo}/releases/{release['id']}/assets",
                             params={"name": name}, data=fh, headers={"Content-Type": content_type})
        return resp.json()["browser_download_url"]

    def delete_asset(self, release: dict, name: str) -> bool:
        assets = self._req("GET", f"{API}/repos/{self.repo}/releases/{release['id']}/assets",
                           params={"per_page": 100}).json()
        for a in assets:
            if a["name"] == name:
                self._req("DELETE", f"{API}/repos/{self.repo}/releases/assets/{a['id']}", ok=(204,))
                return True
        return False

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        return self._req("POST", f"{API}/repos/{self.repo}/issues",
                         json={"title": title, "body": body, "labels": labels}).json()["number"]

    def get_issue(self, number: int) -> dict:
        return self._req("GET", f"{API}/repos/{self.repo}/issues/{number}").json()

    def comment(self, number: int, body: str) -> None:
        self._req("POST", f"{API}/repos/{self.repo}/issues/{number}/comments", json={"body": body})

    def close_issue(self, number: int, reason: str = "completed") -> None:
        self._req("PATCH", f"{API}/repos/{self.repo}/issues/{number}", json={"state": "closed", "state_reason": reason})

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._req("POST", f"{API}/repos/{self.repo}/issues/{number}/labels", json={"labels": labels})

    def download(self, url: str, dest: Path) -> Path:
        with self.session.get(url, headers={"Accept": "application/octet-stream", **{
                k: v for k, v in self.headers.items() if k == "Authorization"}}, stream=True, timeout=300) as resp:
            if resp.status_code != 200:
                raise GitHubError(f"download failed (HTTP {resp.status_code})")
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
        return dest


def issue_body(record: dict, script, meta: dict, qa, video_url: str, sheet_url: str, publishing_on: bool) -> str:
    lines = [f"<!-- video-id: {record['id']} -->",
             f"### ▶️ [Watch the video]({video_url})", "",
             f"![contact sheet]({sheet_url})", "",
             "| | |", "|---|---|",
             f"| Duration | {record.get('duration')} s |",
             f"| Look | {record.get('theme')} |",
             f"| Voice | {record.get('engine')} · {record.get('voice')} |",
             f"| Script by | {record.get('author')} |",
             f"| QA | {'✅ passed' if qa.passed else '❌ failed'} ({sum(c.passed for c in qa.checks)}/{len(qa.checks)} checks) |",
             "", "<details><summary>QA details</summary>", "", "```", qa.summary(), "```", "</details>", "",
             "### Script"]
    for i, line in enumerate(script.lines, 1):
        facts = f" — _facts: {', '.join(line.facts)}_" if line.facts else ""
        lines.append(f"{i}. **{line.kind}** · {line.text}{facts}")
    lines += ["", "### Title & description that will be uploaded", f"**{meta['title']}**", "", "```",
              meta["description"], "```", "", "### Your decision"]
    if publishing_on:
        lines.append("Add the label **approved** to publish it, or **rejected** to discard it.")
    else:
        lines.append("Publishing is **OFF** (Stage 2 — testing). Add **approved** or **rejected** to record your "
                     "verdict; nothing will be uploaded until publishing is switched on.")
    return "\n".join(lines)


def video_id_from_issue(body: str) -> str | None:
    m = VIDEO_ID_RE.search(body or "")
    return m.group(1) if m else None


def load_meta(out_dir: Path) -> dict:
    return json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
