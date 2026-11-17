"""Test set and scorer for remote render models (the "dedicated render model" work).

A render model gets the production prompt (``render._prompt``) and must return the answer
with ``{{entry-id|words}}`` markers around every lexicon phrase it used. This module scores
those replies automatically, so different models (Codex, a stock Ollama model, a fine-tuned
adapter) can be compared on the same fixed cases.

Cases live in ``eval/render/cases.jsonl``: the hand-written ones from
``eval/render/handwritten.jsonl`` plus "synonym swap" cases built from the lexicon's
examples (``build``). Each case has a ``split``: ``test`` cases are held out and must never
be used as training data; ``dev`` cases are for prompt tuning.

A reply passes when every check passes:

- ``markers``: every marker names a lexicon entry and its words really are that entry's
  phrase in some word form (the same rule the renderer uses before it links).
- ``protected``: code blocks, inline code and URLs come back unchanged.
- ``unchanged``: every sentence without a marker is one of the original sentences, so the
  model only rewrote where it used a phrase, and the reply is about as long as the answer.
- ``expected``: for cases with ``expect_any``, at least one of those entries was used; for
  the few ``expect_none`` cases (texts no phrase can touch without changing the facts, such
  as "Yes."), no phrase was used at all.

The match policy is aggressive (Lexicon Render is a language-learning tool, so exposure
counts): ``open`` cases pass with any number of correct phrases, and the summary reports
``phrases_per_case``, the exposures a model produces. Between two models with similar pass
rates, the one with more phrases per case is better.

Usage::

    python -m lesmots.lexrender.evaluate build
    python -m lesmots.lexrender.evaluate run --base-url http://127.0.0.1:11434/v1 \\
        --model qwen2.5:7b --split test --out results.jsonl
    python -m lesmots.lexrender.evaluate score --outputs replies.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .forms import Matcher
from .parse import ParsedEntry, parse_lexicon
from .render import MARKER, SYSTEM, _prompt

ROOT = Path(__file__).resolve().parents[3]
LEXICON = ROOT / "lexicon" / "lexicon.md"
EVAL_DIR = ROOT / "eval" / "render"
HANDWRITTEN = EVAL_DIR / "handwritten.jsonl"
CASES = EVAL_DIR / "cases.jsonl"
FROZEN_TEST = EVAL_DIR / "test-frozen.jsonl"  # the held-out test set, never rebuilt

SYNONYM_CASES = 120  # how many synonym-swap cases ``build`` samples from the lexicon
MAX_PHRASE_WORDS = 8  # longer headings aren't in the prompt's index (render.MAX_INDEX_WORDS)

_EXAMPLE_RE = re.compile(r"\*\*Example\*\*\s*:\s*\*?\"?(.+?)\"?\*?\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?:])\s+|\n+")


# --- lexicon -------------------------------------------------------------------------

@dataclass
class Lexicon:
    entries: list[ParsedEntry]
    matcher: Matcher
    by_id: dict[str, ParsedEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = LEXICON) -> Lexicon:
        entries = parse_lexicon(path.read_text(encoding="utf-8"))
        matcher = Matcher([(e.id, e.aliases) for e in entries],
                          [(e.id, e.synonyms) for e in entries if e.synonyms])
        return cls(entries, matcher, {e.id: e for e in entries})

    def prompt(self, text: str) -> str:
        hits = [h for h in self.matcher.find(text) if h.kind == "synonym"]
        return _prompt(text, self.entries, hits)

    def is_phrase(self, entry_id: str, words: str) -> bool:
        return any(m.entry_id == entry_id and m.kind == "phrase"
                   for m in self.matcher.find(words))


# --- building cases ------------------------------------------------------------------

def synonym_cases(lex: Lexicon, limit: int = SYNONYM_CASES) -> list[dict]:
    """Take an entry's example sentence and swap its phrase for one of its synonyms.

    The model should notice the synonym fits the entry and put the phrase back.
    """
    candidates: list[dict] = []
    for e in lex.entries:
        if not e.synonyms or len(e.phrase.split()) > MAX_PHRASE_WORDS:
            continue
        m = _EXAMPLE_RE.search(e.body)
        if not m:
            continue
        example = m.group(1).strip().strip('*"“”')
        if any(ch in example for ch in '`"'):  # nested quotes or code: not a clean sentence
            continue
        # the phrase as a whole word in its base form: swapping "churns" for a synonym
        # would leave an inflection on the wrong word ("attritions out")
        found = re.search(r"(?<![\w-])" + re.escape(e.phrase.lower()) + r"(?![\w-])",
                          example.lower())
        if not found:
            continue
        at = found.start()
        synonym = next((s for s in e.synonyms
                        if not lex.is_phrase(e.id, s) and s.lower() != e.phrase.lower()
                        # a synonym that is itself an entry ("profitable" for
                        # "lucrative") would make linking that entry a right answer too
                        and lex.matcher.resolve(s) is None), None)
        if synonym is None:
            continue
        if at == 0 and example[:1].isupper():
            synonym = synonym[:1].upper() + synonym[1:]
        text = example[:at] + synonym + example[at + len(e.phrase):]
        if lex.is_phrase(e.id, text):
            continue
        candidates.append({"id": f"syn-{e.id}", "kind": "synonym", "text": text,
                           "expect_any": [e.id], "source": f"lexicon {e.number}"})
    candidates.sort(key=lambda c: hashlib.sha256(c["id"].encode()).hexdigest())
    return sorted(candidates[:limit], key=lambda c: c["id"])


def build(lex: Lexicon, handwritten: Path = HANDWRITTEN, out: Path = CASES,
          frozen: Path = FROZEN_TEST) -> list[dict]:
    """Write ``cases.jsonl``: the frozen test cases exactly as stored, then every other
    hand-written and synonym case as ``dev``.

    The test set is frozen so scores stay comparable across lexicon versions: rebuilding
    synonym cases from a new lexicon used to move cases between the splits (v17 swapped
    4). A case whose id is in the frozen set is never rebuilt, even if its source changed.
    """
    test = [json.loads(line) for line in frozen.read_text().splitlines() if line.strip()]
    frozen_ids = {c["id"] for c in test}
    dev = [json.loads(line) for line in handwritten.read_text().splitlines() if line.strip()]
    dev += synonym_cases(lex)
    dev = [c for c in dev if c["id"] not in frozen_ids]
    for c in test:
        c["split"] = "test"
    for c in dev:
        c["split"] = "dev"
    cases = sorted(test + dev, key=lambda c: c["id"])
    missing = sorted({i for c in cases for i in c.get("expect_any", [])} - set(lex.by_id))
    if missing:
        raise SystemExit(f"cases name entries the lexicon doesn't have: {', '.join(missing)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    return cases


def load_cases(path: Path = CASES, split: str | None = None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [c for c in cases if split in (None, "all") or c["split"] == split]


# --- scoring -------------------------------------------------------------------------

def _protected(text: str) -> list[str]:
    spans = _FENCE_RE.findall(text)
    rest = _FENCE_RE.sub(" ", text)
    spans += _INLINE_RE.findall(rest)
    spans += _URL_RE.findall(_INLINE_RE.sub(" ", rest))
    return spans


def _norm(sentence: str) -> str:
    return " ".join(sentence.split()).strip()


def _sentences(text: str) -> list[str]:
    return [s for s in (_norm(p) for p in _SENTENCE_SPLIT.split(text)) if s]


def score(lex: Lexicon, case: dict, reply: str) -> dict:
    """Check one model reply against its case. Returns the checks and ``passed``."""
    text = case["text"]
    reply = (reply or "").strip()
    plain = MARKER.sub(lambda m: m.group(2), reply)
    markers = MARKER.findall(reply)
    used = [eid for eid, words in markers if eid in lex.by_id and lex.is_phrase(eid, words)]
    bad = [f"{eid}|{words}" for eid, words in markers
           if eid not in lex.by_id or not lex.is_phrase(eid, words)]

    checks: dict[str, bool] = {"markers": not bad}
    checks["protected"] = all(span in plain for span in _protected(text))

    original = set(_sentences(text))
    stray = [s for s in _sentences(reply) if not MARKER.search(s) and s not in original]
    ratio = len(plain) / max(1, len(text.strip()))
    checks["unchanged"] = not stray and 0.6 <= ratio <= 1.8

    if case.get("expect_none"):
        checks["expected"] = not markers
    elif case.get("expect_any"):
        checks["expected"] = any(eid in used for eid in case["expect_any"])
    return {"id": case["id"], "kind": case["kind"], "split": case["split"],
            "passed": all(checks.values()), "checks": checks, "used": used,
            "bad_markers": bad, "stray": stray[:3]}


def _wilson(passed: int, n: int, z: float = 1.96) -> list[float]:
    """95% interval for a pass rate, so small gaps between models aren't over-read."""
    if n == 0:
        return [0.0, 0.0]
    p = passed / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def summarize(results: Iterable[dict]) -> dict:
    results = list(results)
    by_kind: dict[str, list[int]] = {}
    failed: Counter[str] = Counter()
    for r in results:
        tally = by_kind.setdefault(r["kind"], [0, 0])
        tally[0] += r["passed"]
        tally[1] += 1
        failed.update(k for k, ok in r["checks"].items() if not ok)
    passed = sum(r["passed"] for r in results)
    # exposures only count in replies that passed, so broken rewrites don't earn credit
    exposures = sum(len(r["used"]) for r in results if r["passed"])
    latencies = [r["seconds"] for r in results if "seconds" in r]
    return {
        "cases": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "pass_rate_95ci": _wilson(passed, len(results)),
        "by_kind": {k: f"{p}/{n}" for k, (p, n) in sorted(by_kind.items())},
        "failed_checks": dict(failed.most_common()),
        "phrases_per_case": round(exposures / len(results), 2) if results else 0.0,
        "median_seconds": sorted(latencies)[len(latencies) // 2] if latencies else None,
    }


# --- running a model -----------------------------------------------------------------

Complete = Callable[[str, str], str]


def openai_complete(base_url: str, model: str, api_key: str = "",
                    timeout: float = 120.0) -> Complete:
    """A client for any OpenAI-compatible /chat/completions endpoint (Ollama, LM Studio,
    vLLM, OpenAI itself). Temperature 0, so runs are repeatable."""
    url = base_url.rstrip("/") + "/chat/completions"

    def complete(prompt: str, system: str) -> str:
        body = json.dumps({
            "model": model, "temperature": 0, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        }).encode()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"] or ""

    return complete


def run(lex: Lexicon, cases: list[dict], complete: Complete,
        prompt: Callable[[str], str] | None = None) -> list[dict]:
    """``prompt`` builds the model's prompt from an answer; the default is the production
    prompt over the whole lexicon (``--candidates`` swaps in the dedicated model's)."""
    prompt = prompt or lex.prompt
    results = []
    for case in cases:
        start = time.monotonic()
        try:
            reply = complete(prompt(case["text"]), SYSTEM)
            error = None
        except Exception as exc:  # a failed call counts as a failed case
            reply, error = "", f"{type(exc).__name__}: {exc}"
        result = score(lex, case, reply)
        result["seconds"] = round(time.monotonic() - start, 2)
        result["reply"] = reply
        if error:
            result["error"] = error
            result["passed"] = False
        results.append(result)
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"{mark} {case['id']} ({result['seconds']} s)", file=sys.stderr)
    return results


