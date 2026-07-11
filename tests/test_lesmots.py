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
    assert w.familiarity == 0
    w.interval = 7
    assert w.familiarity == 3
    w.interval = 60
    assert w.familiarity == 5


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
