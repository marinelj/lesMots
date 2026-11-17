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


def test_test_set_is_frozen(lex, tmp_path):
    """Rebuilding never moves a case into or out of the test set, or edits one."""
    frozen = [c for c in evaluate.load_cases(evaluate.FROZEN_TEST)]
    fresh = evaluate.build(lex, out=tmp_path / "cases.jsonl")
    assert [c for c in fresh if c["split"] == "test"] == sorted(frozen, key=lambda c: c["id"])
    assert len(frozen) == 95
    ids = [c["id"] for c in fresh]
    assert len(ids) == len(set(ids))


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
    case = cases["none-13"]
    assert case["text"] == "Yes."
    assert evaluate.score(lex, case, case["text"])["passed"]
    assert not evaluate.score(lex, case, "{{go-for-it|Go for it}}.")["checks"]["expected"]


def test_open_cases_pass_with_or_without_phrases(lex, cases):
    """The match policy is aggressive: any correct phrase in an open case is welcome."""
    case = cases["none-01"]
    assert case["kind"] == "open"
    assert evaluate.score(lex, case, case["text"])["passed"]
    # (the scorer checks form, not meaning: a human reads failures for that)
    used = evaluate.score(lex, case, "The file {{rests-on|rests on}} 42 lines.")
    assert used["passed"] and used["used"] == ["rests-on"]


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
    assert summary["by_kind"] == {"open": "1/1", "paraphrase": "0/1"}
    assert summary["failed_checks"] == {"expected": 1}
    assert summary["phrases_per_case"] == 0.0
    good = evaluate.score(lex, cases["para-01"], "If the cache is unavailable, the service will "
                          "{{fall-back-to|fall back to}} the database.")
    assert evaluate.summarize([good])["phrases_per_case"] == 1.0


def test_prompt_is_the_production_prompt(lex, cases):
    prompt = lex.prompt(cases["para-01"]["text"])
    assert "LEXICON (id | phrase):" in prompt
    assert "fall-back-to | Fall back to" in prompt
    assert prompt.endswith(cases["para-01"]["text"])


def test_summary_reports_a_95_percent_interval():
    assert evaluate._wilson(76, 95) == [0.709, 0.868]
    assert evaluate._wilson(0, 0) == [0.0, 0.0]


def test_shortlist_recall_floor(lex):
    """Without embeddings, a 40-entry shortlist holds an expected entry for at least 85%
    of test cases (91% on 2026-11-17). Paraphrase cases are the misses: word overlap
    can't see them, so the Studio measures recall with embeddings."""
    from lesmots.lexrender.candidates import CandidateIndex

    index = CandidateIndex(lex.entries, lex.matcher)
    result = evaluate.shortlist_recall(index, evaluate.load_cases(split="test"), [40])
    assert result["k=40"]["recall"] >= 0.85, result


def test_challenge_cases_are_well_formed(lex):
    challenge = evaluate.load_cases(evaluate.EVAL_DIR / "challenge.jsonl")
    assert len({c["id"] for c in challenge}) == len(challenge) >= 20
    for c in challenge:
        assert c["split"] == "challenge"
        assert set(c.get("expect_any", [])) <= set(lex.by_id), c["id"]
        # returning the answer untouched never breaks code, URLs or the text checks
        checks = evaluate.score(lex, c, c["text"])["checks"]
        assert checks["protected"] and checks["unchanged"] and checks["markers"], c["id"]