# --- command line --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lesmots.lexrender.evaluate",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--lexicon", type=Path, default=LEXICON)
    ap.add_argument("--cases", type=Path, default=CASES)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", help="rebuild cases.jsonl from handwritten.jsonl and the lexicon")

    p = sub.add_parser("run", help="run a model on the cases and score it")
    p.add_argument("--base-url", default=os.environ.get("LESMOTS_API_BASE", ""),
                   help="OpenAI-compatible base URL, e.g. http://127.0.0.1:11434/v1")
    p.add_argument("--model", default=os.environ.get("LESMOTS_MODEL", ""))
    p.add_argument("--api-key", default=os.environ.get("LESMOTS_API_KEY", ""))
    p.add_argument("--split", choices=["dev", "test", "all"], default="test")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", type=Path, help="write per-case results as JSONL")
    p.add_argument("--candidates", type=int, default=0,
                   help="offer only K candidate entries per answer (the dedicated model's "
                        "prompt, as in training); 0 = the whole lexicon")
    p.add_argument("--embed-model", default="",
                   help="Ollama embedding model for --candidates, e.g. nomic-embed-text")

    p = sub.add_parser("score", help="score replies collected elsewhere")
    p.add_argument("--outputs", type=Path, required=True,
                   help='JSONL of {"id": case id, "reply": model reply}')
    p.add_argument("--out", type=Path)

    p = sub.add_parser("prompt", help="print the prompt a model gets for one case")
    p.add_argument("case_id")

    args = ap.parse_args(argv)
    lex = Lexicon.load(args.lexicon)

    if args.cmd == "build":
        cases = build(lex, out=args.cases)
        kinds = Counter(c["kind"] for c in cases)
        splits = Counter(c["split"] for c in cases)
        print(f"{len(cases)} cases -> {args.cases}: {dict(kinds)}, {dict(splits)}")
        return 0

    cases = load_cases(args.cases)
    if args.cmd == "prompt":
        case = next((c for c in cases if c["id"] == args.case_id), None)
        if case is None:
            raise SystemExit(f"no case {args.case_id}")
        print(lex.prompt(case["text"]))
        return 0

    if args.cmd == "run":
        if not (args.base_url and args.model):
            raise SystemExit("--base-url and --model are required (or LESMOTS_API_BASE/MODEL)")
        chosen = [c for c in cases if args.split == "all" or c["split"] == args.split]
        if args.limit:
            chosen = chosen[: args.limit]
        prompt = None
        if args.candidates:
            from .candidates import CandidateIndex, OllamaEmbedder
            from .distill import student_prompt

            embed = (OllamaEmbedder(args.base_url, args.embed_model)
                     if args.embed_model else None)
            index = CandidateIndex(lex.entries, lex.matcher, embed)
            def prompt(text: str) -> str:
                return student_prompt(lex, text, index.select(text, args.candidates))
        results = run(lex, chosen, openai_complete(args.base_url, args.model, args.api_key),
                      prompt)
    else:
        by_id = {c["id"]: c for c in cases}
        replies = [json.loads(line) for line in args.outputs.read_text().splitlines()
                   if line.strip()]
        results = [score(lex, by_id[r["id"]], r["reply"]) for r in replies if r["id"] in by_id]

    if args.out:
        args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results))
    print(json.dumps(summarize(results), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
