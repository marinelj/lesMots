"""Lexicon Render: parser, word-form matcher, lexicon bank and MCP tools."""

import asyncio
from pathlib import Path

import pytest

from lesmots.lexrender.forms import Matcher, inflections
from lesmots.lexrender.parse import github_anchor, parse_lexicon
from lesmots.lexrender.store import LexiconError, Store

LEXICON = Path(__file__).resolve().parents[1] / "lexicon" / "lexicon.md"

SAMPLE = """# Lexicon

## 1. Idioms (3 Items)

### 1.1 `fall back to`
* **Definition**: Revert to a backup.

### 1.2 `well-worn` vs. `well-trodden`
* **Definition**: Familiar from use.

### 1.3 `I wouldn't put it past someone`
Believe someone capable of it.

---

## 6. Unified Slash-Command & Knowledge Sync Protocol

### 6.1 Interactive Slash Commands
Not an entry.
"""


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def account(store):
    account_id, _ = store.create_account("t", link_base="https://x/lexicon.md")
    store.import_markdown(account_id, SAMPLE)
    return account_id


# ---------- parse ----------

def test_parse_sample():
    entries = parse_lexicon(SAMPLE)
    assert [e.id for e in entries] == ["fall-back-to", "well-worn", "i-wouldnt-put-it-past-someone"]
    assert entries[0].definition == "Revert to a backup."
    assert entries[1].aliases == ["well-worn", "well-trodden"]
    assert entries[2].definition == "Believe someone capable of it."
    assert entries[2].anchor == "13-i-wouldnt-put-it-past-someone"


def test_github_anchor():
    assert github_anchor("2.19 Get started with ...") == "219-get-started-with-"
    assert github_anchor("1.57 degrade gracefully") == "157-degrade-gracefully"


def test_parse_real_lexicon():
    entries = parse_lexicon(LEXICON.read_text(encoding="utf-8"))
    assert len(entries) > 500
    ids = [e.id for e in entries]
    assert len(ids) == len(set(ids))
    assert not any(e.section.lower().startswith("unified slash") for e in entries)


# ---------- forms ----------

@pytest.mark.parametrize("base, form", [
    ("fall", "fell"), ("fall", "falling"), ("degrade", "degrading"), ("degrade", "degraded"),
    ("carry", "carried"), ("carry", "carries"), ("stop", "stopped"), ("hit", "hitting"),
    ("take", "took"), ("rely", "relies"), ("fix", "fixes"),
])
def test_inflections(base, form):
    assert form in inflections(base)


def test_matcher_finds_inflected_and_placeholder_phrases():
    m = Matcher([
        ("fall-back-to", ["Fall back to ..."]),
        ("fall", ["fall"]),
        ("degrade", ["degrade gracefully"]),
        ("past", ["I wouldn't put it past someone"]),
        ("align", ["align on [topic]"]),
    ])
    text = "We fell back to the cache, degrading gracefully. I wouldn’t put it past them."
    found = [(x.entry_id, x.text) for x in m.find(text)]
    assert found == [("fall-back-to", "fell back to"), ("degrade", "degrading gracefully"),
                     ("past", "I wouldn’t put it past")]
    assert m.resolve("falling back to") == "fall-back-to"
    assert m.resolve("align on") == "align"
    assert m.resolve("fallback") is None


def test_matcher_respects_word_boundaries():
    m = Matcher([("hit", ["hit"])])
    assert m.find("white hitch") == []


# ---------- store ----------

def test_import_is_idempotent_and_archives_missing(store, account):
    assert store.version(account) == 1
    again = store.import_markdown(account, SAMPLE)
    assert again["unchanged"] == 3 and again["version"] == 1
    removed = "### 1.2 `well-worn` vs. `well-trodden`\n* **Definition**: Familiar from use.\n"
    shorter = SAMPLE.replace(removed, "")
    result = store.import_markdown(account, shorter)
    assert result["archived"] == 1 and result["version"] == 2
    changed = store.entries(account, since_version=1, include_archived=True)
    assert [e.id for e in changed] == ["well-worn"]
    assert store.get(account, "well-worn").status == "archived"


def test_import_rejects_file_without_entries(store, account):
    with pytest.raises(LexiconError):
        store.import_markdown(account, "# nothing here")


