"""The naturalness guardrail: a critic marks forced phrases, the judge and distillation
act on its verdicts, and the alarm flags overused entries."""

import dataclasses
import json
import re

import pytest

from lesmots.lexrender import distill, naturalness
from lesmots.lexrender.evaluate import Lexicon, judge
from lesmots.lexrender.parse import parse_lexicon

TEXT = "If the cache is unavailable, the service uses the database instead."
ONE = "If the cache is unavailable, the service will {{fall-back-to|fall back to}} the database."
TWO = ("If the cache is unavailable, the service will {{fall-back-to|fall back to}} the "
       "database and {{rely-on|rely on}} it.")


@pytest.fixture(scope="module")
def lex():
    return Lexicon.load()


def critic_forcing(*forced_ids, restore=None):
    """A fake critic that marks ``forced_ids`` forced and returns ``restore(id)`` as the
    restored rewrite. It reads the items and phrases back from the prompt it gets."""
    calls = []

    def critic(prompt, system):
        assert system == naturalness.CRITIC_SYSTEM
        calls.append(prompt)
        out, item = {}, None
        for line in prompt.splitlines():
            if line.startswith("=== ITEM "):
                item = line.split()[2]
                out[item] = {"verdicts": []}
            m = re.match(r"\d+\. \{\{([^|]+)\|", line)
            if item and m:
                out[item]["verdicts"].append("forced" if m.group(1) in forced_ids else "ok")
        for item_id, verdict in out.items():
            if restore and "forced" in verdict["verdicts"]:
                verdict["rewrite"] = restore(item_id)
        return "Here you go:\n" + json.dumps(out)
    critic.calls = calls
    return critic


def test_parse_reads_an_optional_not_for_note():
    [entry, other] = parse_lexicon(
        "## 1. Idioms\n\n### 1.1 `get started with`\n* **Definition**: Begin using.\n"
        '* **Not for**: the first step in a sequence (use "start with")\n\n'
        "### 1.2 `fall back to`\n* **Definition**: Revert.\n")
    assert entry.not_for == 'the first step in a sequence (use "start with")'
    assert entry.definition == "Begin using."
    assert other.not_for == ""


def test_judge_without_a_critic_still_prefers_more_phrases(lex):
    assert judge(lex, TEXT, TWO) > judge(lex, TEXT, ONE)


def test_judge_guardrail_breaks_ties_after_phrase_count(lex):
    critic = critic_forcing("rely-on")
    fine = critic_forcing()
    # more phrases still win, even when one of them is forced (exposure first)
    assert judge(lex, TEXT, TWO, critic) > judge(lex, TEXT, ONE, critic)
    # with equally many phrases, the reply with fewer forced phrases wins
    assert judge(lex, TEXT, TWO, fine) > judge(lex, TEXT, TWO, critic)
    # the critic is only asked about replies that pass the structural checks
    calls = len(critic.calls)
    judge(lex, TEXT, TEXT, critic)
    assert len(critic.calls) == calls


def test_critic_prompt_shows_meaning_and_not_for_note(lex):
    entry = dataclasses.replace(lex.by_id["rely-on"], not_for="trusting a person")
    lex2 = dataclasses.replace(lex, by_id={**lex.by_id, "rely-on": entry})
    prompt = naturalness.critic_prompt(lex2, [("1", TEXT, TWO)])
    assert "1. {{fall-back-to|fall back to}}" in prompt
    assert "2. {{rely-on|rely on}}" in prompt and "Not for: trusting a person" in prompt
    assert "ORIGINAL:\n" + TEXT in prompt


def test_a_failed_or_malformed_critic_never_removes_a_phrase(lex, capsys):
    def broken(prompt, system):
        raise TimeoutError("slow")
    assert naturalness.forced_count(broken, lex, TEXT, TWO) == 0
    assert "critic failed" in capsys.readouterr().err
    short = lambda p, s: json.dumps({"1": {"verdicts": ["forced"]}})
    assert naturalness.forced_count(short, lex, TEXT, TWO) == 0


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_natural_pass_restores_forced_phrases_before_export(lex, tmp_path):
    sources = [{"id": "a", "batch": 0, "target": "fall-back-to", "text": TEXT},
               {"id": "b", "batch": 0, "target": None,
                "text": "We use the database. The cache is optional."}]
    write_rows(tmp_path / "sources.jsonl", sources)
    write_rows(tmp_path / "rewrites.jsonl", [
        {"id": "a", "rewrite": TWO, "candidates": ["fall-back-to", "rely-on"]},
        {"id": "b", "rewrite": "We {{rely-on|rely on}} the database. The cache is optional.",
         "candidates": ["rely-on"]},
    ])
    restored = {"a": ONE, "b": "We use the database. The cache is optional."}
    critic = critic_forcing("rely-on", restore=restored.get)
    assert distill.natural(lex, critic, tmp_path) == 2
    assert distill.natural(lex, critic, tmp_path) == 0  # resumable
    rows = {r["id"]: r for r in distill._read(tmp_path / "natural.jsonl")}
    assert rows["a"]["forced"] == ["rely-on"] and rows["a"]["rewrite"] == ONE

    counts = distill.export(lex, tmp_path)
    assert counts["train"] + counts["valid"] == 2 and counts["rejected"] == 0
    replies = [ex["messages"][2]["content"]
               for ex in distill._read(tmp_path / "train.jsonl")
               + distill._read(tmp_path / "valid.jsonl")]
    assert ONE in replies  # the forced phrase is gone, the natural one stays
    assert "We use the database. The cache is optional." in replies  # no phrase is fine


