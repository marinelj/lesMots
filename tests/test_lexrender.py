"""Lexicon Render: parser, word-form matcher, lexicon bank and MCP tools."""

import asyncio
from pathlib import Path

import pytest

from lesmots.lexrender.forms import Matcher, inflections
from lesmots.lexrender.parse import github_anchor, parse_lexicon
from lesmots.lexrender.render import render
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
    account_id = store.create_account("t", link_base="https://x/lexicon.md")
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
    other = store.create_account("other")
    assert store.entries(other) == []
    assert store.lookup(other, "fall back to") is None


def test_app_user_id_signs_in_to_one_account(store):
    first = store.account_for_app_user("1234567890")
    assert first.startswith("acct_")
    assert store.account_for_app_user("1234567890") == first  # same user, same account
    assert store.account_for_app_user("wx-openid") != first
    assert store.account_for_app_user("nobody", create=False) is None
    assert store.account_for_app_user("") is None


def test_sign_in_needs_a_token_signed_by_the_app(monkeypatch):
    monkeypatch.setenv("LESMOTS_SESSION_SECRET", "test-secret")
    from lesmots import web
    from lesmots.lexrender.signin import verify_sign_in

    assert verify_sign_in(web.make_session("wx-abc", "")) == "wx-abc"
    assert verify_sign_in("1234567890") is None  # a bare user_id is not a login
    forged = web.make_session("victim", "")
    monkeypatch.setenv("LESMOTS_SESSION_SECRET", "another-secret")
    assert verify_sign_in(forged) is None
    assert verify_sign_in("") is None


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
    other = store.create_account("copy")
    store.import_markdown(other, exported)
    assert [(e.id, e.aliases) for e in store.entries(other)] == \
           [(e.id, e.aliases) for e in store.entries(account)]


def test_real_lexicon_round_trips(store):
    acct = store.create_account("real")
    store.import_markdown(acct, LEXICON.read_text(encoding="utf-8"))
    copy = store.create_account("copy")
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
    assert names == {"get_lexicon", "get_entry", "log_exposure", "add_entry", "set_synonyms",
                     "set_render_mode", "render"}

    index = _call(server, "get_lexicon", {})
    assert index["version"] == 1 and index["link_base"] == "https://x/lexicon.md"
    assert ["fall-back-to", "fall back to", "11-fall-back-to", []] in index["entries"]
    assert index["render_mode"] == "local"
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
    verifier = TokenVerifier(store, verify={"good": "g-123"}.get)
    first = asyncio.run(verifier.verify_token("good"))
    assert first.client_id == store.account_for_app_user("g-123", create=False)
    assert asyncio.run(verifier.verify_token("good")).client_id == first.client_id
    assert asyncio.run(verifier.verify_token("bad")) is None
    assert build_server(store, public_url="https://lexicon.example.com") is not None


# ---------- synonyms ----------

SYN_SAMPLE = SAMPLE.replace(
    "### 1.3 `I wouldn't put it past someone`",
    "### 1.4 `sophisticated`\n* **Definition**: Complex and refined.\n"
    "* **Synonyms**: refined, elaborate; `polished`\n\n"
    "### 1.3 `I wouldn't put it past someone`")


def test_parse_synonyms_line():
    entry = next(e for e in parse_lexicon(SYN_SAMPLE) if e.id == "sophisticated")
    assert entry.synonyms == ["refined", "elaborate", "polished"]


def test_matcher_finds_synonyms_and_prefers_the_entry_phrase():
    m = Matcher([("soph", ["sophisticated"]), ("elab", ["elaborate"])],
                [("soph", ["refined", "elaborate"])])
    found = m.find("A refined design with an elaborate plan.")
    assert [(x.entry_id, x.kind, x.via, x.text) for x in found] == [
        ("soph", "synonym", "refined", "refined"),
        ("elab", "phrase", "", "elaborate"),
    ]
    assert m.resolve("refined") is None
    assert m.find("They refined it")[0].via == "refined"


