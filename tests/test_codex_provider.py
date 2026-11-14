"""Codex as the remote-render model: polling, failures, allowlist, prompt guard."""

import asyncio

import pytest

from lesmots.lexrender.codex import GUARD, CodexConfig, CodexError, codex_complete
from lesmots.lexrender.store import Store

LEXICON = """## 1. Phrases

### 1.1 `sophisticated`

* **Definition**: Refined and complex.
* **Synonyms**: refined, elaborate
"""


class FakeCodex:
    """Answers POST /v1/runs and GET /v1/runs/{id} from a scripted list of statuses."""

    def __init__(self, statuses, output="A {{sophisticated|sophisticated}} plan.", error=None):
        self.statuses = list(statuses)
        self.output = output
        self.error = error
        self.calls = []

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if method == "POST":
            return {"run_id": "run_1", "status": "queued",
                    "status_url": "/codex/v1/runs/run_1"}
        status = self.statuses.pop(0) if self.statuses else "completed"
        run = {"run_id": "run_1", "status": status}
        if status == "completed":
            run["final_output"] = self.output
        if self.error:
            run["error"] = self.error
        return run


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def config(**kw):
    return CodexConfig(base_url="https://gw/codex", users=frozenset({"g-owner"}), **kw)


def make(fake, cfg=None, clock=None):
    clock = clock or Clock()
    return codex_complete(cfg or config(), http=fake, sleep=clock.sleep, clock=clock)


def test_submits_then_polls_until_completed():
    fake = FakeCodex(["starting", "running", "completed"])
    out = make(fake)("PROMPT", "SYSTEM", 600)
    assert out == "A {{sophisticated|sophisticated}} plan."
    method, url, body = fake.calls[0]
    assert (method, url) == ("POST", "https://gw/codex/v1/runs")
    assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "low"
    assert "project_id" not in body  # never inside a repo checkout
    assert body["prompt"].startswith(GUARD)
    assert body["prompt"] == f"{GUARD}\n\nSYSTEM\n\n=== TASK ===\nPROMPT"
    assert [c[:2] for c in fake.calls[1:]] == [("GET", "https://gw/codex/v1/runs/run_1")] * 3


@pytest.mark.parametrize("status", ["failed", "interrupted", "cancelled", "timed_out"])
def test_unsuccessful_run_raises(status):
    fake = FakeCodex([status], error={"code": "x", "message": "boom"})
    with pytest.raises(CodexError, match=f"{status}: boom"):
        make(fake)("p", "s", 600)


def test_times_out():
    fake = FakeCodex(["running"] * 1000)
    with pytest.raises(CodexError, match="still running after 10s"):
        make(fake, config(timeout=10, poll=3))("p", "s", 600)
    assert len(fake.calls) <= 6  # stopped polling at the deadline


def test_empty_output_raises():
    with pytest.raises(CodexError, match="no output"):
        make(FakeCodex(["completed"], output=""))("p", "s", 600)


def test_missing_run_id_raises():
    def http(method, url, body):
        return {"error": "nope"}
    with pytest.raises(CodexError, match="no run_id"):
        codex_complete(config(), http=http, sleep=lambda s: None, clock=lambda: 0)("p", "s", 1)


def test_config_from_env(monkeypatch):
    monkeypatch.delenv("LEXRENDER_CODEX_URL", raising=False)
    assert CodexConfig.from_env() is None
    monkeypatch.setenv("LEXRENDER_CODEX_URL", "https://gw/codex/")
    monkeypatch.setenv("LEXRENDER_CODEX_USERS", " g-owner , wx-abc,")
    monkeypatch.setenv("LEXRENDER_CODEX_TIMEOUT", "60")
    cfg = CodexConfig.from_env()
    assert cfg.base_url == "https://gw/codex"
    assert cfg.users == {"g-owner", "wx-abc"}
    assert cfg.timeout == 60 and cfg.model == "gpt-6-luna" and cfg.effort == "low"


def test_allowlist():
    cfg = config()
    assert cfg.allows("g-owner")
    assert not cfg.allows("g-someone-else")
    assert not cfg.allows(None) and not cfg.allows("")
    assert CodexConfig("u", frozenset({"*"})).allows("anyone")
    assert not CodexConfig("u", frozenset()).allows("g-owner")


def test_server_uses_codex_only_for_listed_users(monkeypatch):
    pytest.importorskip("mcp")
    from lesmots.lexrender import codex as codex_mod
    from lesmots.lexrender.server import build_server

    sent = []

    def fake_complete(cfg, **kw):
        def complete(prompt, system, max_tokens):
            sent.append(prompt)
            return "A {{sophisticated|sophisticated}} plan."
        return complete

    monkeypatch.setattr("lesmots.lexrender.server.codex_complete", fake_complete)
    monkeypatch.setenv("LEXRENDER_CODEX_URL", "https://gw/codex")
    monkeypatch.setenv("LEXRENDER_CODEX_USERS", "g-owner")
    for var in ("ANTHROPIC_API_KEY", "LESMOTS_API_KEY", "LESMOTS_API_BASE"):
        monkeypatch.delenv(var, raising=False)
    assert codex_mod.CodexConfig.from_env() is not None

    store = Store(":memory:")
    owner = store.account_for_app_user("g-owner")
    other = store.account_for_app_user("g-other")
    for acct in (owner, other):
        store.import_markdown(acct, LEXICON)
        store.set_plan(acct, "paid")

    def render_as(acct):
        server = build_server(store, account_id=acct)
        result = asyncio.run(server.call_tool("render", {"text": "A refined plan.",
                                                         "surface": "test"}))
        assert not result.is_error, result.content
        return result.structured_content

    mine = render_as(owner)
    assert mine["fallback"] is None and mine["used"] == ["sophisticated"]
    assert len(sent) == 1

    theirs = render_as(other)  # not listed: default model, which isn't configured here
    assert theirs["fallback"] == "local"
    assert len(sent) == 1  # their text never went to Codex
