"""Distillation pipeline with a fake teacher: seed -> rewrite -> check -> export."""

import json

import pytest

from lesmots.lexrender import distill
from lesmots.lexrender.candidates import CandidateIndex
from lesmots.lexrender.evaluate import Lexicon


@pytest.fixture(scope="module")
def lex():
    return Lexicon.load()


@pytest.fixture(scope="module")
def index(lex):
    return CandidateIndex(lex.entries, lex.matcher)


def fake_teacher(seed_reply, rewrite_fn):
    """Answers seed prompts with ``seed_reply`` and rewrite prompts via ``rewrite_fn``."""
    def teacher(prompt, system):
        if system == distill.SEED_SYSTEM:
            return "```json\n" + json.dumps(seed_reply) + "\n```"
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("=== ANSWER")]
        return json.dumps({i: rewrite_fn(i) for i in ids})
    return teacher


def test_candidates_keep_the_target_and_matcher_hits(lex, index):
    source = {"text": "If the cache is down, we use the database instead.",
              "target": "fall-back-to"}
    cands = distill.candidates_for(index, lex, source, 20)
    assert len(cands) == 20
    assert "fall-back-to" in [e.id for e in cands]
    hits = [e.id for e in index.select("We fell back to the cache.", 20)]
    assert hits[0] == "fall-back-to"


