"""Offline tests: SM-2, memory, daily job, CLI (fetch/LLM injected/mocked)."""

from datetime import date, timedelta

import pytest

from lesmots import daily
from lesmots.memory import Memory
from lesmots.models import Word, detect_kind
from lesmots.srs import review, pick_words, AGAIN, GOOD, EASY, MIN_EASE

TODAY = date.today()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LESMOTS_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LESMOTS_API_KEY", raising=False)
    monkeypatch.delenv("LESMOTS_API_BASE", raising=False)
    monkeypatch.delenv("LESMOTS_GOOGLE_CLIENT_ID", raising=False)
    return tmp_path


def fake_fetch(interests):
    return {"title": "AI breakthrough", "url": "http://x", "points": 100,
            "source": "Test", "text": "A new model was released."}


def fake_rewrite(title, text, words, max_words):
    return f"News about {title}: " + " ".join(f"**{w}**" for w in words[:3])


# ---------- models ----------

def test_detect_kind():
    assert detect_kind("inference") == "word"
    assert detect_kind("state of the art") == "phrase"
    assert detect_kind("The model beats the benchmark.") == "sentence"


def test_familiarity_levels():
    w = Word(text="x")
    assert w.familiarity == 1  # brand new = completely unfamiliar
    w.interval = 1
    assert w.familiarity == 2
    w.interval = 7
    assert w.familiarity == 3
    w.interval = 60
    assert w.familiarity == 5


def test_set_familiarity_snaps_interval_and_due():
    w = Word(text="x")
    w.set_familiarity(4, on=TODAY)
    assert w.familiarity == 4
    assert w.interval == 21
    assert w.due == (TODAY + timedelta(days=21)).isoformat()
    with pytest.raises(ValueError):
        w.set_familiarity(6)


# ---------- SM-2 ----------

def test_good_review_grows_interval():
    w = Word(text="x", interval=10, ease=2.5)
    review(w, GOOD, on=TODAY)
    assert w.interval == 25
    assert w.due == (TODAY + timedelta(days=25)).isoformat()


def test_again_resets():
    w = Word(text="x", interval=30)
    review(w, AGAIN, on=TODAY)
    assert w.interval == 0 and w.lapses == 1 and w.is_due(TODAY)


def test_ease_floor():
    w = Word(text="x", ease=MIN_EASE)
    review(w, AGAIN, on=TODAY)
    assert w.ease == MIN_EASE


def test_easy_boosts():
    w = review(Word(text="x"), EASY, on=TODAY)
    assert w.interval >= 3 and w.ease > 2.5


def test_interval_capped_prevents_date_overflow():
    from lesmots.srs import MAX_INTERVAL
    w = Word(text="x")
    for _ in range(60):  # uncapped, this overflows date arithmetic at ~review 17
        review(w, GOOD, on=TODAY)
    assert w.interval == MAX_INTERVAL
    # words saved with a runaway interval before the cap existed still recover
    w2 = Word(text="y", interval=3_000_000)
    review(w2, GOOD, on=TODAY)
    assert w2.interval == MAX_INTERVAL


def test_pick_words_due_first_then_least_familiar():
    overdue = Word(text="a", due=(TODAY - timedelta(days=2)).isoformat())
    due = Word(text="b", due=TODAY.isoformat())
    future = Word(text="c", due=(TODAY + timedelta(days=9)).isoformat(), interval=9)
    picked = pick_words([future, due, overdue], limit=3, on=TODAY)
    assert [w.text for w in picked] == ["a", "b", "c"]  # top-up includes future word


# ---------- memory ----------

def test_memory_add_dedup_and_roundtrip(tmp_path):
    m = Memory()
    m.add(Word(text="inference"))
    m.add(Word(text="Inference"))  # dedup, case-insensitive
    assert len(m.words) == 1
    path = m.save()
    loaded = Memory.load(path)
    assert loaded.words[0].text == "inference"
    assert loaded.config["language"] == "Chinese"
    assert loaded.config["interests"] == ["LLM", "AI", "state-of-the-art tech"]


def test_record_exposure_updates_count_and_familiarity():
    m = Memory()
    w = m.add(Word(text="benchmark"))
    m.record_exposure(["benchmark", "unknown"])
    assert w.exposure_count == 1
    assert w.reviews == 1
    assert w.interval >= 1  # familiarity moved


# ---------- daily job ----------

def test_generate_prepares_content():
    m = Memory()
    m.add(Word(text="model"))
    m.add(Word(text="benchmark"))
    prepared = daily.generate(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    assert prepared["original"]["title"] == "AI breakthrough"
    assert "**model**" in prepared["rewritten"]
    assert "model" in prepared["words_used"]
    assert prepared["consumed"] is False


def test_show_me_updates_exposure_and_consumes():
    m = Memory()
    w = m.add(Word(text="model"))
    daily.generate(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    prepared = daily.show_me(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    assert prepared["consumed"] is True
    assert w.exposure_count == 1
    # second show-me regenerates (content consumed) and exposes again
    daily.show_me(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    assert w.exposure_count == 2


def fake_extend(story_so_far, words, max_words):
    return "The story goes on with " + " ".join(f"**{w}**" for w in words[:2])


def test_keep_reading_extends_story_and_exposes():
    m = Memory()
    w = m.add(Word(text="model"))
    daily.show_me(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    before = w.exposure_count
    prepared = daily.keep_reading(m, extend_fn=fake_extend)
    assert "The story goes on" in prepared["rewritten"]
    assert prepared["rewritten"].startswith("News about")  # original kept
    assert w.exposure_count == before + 1
    assert "model" in prepared["words_used"]


def test_keep_reading_without_story_raises():
    with pytest.raises(ValueError):
        daily.keep_reading(Memory(), extend_fn=fake_extend)


def test_love_current_persists_and_dedupes(tmp_path):
    m = Memory()
    m.add(Word(text="model"))
    daily.show_me(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    m.love_current()
    m.love_current()  # same story twice -> one entry
    assert len(m.loved) == 1
    path = m.save()
    loaded = Memory.load(path)
    assert len(loaded.loved) == 1
    assert loaded.loved[0]["rewritten"] == m.prepared["rewritten"]
    assert loaded.loved[0]["loved_at"] == TODAY.isoformat()


def test_love_current_without_story_raises():
    with pytest.raises(ValueError):
        Memory().love_current()


def test_unlove_removes_by_id():
    m = Memory()
    m.add(Word(text="model"))
    daily.show_me(m, fetch_fn=fake_fetch, rewrite_fn=fake_rewrite)
    story = m.love_current()
    assert story["id"]
    assert m.unlove("nonexistent") is False
    assert m.unlove(story["id"]) is True
    assert m.loved == []


def test_loved_entries_without_id_get_backfilled():
    m = Memory(loved=[{"rewritten": "old story", "loved_at": "2026-07-10"}])
    assert m.loved[0]["id"]
    assert m.unlove(m.loved[0]["id"]) is True


def test_memory_remove():
    m = Memory()
    m.add(Word(text="model"))
    assert m.remove("MODEL") is True  # case-insensitive
    assert m.words == []
    assert m.remove("model") is False


def test_memory_set_familiarity():
    m = Memory()
    m.add(Word(text="benchmark"))
    w = m.set_familiarity("Benchmark", 5)  # case-insensitive find
    assert w is not None and w.familiarity == 5
    assert m.set_familiarity("unknown", 3) is None


# ---------- fetcher ----------

def test_fetch_url_freshness_filter():
    from lesmots import fetcher
    fresh = fetcher._url("ai", since_days=7)
    assert "numericFilters=" in fresh and "created_at_i" in fresh
    assert "numericFilters" not in fetcher._url("ai")
    assert "when%3A7d" in fetcher._google_news_url("Sports")


GOOGLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Norway wins big at winter games - CNN</title>
    <link>https://example.com/norway</link>
    <pubDate>Sat, 11 Jul 2026 11:30:25 GMT</pubDate>
    <source url="https://cnn.com">CNN</source>
  </item>
  <item><title></title></item>
  <item>
    <title>Plain headline without outlet suffix</title>
    <link>https://example.com/plain</link>
    <pubDate>not a date</pubDate>
  </item>
</channel></rss>"""


def test_parse_google_rss():
    from lesmots.fetcher import _parse_google_rss
    stories = _parse_google_rss(GOOGLE_RSS)
    assert len(stories) == 2  # empty-title item skipped
    assert stories[0]["title"] == "Norway wins big at winter games"  # outlet stripped
    assert stories[0]["source"] == "CNN"
    assert stories[0]["published"] == "2026-07-11"
    assert stories[0]["url"] == "https://example.com/norway"
    assert stories[1]["published"] == ""  # bad pubDate tolerated


def test_fetch_popular_falls_back_to_hn(monkeypatch):
    from lesmots import fetcher
    monkeypatch.setattr(fetcher, "_fetch_google_news",
                        lambda topic: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(fetcher, "_search",
                        lambda q, d=None: [{"title": "HN story", "points": 5,
                                            "created_at": "2026-07-10T00:00:00Z",
                                            "objectID": "1"}])
    story = fetcher.fetch_popular(["ai"])
    assert story["title"] == "HN story"
    assert story["source"] == "Hacker News"
    assert story["published"] == "2026-07-10"


# ---------- web auth (issue #18) ----------

def test_session_round_trip_tamper_and_expiry(monkeypatch):
    monkeypatch.setenv("LESMOTS_SESSION_SECRET", "test-secret")
    from lesmots import web
    token = web.make_session("12345", "a@b.c")
    data = web.check_session(token)
    assert data["uid"] == "12345" and data["email"] == "a@b.c"
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert web.check_session(tampered) is None
    assert web.check_session("garbage") is None
    import base64
    import json as jsonlib
    import time as timelib
    payload = base64.urlsafe_b64encode(jsonlib.dumps(
        {"uid": "1", "email": "", "exp": int(timelib.time()) - 10}
    ).encode()).decode().rstrip("=")
    assert web.check_session(payload + "." + web._sign(payload)) is None


def test_data_path_per_user():
    from lesmots.memory import data_path
    assert data_path().name == "lesmots.json"
    per_user = data_path("google-sub-1")
    assert per_user.parent.name == "users"
    assert per_user.name == "google-sub-1.json"


# ---------- llm helpers ----------

def test_parse_json_list_variants():
    from lesmots.llm import _parse_json_list
    assert _parse_json_list('["inference", "interference"]') == ["inference", "interference"]
    assert _parse_json_list('```json\n["a"]\n```') == ["a"]
    assert _parse_json_list('Sure! Here it is: ["b"] hope that helps') == ["b"]
    assert _parse_json_list("[]") == []
    assert _parse_json_list("not json at all") == []
    assert _parse_json_list('[1, {"x": 2}, "ok", "", "  "]') == ["ok"]
    assert _parse_json_list('["a","b","c","d","e"]') == ["a", "b", "c"]  # capped at 3


def test_fallback_rewrite_respects_max_words():
    text = daily._fallback_rewrite("T", "S. " * 300, ["a", "b"], max_words=120)
    assert len(text.split()) <= 120


# ---------- CLI ----------

def test_cli_add_and_words(capsys):
    from lesmots.cli import main
    assert main(["add", "state of the art"]) == 0
    assert main(["words"]) == 0
    out = capsys.readouterr().out
    assert "state of the art" in out
    assert "phrase" in out


def test_cli_config(capsys):
    from lesmots.cli import main
    assert main(["config", "--lang", "French", "--interests", "robotics,space"]) == 0
    out = capsys.readouterr().out
    assert "French" in out and "robotics" in out


def test_cli_show_me_offline(capsys, monkeypatch):
    from lesmots import cli
    monkeypatch.setattr("lesmots.daily.fetcher.fetch_popular", fake_fetch)
    cli.main(["add", "model"])
    assert cli.main(["show-me"]) == 0
    out = capsys.readouterr().out
    assert "Demonstration" in out and "Original" in out and "model" in out