def test_lookup_by_id_and_word_form(store, account):
    assert store.lookup(account, "fall-back-to").phrase == "fall back to"
    entry = store.lookup(account, "fell back to")
    assert entry.id == "fall-back-to"
    assert entry.link == "https://x/lexicon.md#11-fall-back-to"
    assert store.lookup(account, "well-trodden").id == "well-worn"


def test_accounts_are_isolated(store, account):
    other, token = store.create_account("other")
    assert store.account_for_token(token) == other
    assert store.entries(other) == []
    assert store.lookup(other, "fall back to") is None
    assert store.account_for_token("lr_wrong") is None


def test_add_entry_refuses_duplicates_in_any_form(store, account):
    with pytest.raises(LexiconError, match="fall-back-to"):
        store.add_entry(account, "fell back to", "dup")
    entry = store.add_entry(account, "take a backseat", "Become less important.",
                            "Docs took a backseat.")
    assert entry.id == "take-a-backseat" and "Docs took a backseat." in entry.body
    assert store.version(account) == 2
    assert store.lookup(account, "taking a backseat").id == "take-a-backseat"


def test_log_exposures_counts_once_per_answer(store, account):
    first = store.log_exposures(account, "evt-1", ["fall-back-to", "fall-back-to", "nope"],
                                surface="Claude Code", label="lesMots")
    assert first == {"accepted": 1, "duplicate": 0, "unknown": ["nope"]}
    retry = store.log_exposures(account, "evt-1", ["fall-back-to"], surface="Claude Code")
    assert retry["accepted"] == 0 and retry["duplicate"] == 1
    store.log_exposures(account, "evt-2", ["fall-back-to", "well-worn"], surface="Notion",
                        occurred_at="2026-11-13T07:58:00-08:00")
    assert store.exposure_counts(account) == {"fall-back-to": 2, "well-worn": 1}


@pytest.mark.parametrize("kwargs", [
    {"event_id": "", "surface": "x"}, {"event_id": "e", "surface": ""},
    {"event_id": "e", "surface": "x", "mode": "link_only"},
    {"event_id": "e", "surface": "x", "occurred_at": "yesterday"},
])
def test_log_exposures_validates(store, account, kwargs):
    with pytest.raises(LexiconError):
        store.log_exposures(account, entry_ids=["fall-back-to"], **kwargs)


def test_export_round_trips(store, account):
    exported = store.export_markdown(account)
    other, _ = store.create_account("copy")
    store.import_markdown(other, exported)
    assert [(e.id, e.aliases) for e in store.entries(other)] == \
           [(e.id, e.aliases) for e in store.entries(account)]


def test_real_lexicon_round_trips(store):
    acct, _ = store.create_account("real")
    store.import_markdown(acct, LEXICON.read_text(encoding="utf-8"))
    copy, _ = store.create_account("copy")
    store.import_markdown(copy, store.export_markdown(acct))
    assert [e.id for e in store.entries(copy)] == [e.id for e in store.entries(acct)]


# ---------- MCP server ----------

def _call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result.content
    return result.structured_content


def test_mcp_tools(store, account):
    pytest.importorskip("mcp")
    from lesmots.lexrender.server import build_server

    server = build_server(store, account_id=account)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"get_lexicon", "get_entry", "log_exposure", "add_entry"}

    index = _call(server, "get_lexicon", {})
    assert index["version"] == 1 and index["link_base"] == "https://x/lexicon.md"
    assert ["fall-back-to", "fall back to", "11-fall-back-to"] in index["entries"]
    assert _call(server, "get_lexicon", {"since_version": 1})["entries"] == []

    assert _call(server, "get_entry", {"id_or_phrase": "falling back to"})["id"] == "fall-back-to"
    logged = _call(server, "log_exposure", {"event_id": "a", "entry_ids": ["well-worn"],
                                            "surface": "Claude Code"})
    assert logged["accepted"] == 1


def test_http_server_requires_public_url(store):
    pytest.importorskip("mcp")
    from lesmots.lexrender.server import TokenVerifier, build_server

    with pytest.raises(ValueError):
        build_server(store)
    account_id, token = store.create_account("h")
    verifier = TokenVerifier(store)
    assert asyncio.run(verifier.verify_token(token)).client_id == account_id
    assert asyncio.run(verifier.verify_token("bad")) is None
    assert build_server(store, public_url="https://lexicon.example.com") is not None