def test_pipeline_keeps_good_rewrites_and_rejects_bad_ones(lex, index, tmp_path):
    seed_reply = [
        {"target": "fall-back-to", "text": "If the cache is down, we use the database instead."},
        {"target": "fall-back-to", "text": "We fall back to the database."},  # uses phrase
        {"target": None, "text": "Run `make test` before pushing. Thanks."},
        {"target": "not-offered", "text": "Some other text."},  # not a target it was given
    ]
    # seed_prompt picks its targets at random; make every batch target fall-back-to
    pool = [lex.by_id["fall-back-to"]] * 50
    orig = distill.random.Random.sample
    distill.random.Random.sample = lambda self, seq, n: (pool[:n] if seq and hasattr(
        seq[0], "phrase") else orig(self, seq, n))
    try:
        kept = distill.seed(lex, fake_teacher(seed_reply, None), tmp_path, 1,
                            distill.TestGuard())
    finally:
        distill.random.Random.sample = orig
    assert kept == 2
    sources = distill._read(tmp_path / "sources.jsonl")
    assert [s["target"] for s in sources] == ["fall-back-to", None]

    rewrites = {
        sources[0]["id"]: "If the cache is down, we {{fall-back-to|fall back to}} the database.",
        sources[1]["id"]: "Run `make tests` before pushing. Thanks.",  # code changed
    }
    teacher = fake_teacher(None, lambda i: rewrites[i])
    assert distill.rewrite(lex, index, teacher, tmp_path) == 2
    assert distill.rewrite(lex, index, teacher, tmp_path) == 0  # resumable: nothing left

    counts = distill.export(lex, tmp_path)
    assert counts["train"] + counts["valid"] == 1
    assert counts["rejected"] == 1
    rejected = distill._read(tmp_path / "rejected.jsonl")
    assert rejected[0]["why"] == "protected text changed"
    example = (distill._read(tmp_path / "train.jsonl") or
               distill._read(tmp_path / "valid.jsonl"))[0]
    roles = [m["role"] for m in example["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert "fall-back-to | Fall back to" in example["messages"][1]["content"]
    assert example["messages"][1]["content"].endswith(sources[0]["text"])


@pytest.mark.parametrize(("reply", "why"), [
    ("", "empty"),
    ("If the cache is down, we {{degrade-gracefully|fall back to}} the database.",
     "bad marker degrade-gracefully|fall back to"),
    ("If the cache is down, we use the database instead.", "no phrase used"),
    ("The cache is down. We {{fall-back-to|fall back to}} the database.",
     "unmarked sentence changed"),
])
def test_check_rejects(lex, reply, why):
    source = {"text": "If the cache is down, we use the database instead.",
              "target": "fall-back-to"}
    row = {"rewrite": reply, "candidates": ["fall-back-to", "degrade-gracefully"]}
    assert distill.check(lex, source, row) == (False, why)


def test_teacher_failures_stop_with_retry_later(lex, tmp_path):
    def broken(prompt, system):
        raise RuntimeError("usage limit")
    with pytest.raises(SystemExit) as stop:
        distill.seed(lex, broken, tmp_path, 1, distill.TestGuard())
    assert stop.value.code == distill.EXIT_RETRY_LATER


def test_guard_drops_test_case_copies():
    guard = distill.TestGuard()
    test_text = next(c["text"] for c in distill.load_cases() if c["split"] == "test")
    assert guard.too_close(test_text)
    assert not guard.too_close("A completely unrelated sentence about penguins and tea.")


def test_embedder_brings_paraphrase_targets_into_the_list(lex):
    """Word overlap misses "uses the database instead" -> "fall back to"; meaning doesn't."""
    def fake_embed(texts):
        return [[1.0, 0.0] if ("instead" in t or t.startswith("Fall back to")) else [0.0, 1.0]
                for t in texts]
    text = "When the cache is unavailable, the service uses the database instead."
    plain = CandidateIndex(lex.entries, lex.matcher)
    assert "fall-back-to" not in [e.id for e in plain.select(text, 20)]
    embedded = CandidateIndex(lex.entries, lex.matcher, fake_embed)
    assert "fall-back-to" in [e.id for e in embedded.select(text, 20)]


def test_grok_teacher_polls_and_strips_control_characters():
    from lesmots.lexrender.distill import grok_complete

    calls = []
    replies = iter([{"run_id": "run_1", "status": "queued"},
                    {"status": "running"},
                    {"status": "completed", "final_output": "ok\x07 text\x00"}])

    def http(method, url, body):
        calls.append((method, url, body))
        return next(replies)

    complete = grok_complete("http://gw/grokbot/", poll=0, http=http,
                             sleep=lambda s: None, clock=lambda: 0.0)
    assert complete("TASK", "SYS") == "ok text"
    assert calls[0] == ("POST", "http://gw/grokbot/v1/runs", {"prompt": "SYS\n\nTASK"})
    assert calls[1][1] == "http://gw/grokbot/v1/runs/run_1"


def test_grok_teacher_raises_on_timeout_status():
    import pytest

    from lesmots.lexrender.distill import grok_complete

    replies = iter([{"run_id": "r", "status": "running"}, {"status": "timed_out"},
                    {"run_id": "r2", "status": "running"}, {"status": "failed"}])
    complete = grok_complete("http://gw", poll=0, http=lambda *a: next(replies),
                             sleep=lambda s: None, clock=lambda: 0.0)
    with pytest.raises(RuntimeError):
        complete("t", "s")


def test_grok_teacher_retries_once():
    from lesmots.lexrender.distill import grok_complete

    replies = iter([{"run_id": "r", "status": "failed"},
                    {"run_id": "r2", "status": "completed", "final_output": "fine"}])
    complete = grok_complete("http://gw", poll=0, http=lambda *a: next(replies),
                             sleep=lambda s: None, clock=lambda: 0.0)
    assert complete("t", "s") == "fine"


def test_batches_stay_under_the_byte_limit():
    from lesmots.lexrender import distill
    from lesmots.lexrender.evaluate import Lexicon

    lex = Lexicon.load()
    cands = lex.entries[:40]
    items = [({"id": f"a{i}", "text": "The service uses the database instead. " * 5}, cands)
             for i in range(10)]
    whole = len(distill.rewrite_prompt(lex, items).encode())
    batches = distill._batches(lex, items, 10, whole // 3)
    assert len(batches) > 1 and sum(len(b) for b in batches) == 10
    assert all(len(distill.rewrite_prompt(lex, b).encode()) <= whole // 3 for b in batches)
    assert distill._batches(lex, items, 10, 0) == [items]