def test_synonyms_in_store(store):
    acct = store.create_account("s", link_base="https://x/l.md")
    store.import_markdown(acct, SYN_SAMPLE)
    entry = store.get(acct, "sophisticated")
    assert entry.synonyms == ["refined", "elaborate", "polished"]
    assert entry.full()["synonyms_link"] == entry.link
    hit = store.matcher(acct).find("a polished answer")[0]
    assert (hit.entry_id, hit.kind) == ("sophisticated", "synonym")

    updated = store.set_synonyms(acct, "sophisticated", ["subtle", "Sophisticated", "subtle"])
    assert updated.synonyms == ["subtle"]
    assert updated.body.count("**Synonyms**") == 1
    assert store.matcher(acct).find("a polished answer") == []
    assert store.set_synonyms(acct, "sophisticated", []).synonyms == []

    added = store.add_entry(acct, "leverage", "Use to advantage.", synonyms=["use", "exploit"])
    assert added.synonyms == ["use", "exploit"]
    # a synonym is not the entry: adding it as its own entry is allowed
    assert store.add_entry(acct, "exploit", "Make full use of.").id == "exploit"
    exported = store.export_markdown(acct)
    copy = store.create_account("c")
    store.import_markdown(copy, exported)
    assert store.get(copy, "leverage").synonyms == ["use", "exploit"]


def test_render_mode(store, account):
    assert store.render_mode(account) == "local"
    assert store.set_render_mode(account, "remote") == "remote"
    assert store.render_mode(account) == "remote"
    with pytest.raises(LexiconError):
        store.set_render_mode(account, "full")


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT "
        "NULL UNIQUE, link_base TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL);"
        "CREATE TABLE exposures (account_id TEXT NOT NULL, event_id TEXT NOT NULL, entry_id "
        "TEXT NOT NULL, occurred_at TEXT NOT NULL, surface TEXT NOT NULL, label TEXT NOT NULL "
        "DEFAULT '', mode TEXT NOT NULL, received_at TEXT NOT NULL, "
        "PRIMARY KEY (account_id, event_id, entry_id));"
        "INSERT INTO accounts VALUES ('a', 'n', 'h', '', 0, 'now');")
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.render_mode("a") == "local"


def test_log_exposures_records_synonym(store, account):
    store.log_exposures(account, "e", ["well-worn"], surface="x", via={"well-worn": "familiar"})
    row = store._conn.execute("SELECT via FROM exposures").fetchone()
    assert row["via"] == "familiar"


# ---------- server-side render ----------

@pytest.fixture
def syn_account(store):
    acct = store.create_account("r", link_base="https://x/l.md")
    store.import_markdown(acct, SYN_SAMPLE)
    return acct


def test_render_links_phrases_and_replaces_synonyms(store, syn_account):
    seen = {}

    def fake(prompt, system, max_tokens):
        seen["prompt"] = prompt
        return ("We {{fall-back-to|fell back to}} the cache. It is a "
                "{{sophisticated|sophisticated}} design. {{no-such-id|kept}}.")

    out = render(store, syn_account, "We used the cache. It is a refined design. Kept.",
                 surface="Claude Code", complete=fake)
    assert out["fallback"] is None
    assert out["rendered"] == (
        "We [fell back to](https://x/l.md#11-fall-back-to) the cache. It is a "
        "[sophisticated](https://x/l.md#14-sophisticated) design. kept.")
    assert out["used"] == ["fall-back-to", "sophisticated"]
    assert out["via"] == {"sophisticated": "refined"}
    assert '"refined" -> sophisticated' in seen["prompt"]
    assert store.exposure_counts(syn_account) == {"fall-back-to": 1, "sophisticated": 1}
    modes = {r["mode"] for r in store._conn.execute("SELECT mode FROM exposures")}
    assert modes == {"full"}


