"""Optional free-tier LLM backends used to draft scripts inside GitHub Actions.

Nothing here is required: the pipeline also accepts scripts written by a
scheduled Claude Code session (your Claude Pro plan) or by hand, and it has an
offline template writer. Every LLM draft still has to pass the fact checker.

Note: GitHub Models was retired on 2026-07-30, so it is intentionally absent.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

import requests

from .config import env


class LLMError(RuntimeError):
    pass


class LLMBackend(Protocol):
    name: str

    def complete(self, system: str, user: str, *, temperature: float = 0.9) -> str: ...


class GeminiBackend:
    """Google Gemini API (free tier). Needs the GEMINI_API_KEY secret."""

    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str = "auto", session: requests.Session | None = None, timeout: int = 90):
        self.api_key = api_key
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        # Key goes in a header, never in the URL, so it can't leak into logs.
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def resolve_model(self) -> str:
        if self.model and self.model != "auto":
            return self.model if self.model.startswith("models/") else f"models/{self.model}"
        resp = self.session.get(f"{self.BASE}/models", headers=self._headers(), timeout=self.timeout)
        if resp.status_code != 200:
            raise LLMError(f"Gemini model list failed: HTTP {resp.status_code}")
        models = [
            m for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        flash = [m["name"] for m in models if "flash" in m["name"] and "image" not in m["name"] and "tts" not in m["name"]]
        if not flash:
            raise LLMError("no Gemini 'flash' model available for this key")

        def version_key(name: str) -> tuple:
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", name)]
            preview = "preview" in name or "exp" in name
            lite = "lite" in name
            return (not preview, not lite, nums)

        self.model = sorted(flash, key=version_key, reverse=True)[0]
        return self.model

    def complete(self, system: str, user: str, *, temperature: float = 0.9) -> str:
        model = self.resolve_model()
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        resp = self.session.post(f"{self.BASE}/{model}:generateContent", headers=self._headers(),
                                 data=json.dumps(body), timeout=self.timeout)
        if resp.status_code == 429:
            raise LLMError("Gemini free-tier quota reached (HTTP 429)")
        if resp.status_code != 200:
            raise LLMError(f"Gemini request failed: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Gemini returned no text: {str(data)[:200]}") from exc
        return "".join(p.get("text", "") for p in parts)


class OpenAICompatBackend:
    """Any OpenAI-compatible chat endpoint (e.g. a provider's free tier).

    Configure with secrets LLM_BASE_URL, LLM_MODEL and LLM_API_KEY.
    """

    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str, session: requests.Session | None = None, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout

    def complete(self, system: str, user: str, *, temperature: float = 0.9) -> str:
        body = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        resp = self.session.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=self.timeout,
        )
        if resp.status_code == 429:
            raise LLMError("LLM endpoint rate limit reached (HTTP 429)")
        if resp.status_code != 200:
            raise LLMError(f"LLM request failed: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError("LLM endpoint returned no message") from exc


def available_backends(names: list[str], cfg) -> list[LLMBackend]:
    """Instantiate the configured backends whose secrets are present."""
    out: list[LLMBackend] = []
    for name in names:
        if name == "gemini" and env("GEMINI_API_KEY"):
            out.append(GeminiBackend(env("GEMINI_API_KEY"), cfg.get("writer.gemini_model", "auto")))
        elif name == "openai" and env("LLM_BASE_URL") and env("LLM_API_KEY") and env("LLM_MODEL"):
            out.append(OpenAICompatBackend(env("LLM_BASE_URL"), env("LLM_API_KEY"), env("LLM_MODEL")))
    return out


def extract_json(text: str) -> dict:
    """Parse the first JSON object in an LLM reply (tolerates ```json fences)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("reply contained no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc
