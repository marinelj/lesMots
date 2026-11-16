"""The render-model scorer: good replies pass, each kind of mistake fails its check."""

import pytest

from lesmots.lexrender import evaluate


@pytest.fixture(scope="module")
def lex():
    return evaluate.Lexicon.load()


@pytest.fixture(scope="module")
def cases():
    return {c["id"]: c for c in evaluate.load_cases()}


def test_cases_file_matches_a_fresh_build(lex, tmp_path):
    fresh = evaluate.build(lex, out=tmp_path / "cases.jsonl")
    assert (tmp_path / "cases.jsonl").read_text() == evaluate.CASES.read_text(), (
        "eval/render/cases.jsonl is stale: run python -m lesmots.lexrender.evaluate build")
    assert {c["split"] for c in fresh} == {"dev", "test"}


def test_a_good_rewrite_passes(lex, cases):
    reply = ("If the cache is unavailable, the service will {{fall-back-to|fall back to}} "
             "the database.")
    result = evaluate.score(lex, cases["para-01"], reply)
    assert result["passed"], result
    assert result["used"] == ["fall-back-to"]


def test_echoing_the_answer_misses_the_expected_phrase(lex, cases):
    result = evaluate.score(lex, cases["para-01"], cases["para-01"]["text"])
    assert result["checks"] == {"markers": True, "protected": True, "unchanged": True,
                                "expected": False}


def test_a_marker_around_the_wrong_words_fails(lex, cases):
    reply = "If the cache is unavailable, the service {{fall-back-to|uses the database}} instead."
    result = evaluate.score(lex, cases["para-01"], reply)
    assert not result["checks"]["markers"]
    assert result["bad_markers"] == ["fall-back-to|uses the database"]


def test_forcing_a_phrase_into_a_none_case_fails(lex, cases):
    case = cases["none-01"]
    assert evaluate.score(lex, case, case["text"])["passed"]
    forced = "The file has 42 lines, so we {{fall-back-to|fall back to}} reading it."
    assert not evaluate.score(lex, case, forced)["checks"]["expected"]


def test_changing_code_fails(lex, cases):
    case = cases["code-02"]
    assert evaluate.score(lex, case, case["text"])["passed"]
    edited = case["text"].replace("tidy_up --all", "tidy_up")
    assert not evaluate.score(lex, case, edited)["checks"]["protected"]


def test_rewording_a_sentence_without_a_phrase_fails(lex, cases):
    case = cases["code-14"]
    reply = case["text"].replace("That was the rule we agreed on.", "We agreed on that rule.")
    result = evaluate.score(lex, case, reply)
    assert not result["checks"]["unchanged"]
    assert result["stray"] == ["We agreed on that rule."]


def test_summary_counts_by_kind(lex, cases):
    results = [evaluate.score(lex, cases[i], cases[i]["text"]) for i in ("para-01", "none-01")]
    summary = evaluate.summarize(results)
    assert summary["by_kind"] == {"none": "1/1", "paraphrase": "0/1"}
    assert summary["failed_checks"] == {"expected": 1}


def test_prompt_is_the_production_prompt(lex, cases):
    prompt = lex.prompt(cases["para-01"]["text"])
    assert "LEXICON (id | phrase):" in prompt
    assert "fall-back-to | Fall back to" in prompt
    assert prompt.endswith(cases["para-01"]["text"])