def test_render_drops_markers_around_the_wrong_words(store, syn_account):
    # a real Codex reply: it tagged the original wording instead of rewriting it
    text = "If the cache is down, we use the database instead."
    out = render(store, syn_account, text, surface="x",
                 complete=lambda p, s, m: "If the cache is down, we "
                                          "{{fall-back-to|use the database instead}}.")
    assert out["rendered"] == text
    assert out["used"] == [] and store.exposure_counts(syn_account) == {}
    # a marker that does hold the phrase (in any form, with extra words) still links
    ok = render(store, syn_account, text, surface="x",
                complete=lambda p, s, m: "If the cache is down, we "
                                         "{{fall-back-to|fall back to the}} database.")
    assert ok["used"] == ["fall-back-to"]
    assert "[fall back to the](https://x/l.md#11-fall-back-to)" in ok["rendered"]


def test_render_prompt_says_to_rewrite_not_tag(store, syn_account):
    seen = {}

    def fake(prompt, system, max_tokens):
        seen["prompt"] = prompt
        return "Some answer text."

    render(store, syn_account, "Some answer text.", surface="x", complete=fake)
    assert "Never put the marker around your own or the original words" in seen["prompt"]


def test_render_falls_back_to_local(store, syn_account):
    def broken(prompt, system, max_tokens):
        raise RuntimeError("no key")

    out = render(store, syn_account, "Some answer text.", surface="x", complete=broken)
    assert out["fallback"] == "local" and out["rendered"] == "Some answer text."
    assert "no key" in out["reason"]
    short = render(store, syn_account, "A much longer answer than the reply.", surface="x",
                   complete=lambda p, s, m: "ok")
    assert short["fallback"] == "local"
    assert store.exposure_counts(syn_account) == {}


def test_mcp_commands(store, syn_account):
    pytest.importorskip("mcp")
    from lesmots.lexrender.server import build_server

    server = build_server(store, account_id=syn_account,
                          complete=lambda p, s, m: "A {{sophisticated|sophisticated}} plan.")
    prompts = {p.name for p in asyncio.run(server.list_prompts())}
    assert prompts == {"add", "load", "local", "remote"}

    def prompt_text(name, args=None):
        result = asyncio.run(server.get_prompt(name, args or {}))
        return result.messages[0].content.text

    assert "paid plan" in prompt_text("remote")  # free plan: stays local
    assert store.render_mode(syn_account) == "local"
    free = _call(server, "render", {"text": "A refined plan.", "surface": "test"})
    assert free["fallback"] == "local" and "paid plan" in free["reason"]
    store.set_plan(syn_account, "paid")
    assert "remote render mode" in prompt_text("remote")
    assert store.render_mode(syn_account) == "remote"
    loaded = prompt_text("load")
    assert "render mode: remote" in loaded
    assert "sophisticated | 14-sophisticated | synonyms: refined, elaborate, polished" in loaded
    assert '"leverage"' in prompt_text("add", {"phrase": "leverage"})
    assert "local render mode" in prompt_text("local")
    assert store.render_mode(syn_account) == "local"

    out = _call(server, "render", {"text": "A refined plan.", "surface": "test"})
    assert out["used"] == ["sophisticated"] and out["via"] == {"sophisticated": "refined"}
    syn = _call(server, "set_synonyms", {"id_or_phrase": "sophisticated", "synonyms": ["fancy"]})
    assert syn["synonyms"] == ["fancy"]


def test_cli_signs_in_with_the_app_session(tmp_path, monkeypatch, capsys):
    from lesmots import web
    from lesmots.lexrender.cli import main

    monkeypatch.setenv("LESMOTS_SESSION_SECRET", "test-secret")
    monkeypatch.delenv("LEXRENDER_SESSION", raising=False)
    db = tmp_path / "l.db"
    token = web.make_session("g-marine", "")
    sample = tmp_path / "lexicon.md"
    sample.write_text(SAMPLE, encoding="utf-8")
    main(["--db", str(db), "--session", token, "import", str(sample)])
    capsys.readouterr()
    monkeypatch.setenv("LEXRENDER_SESSION", token)
    main(["--db", str(db), "whoami"])
    out = capsys.readouterr().out
    assert '"user_id": "g-marine"' in out and '"entries": 3' in out
    with pytest.raises(SystemExit, match="invalid or expired"):
        main(["--db", str(db), "--session", "g-marine", "stats"])
    monkeypatch.delenv("LEXRENDER_SESSION")
    with pytest.raises(SystemExit, match="sign in"):
        main(["--db", str(db), "stats"])