def test_export_rejects_a_forced_phrase_the_critic_did_not_undo(lex, tmp_path):
    write_rows(tmp_path / "sources.jsonl",
               [{"id": "a", "batch": 0, "target": "fall-back-to", "text": TEXT}])
    write_rows(tmp_path / "rewrites.jsonl",
               [{"id": "a", "rewrite": TWO, "candidates": ["fall-back-to", "rely-on"]}])
    write_rows(tmp_path / "natural.jsonl", [{"id": "a", "forced": ["rely-on"], "rewrite": TWO}])
    assert distill.export(lex, tmp_path)["rejected"] == 1
    assert distill._read(tmp_path / "rejected.jsonl")[0]["why"] == "forced phrase still there"
    write_rows(tmp_path / "natural.jsonl", [{"id": "a", "forced": ["rely-on"], "rewrite": None}])
    distill.export(lex, tmp_path)
    assert distill._read(tmp_path / "rejected.jsonl")[0]["why"] == "forced phrase, not restored"


def test_rewrite_prompt_shows_the_not_for_note_to_the_teacher_only(lex):
    entry = dataclasses.replace(lex.by_id["fall-back-to"], not_for="a planned migration")
    source = {"id": "s1", "text": TEXT}
    teacher = distill.rewrite_prompt(lex, [(source, [entry])])
    assert f"fall-back-to | {entry.phrase} (not for: a planned migration)" in teacher
    assert "fluent writer" in teacher
    assert "not for" not in distill.student_prompt(lex, TEXT, [entry]).lower()


def test_contrast_seed_offers_the_entry_and_rejects_its_use(lex, tmp_path, monkeypatch):
    entry = dataclasses.replace(lex.by_id["fall-back-to"], not_for="a planned migration")
    seed_reply = [
        # (TEXT itself is a held-out test sentence, so the guard would drop it)
        {"target": "fall-back-to", "level": "plain",
         "text": "When the main printer jams, we print on the old one in the hall."},
        {"target": "fall-back-to", "level": "contrast",
         "text": "Next quarter we move every service to the new database."},
    ]
    monkeypatch.setattr(distill.random.Random, "sample", lambda self, seq, n: (
        [entry] * n if seq and hasattr(seq[0], "phrase") else list(seq)[:n]))
    prompt_seen = []

    def teacher(prompt, system):
        prompt_seen.append(prompt)
        return json.dumps(seed_reply)
    assert distill.seed(lex, teacher, tmp_path, 1, distill.TestGuard(), angles=True) == 2
    assert '"contrast"' in prompt_seen[0] and "Not for: a planned migration" in prompt_seen[0]
    contrast = distill._read(tmp_path / "sources.jsonl")[1]
    assert contrast["target"] is None and contrast["contrast"] == "fall-back-to"
    assert contrast["level"] == distill.CONTRAST

    from lesmots.lexrender.candidates import CandidateIndex
    index = CandidateIndex(lex.entries, lex.matcher)
    assert "fall-back-to" in [e.id for e in distill.candidates_for(index, lex, contrast, 20)]
    used = {"rewrite": "Next quarter we {{fall-back-to|fall back to}} the new database.",
            "candidates": ["fall-back-to"]}
    assert distill.check(lex, contrast, used) == (False, "used the contrast entry fall-back-to")
    left = {"rewrite": contrast["text"], "candidates": ["fall-back-to"]}
    assert distill.check(lex, contrast, left) == (True, "")


def test_alarm_flags_an_overused_entry(lex, tmp_path, monkeypatch):
    monkeypatch.setattr(distill, "ALARM_MIN_OFFERED", 2)
    rewrites, examples = [], []
    for i in range(20):
        heavy = i < 10  # fall-back-to used in half the examples, rely-on once
        reply = ONE if heavy else ("We {{rely-on|rely on}} the database." if i == 10
                                   else "We use the database.")
        rewrites.append({"id": f"r{i}", "rewrite": reply,
                         "candidates": ["fall-back-to", "rely-on"]})
        examples.append({"messages": [{"role": "assistant", "content": reply}]})
    write_rows(tmp_path / "rewrites.jsonl", rewrites)
    write_rows(tmp_path / "train.jsonl", examples)
    report = distill.alarm(lex, tmp_path)
    assert report["examples"] == 20
    flagged = {f["id"]: f for f in report["flagged"]}
    assert "fall-back-to" in flagged and flagged["fall-back-to"]["used"] == 10
    assert "in 50.0% of examples" in flagged["fall-back-to"]["why"]
