from __future__ import annotations

import json

import pytest

from conftest import TODAY
from gta6shorts.factcheck import check_with_config, errors
from gta6shorts.facts import load_facts
from gta6shorts.llm import GeminiBackend, LLMError, OpenAICompatBackend, extract_json
from gta6shorts.script import load_scripts
from gta6shorts.writer import LLMWriter, TemplateWriter, write_new_scripts


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text or json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)


GOOD = {
    "title": "GTA 6 Reviews Are Wild",
    "hook_text": "THE REVIEWS ARE IN",
    "format": "news",
    "lines": [
        {"text": "The GTA 6 reviews just dropped, and wow.", "kind": "hook", "facts": ["gta6_metacritic_99"]},
        {"text": "It launched with a 99 on Metacritic, tying the highest score the site has ever given.",
         "kind": "claim", "facts": ["gta6_metacritic_99"], "card": {"type": "stat", "big": "99", "small": "METASCORE"}},
        {"text": "That even beats GTA 4's 98, making it Rockstar's highest-rated game on Metacritic.",
         "kind": "claim", "facts": ["gta6_beats_gta4"]},
        {"text": "Some reviewers did call out clunky controls and occasional performance problems.",
         "kind": "claim", "facts": ["gta6_review_criticism"]},
        {"text": "Honestly, when the worst complaint is the controls, you know Rockstar cooked something special here.",
         "kind": "opinion", "facts": []},
        {"text": "Would you trust critics on this one, or wait to play it yourself? Tell me below and follow for more.",
         "kind": "cta", "facts": []},
    ],
    "description": "What critics are saying about GTA 6.",
    "tags": ["GTA 6", "reviews"],
}


class FakeBackend:
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, user, *, temperature=0.9):
        self.prompts.append(user)
        return self.replies.pop(0)


@pytest.fixture
def real_fb(cfg):
    return load_facts(cfg.facts_file)


def test_template_writer_produces_valid_scripts(cfg, real_fb):
    made, log = write_new_scripts(3, real_fb, cfg, TODAY, history=[], backends=[], allow_template=True, seed=7)
    assert len(made) == 3, log
    for s in made:
        assert s.author == "template"
        assert errors(check_with_config(s, real_fb, cfg, today=TODAY, history=[o for o in made if o is not s])) == []


def test_template_writer_rotates_categories(cfg, real_fb):
    tw = TemplateWriter(real_fb, cfg, TODAY)
    first = tw.draft([], seed=1)
    second = tw.draft([first], seed=2)
    assert set(first.fact_ids()) != set(second.fact_ids())


def test_llm_writer_accepts_good_draft(cfg, real_fb):
    backend = FakeBackend([json.dumps(GOOD)])
    result = LLMWriter(backend, real_fb, cfg, TODAY).draft([], seed=1)
    assert result.script is not None, result.issues
    assert result.attempts == 1
    assert result.script.author == "fake"


def test_llm_writer_repairs_hallucinated_number(cfg, real_fb):
    bad = json.loads(json.dumps(GOOD))
    bad["lines"][1]["text"] = "It launched with a 97 on Metacritic, tying the highest score ever."
    backend = FakeBackend(["```json\n" + json.dumps(bad) + "\n```", json.dumps(GOOD)])
    result = LLMWriter(backend, real_fb, cfg, TODAY).draft([], seed=1)
    assert result.script is not None
    assert result.attempts == 2
    assert "number_mismatch" in backend.prompts[1]


def test_llm_writer_gives_up_when_still_wrong(cfg, real_fb):
    bad = json.loads(json.dumps(GOOD))
    bad["lines"][1]["text"] = "It launched with a 97 on Metacritic."
    backend = FakeBackend([json.dumps(bad), json.dumps(bad)])
    result = LLMWriter(backend, real_fb, cfg, TODAY).draft([], seed=1)
    assert result.script is None
    assert result.error


def test_write_new_scripts_falls_back_to_template(cfg, real_fb):
    class Broken:
        name = "broken"

        def complete(self, *a, **k):
            raise LLMError("quota")

    made, log = write_new_scripts(1, real_fb, cfg, TODAY, history=load_scripts(cfg.scripts_dir), backends=[Broken()])
    assert len(made) == 1 and made[0].author == "template"
    assert any("broken" in line for line in log)


def test_extract_json_handles_fences_and_noise():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('text {"b": [1,2]} trailing') == {"b": [1, 2]}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_gemini_picks_newest_stable_flash_and_uses_header_key():
    models = {"models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.9-flash-preview", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-4-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]},
    ]}
    reply = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    session = FakeSession([FakeResp(200, models), FakeResp(200, reply)])
    backend = GeminiBackend("SECRET", "auto", session=session)
    assert backend.complete("sys", "user") == '{"ok": true}'
    assert backend.model == "models/gemini-3.8-flash"
    method, url, kw = session.calls[1]
    assert url.endswith("models/gemini-3.8-flash:generateContent")
    assert "SECRET" not in url and kw["headers"]["x-goog-api-key"] == "SECRET"


def test_gemini_quota_error_is_reported():
    session = FakeSession([FakeResp(429, {"error": "quota"})])
    with pytest.raises(LLMError, match="quota"):
        GeminiBackend("k", "gemini-x", session=session).complete("s", "u")


def test_openai_compat_backend():
    session = FakeSession([FakeResp(200, {"choices": [{"message": {"content": "{}"}}]})])
    out = OpenAICompatBackend("https://api.example.com/v1/", "KEY", "some-model", session=session).complete("s", "u")
    assert out == "{}"
    method, url, kw = session.calls[0]
    assert url == "https://api.example.com/v1/chat/completions"
    assert kw["headers"]["Authorization"] == "Bearer KEY"