# ---------- plans and dashboard ----------

def test_plans(store, account):
    assert store.plan(account) == "free" and not store.is_paid(account)
    store.set_plan(account, "paid")
    assert store.is_paid(account)
    with pytest.raises(LexiconError):
        store.set_plan(account, "gold")


def _seed_exposures(store, account):
    store.log_exposures(account, "e1", ["fall-back-to", "well-worn"], "Claude Code",
                        occurred_at="2026-11-10T09:00:00Z")
    store.log_exposures(account, "e2", ["fall-back-to"], "Notion", label="spec",
                        occurred_at="2026-11-12T18:30:00Z", via={"fall-back-to": "revert to"})


def test_analytics(store, account):
    from datetime import datetime, timezone

    from lesmots.lexrender import analytics

    _seed_exposures(store, account)
    s = analytics.summary(store, account)
    assert (s["entries"], s["exposures_total"], s["entries_exposed"]) == (3, 3, 2)
    rows = analytics.entries(store, account)
    assert rows[0]["id"] == "fall-back-to" and rows[0]["exposures"] == 2
    assert rows[0]["via_synonyms"] == 1 and rows[0]["last_seen"].startswith("2026-11-12")
    assert rows[-1]["exposures"] == 0 and rows[-1]["last_seen"] is None
    assert analytics.entries(store, account, sort="recent")[0]["id"] == "fall-back-to"
    with pytest.raises(LexiconError):
        analytics.entries(store, account, sort="nope")
    days = analytics.timeline(store, account, days=7,
                              today=datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert len(days) == 7 and days[-1]["date"] == "2026-11-13"
    assert {d["date"]: d["count"] for d in days}["2026-11-10"] == 2
    assert analytics.surfaces(store, account) == [{"surface": "Claude Code", "count": 2},
                                                  {"surface": "Notion", "count": 1}]
    detail = analytics.entry_detail(store, account, "fall-back-to")
    assert detail["exposures"][0] == {"at": "2026-11-12T18:30:00+00:00", "surface": "Notion",
                                      "label": "spec", "mode": "local_rewrite",
                                      "via": "revert to"}
    assert analytics.entry_detail(store, account, "missing") is None


def test_dashboard_routes(store):
    pytest.importorskip("mcp")
    from starlette.testclient import TestClient

    from lesmots.lexrender.server import build_server

    server = build_server(store, public_url="https://lexicon.example.com",
                          verify={"tok-paid": "g-paid", "tok-free": "g-free"}.get)
    paid = store.account_for_app_user("g-paid")
    store.import_markdown(paid, SAMPLE)
    store.set_plan(paid, "paid")
    _seed_exposures(store, paid)
    client = TestClient(server.streamable_http_app())

    def get(path, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return client.get(path, headers=headers)

    assert get("/api/dashboard/summary").status_code == 401
    assert get("/api/dashboard/summary", "forged").status_code == 401
    assert get("/api/dashboard/summary", "tok-free").status_code == 402
    assert get("/api/dashboard/summary", "tok-paid").json()["exposures_total"] == 3
    assert get("/api/dashboard/entries?sort=recent", "tok-paid").json()[0]["id"] == "fall-back-to"
    assert get("/api/dashboard/entries?sort=bad", "tok-paid").status_code == 400
    assert len(get("/api/dashboard/timeline?days=5", "tok-paid").json()) == 5
    assert get("/api/dashboard/surfaces", "tok-paid").json()[0]["surface"] == "Claude Code"
    assert get("/api/dashboard/entry/well-worn", "tok-paid").json()["entry"]["id"] == "well-worn"
    assert get("/api/dashboard/entry/nope", "tok-paid").status_code == 404
    assert get("/dashboard").status_code == 200
    assert get("/dashboard/demo/summary.json").json()["entries"] > 0
    assert get("/dashboard/demo/..%2Fdashboard.html").status_code == 404
    assert get("/dashboard/demo/nope.json").status_code == 404


# ---------- Claude plugin ----------

def test_claude_plugin_matches_the_server():
    import json
    import re
    from pathlib import Path

    pytest.importorskip("mcp")
    from lesmots.lexrender.server import build_server

    root = Path(__file__).parent.parent
    plugin = root / "plugins" / "lexicon-render"
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert market["plugins"][0]["name"] == manifest["name"] == "lexicon-render"
    assert (root / market["plugins"][0]["source"]).resolve() == plugin.resolve()
    mcp = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["lexicon"]
    assert mcp["command"] == "lexrender" and mcp["args"] == ["serve"]

    store = Store(":memory:")
    server = build_server(store, account_id=store.create_account("p"))
    tools = {t.name for t in asyncio.run(server.list_tools())}
    commands = {p.stem: p.read_text() for p in (plugin / "commands").glob("*.md")}
    assert set(commands) == {"add", "load", "local", "remote"}
    for name, text in commands.items():
        assert text.startswith("---\ndescription: "), name
        for tool in re.findall(r"`([a-z_]+)` tool|call `([a-z_]+)`", text):
            used = tool[0] or tool[1]
            assert used in tools, (name, used)


def test_labelled_synonym_lines_from_the_lexicon_file():
    md = SAMPLE.replace(
        "### 1.3 `I wouldn't put it past someone`",
        "### 1.4 `leverage`\n* **Definition**: Use to advantage.\n"
        "* **Common synonyms (Synonyms)**: `utilize`, `exploit`, `harness`\n"
        "* **Example**: She leveraged her skills.\n\n"
        "### 1.3 `I wouldn't put it past someone`")
    entry = next(e for e in parse_lexicon(md) if e.phrase == "leverage")
    assert entry.synonyms == ["utilize", "exploit", "harness"]

    store = Store(":memory:")
    acct = store.create_account("v13")
    store.import_markdown(acct, md)
    assert store.matcher(acct).find("We harness the cache.")[0].entry_id == "leverage"
    updated = store.set_synonyms(acct, "leverage", ["utilize", "tap"])
    assert "* **Common synonyms (Synonyms)**: utilize, tap\n* **Example**" in updated.body
    assert store.set_synonyms(acct, "leverage", []).synonyms == []


def test_hosted_under_a_path_prefix(store):
    pytest.importorskip("mcp")
    from starlette.testclient import TestClient

    from lesmots.lexrender.hosting import http_app, prefix_of
    from lesmots.lexrender.server import build_server

    public = "https://marine-api.tunnel.tungo.cn/lesmots"
    assert prefix_of(public) == "/lesmots" and prefix_of("https://x.example") == ""
    server = build_server(store, public_url=public, verify={"tok": "g-1"}.get)
    app = http_app(server, public)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}}
    accept = {"Accept": "application/json, text/event-stream"}
    signed_in = {**accept, "Authorization": "Bearer tok"}
    with TestClient(app, base_url="https://marine-api.tunnel.tungo.cn") as client:
        # the gateway may forward the prefix or strip it
        assert client.get("/lesmots/healthz").json() == {"ok": True,
                                                         "service": "lexicon-render"}
        assert client.get("/healthz").status_code == 200
        assert client.get("/lesmots/dashboard").status_code == 200
        assert client.get("/lesmots/dashboard/demo/summary.json").status_code == 200
        assert client.get("/lesmots/api/dashboard/summary").status_code == 401
        assert client.get("/lesmots/api/dashboard/summary",
                          headers={"Authorization": "Bearer tok"}).status_code == 402
        assert client.post("/lesmots/mcp", json=init, headers=accept).status_code == 401
        ok = client.post("/lesmots/mcp", json=init, headers=signed_in)
        assert ok.status_code == 200, ok.text
        evil = client.post("/lesmots/mcp", json=init,
                           headers={**signed_in, "Host": "evil.example"})
        assert evil.status_code in (400, 421)
