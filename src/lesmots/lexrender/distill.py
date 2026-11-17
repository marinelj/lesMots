"""Training data for a dedicated render model, distilled from a strong teacher model.

Three stages, all resumable, with every intermediate file kept in one ``--out`` folder:

1. ``seed``: the teacher writes short assistant-style answers. Most of them express the
   meaning of a chosen lexicon entry in plain words, *without* its phrase, so there is
   something to rewrite. The rest are plain answers with no target.
2. ``rewrite``: the teacher rewrites each answer the way the render model should, given
   the same short candidate list the student will see (``candidates.CandidateIndex``).
   The policy is aggressive: as many phrases as fit grammatically and keep the meaning.
3. ``export``: every rewrite is checked with the render scorer's rules (valid markers on
   offered entries only, code and URLs untouched, unmarked sentences unchanged, and at
   least one phrase when the answer had a target). Passing pairs become MLX-LM chat
   examples (``train.jsonl`` / ``valid.jsonl``); the rest go to ``rejected.jsonl``.

Answers too close to any held-out test case (``eval/render/cases.jsonl``, split ``test``)
are dropped, so the test set stays a fair exam.

The teacher is called in batches (10 answers per call by default) to save quota. When the
teacher fails several times in a row, for example because the quota ran out, the command
stops with exit code 75; running it again continues where it stopped.

Usage on the Mac Studio (teacher = Grok through the gateway; ``--codex-url`` and
``--base-url`` pick Codex or an OpenAI-compatible model instead)::

    python -m lesmots.lexrender.distill run --out ~/lexrender-distill \\
        --grok-url http://127.0.0.1:<port>/grokbot --target 3000
    python -m lesmots.lexrender.distill export --out ~/lexrender-distill
    python -m lesmots.lexrender.distill stats --out ~/lexrender-distill
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections.abc import Callable
from pathlib import Path

from .candidates import DEFAULT_K, CandidateIndex, OllamaEmbedder, content_words
from .evaluate import (
    CASES,
    LEXICON,
    Lexicon,
    _protected,
    _sentences,
    load_cases,
    openai_complete,
)
from .render import MARKER, SYSTEM, _prompt

BATCH = 10
PLAIN_SHARE = 0.2  # answers with no target phrase
MAX_PROMPT_BYTES = 24_000  # Grok's webhook cut a ~31 KB body mid-prompt; stay well under
MAX_FAILURES = 3  # consecutive teacher failures before stopping (exit 75)
NEAR_DUPLICATE = 0.6  # content-word Jaccard with a test case
VALID_SHARE = 0.05
EXIT_RETRY_LATER = 75

# (prompt, system) -> reply
Teacher = Callable[[str, str], str]

STYLES = [
    "a one-line chat reply", "a short status update", "a code review comment",
    "an explanation of a design trade-off", "a reply that includes a short code block",
    "a reply that mentions a URL", "a Markdown bullet list of two or three items",
    "an everyday (non-technical) remark", "an apology or correction", "a short plan",
]

SEED_SYSTEM = "You write realistic, varied example texts for training data. Output JSON only."
REWRITE_SYSTEM = SYSTEM


# --- files ---------------------------------------------------------------------------

def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _parse_json(reply: str) -> object:
    """The first JSON object or array in a reply (models sometimes add a code fence)."""
    match = re.search(r"[\[{].*[\]}]", reply, re.DOTALL)
    if not match:
        raise ValueError("no JSON in the teacher's reply")
    return json.loads(match.group(0))


# --- guarding the test set -----------------------------------------------------------

class TestGuard:
    def __init__(self, cases_path: Path = CASES):
        tests = [c for c in load_cases(cases_path) if c["split"] == "test"]
        self.texts = {" ".join(c["text"].split()).lower() for c in tests}
        self.words = [content_words(c["text"]) for c in tests]

    def too_close(self, text: str) -> bool:
        if " ".join(text.split()).lower() in self.texts:
            return True
        words = content_words(text)
        if not words:
            return False
        return any(len(words & w) / len(words | w) >= NEAR_DUPLICATE for w in self.words if w)


# --- stage 1: seed answers -----------------------------------------------------------

def seed_prompt(targets: list, plain: int, rng: random.Random) -> str:
    lines = [
        f"Write {len(targets) + plain} short texts of the kind an AI assistant writes to a "
        "user (1 to 4 sentences each). Vary the topic, tone and form; use these forms: "
        + ", ".join(rng.sample(STYLES, 4)) + ".",
        "",
        ("For each TARGET below, write one text that clearly expresses the target's meaning "
         "in plain, ordinary words, but does NOT use the target phrase itself or an obvious "
         "form of it. Someone who knows the phrase should see that it could replace part "
         "of the text."),
    ]
    for t in targets:
        lines.append(f'- TARGET {t.id}: "{t.phrase}" means: {t.definition[:200]}')
    if plain:
        lines.append(f"Also write {plain} text(s) with no target at all (\"target\": null).")
    lines += [
        "",
        'Reply with only a JSON array: [{"target": "<target id or null>", "text": "..."}]',
    ]
    return "\n".join(lines)


def seed(lex: Lexicon, teacher: Teacher, out: Path, batches: int, guard: TestGuard,
         seed_value: int = 0) -> int:
    """Run ``batches`` more seed batches. Returns the number of answers kept."""
    path = out / "sources.jsonl"
    done = {r["batch"] for r in _read(path)}
    pool = [e for e in lex.entries if len(e.phrase.split()) <= 8 and e.definition]
    kept = failures = 0
    batch = 0
    while batches > 0:
        if batch in done:
            batch += 1
            continue
        rng = random.Random(f"{seed_value}-{batch}")
        plain = round(BATCH * PLAIN_SHARE)
        targets = rng.sample(pool, BATCH - plain)
        try:
            items = _parse_json(teacher(seed_prompt(targets, plain, rng), SEED_SYSTEM))
            failures = 0
        except Exception as exc:  # a failed call or a reply that isn't JSON
            failures += 1
            print(f"seed batch {batch}: {type(exc).__name__}: {exc}", file=sys.stderr)
            if failures >= MAX_FAILURES:
                raise SystemExit(EXIT_RETRY_LATER) from exc
            continue
        rows = []
        wanted = {t.id for t in targets}
        for i, item in enumerate(items if isinstance(items, list) else []):
            text = str(item.get("text") or "").strip()
            target = item.get("target") or None
            if not text or (target and target not in wanted) or guard.too_close(text):
                continue
            if target and lex.is_phrase(target, text):
                continue  # the teacher used the phrase it was asked to avoid
            rows.append({"id": f"s{batch:04d}-{i:02d}", "batch": batch, "target": target,
                         "text": text})
        if not rows:  # still mark the batch done so it isn't retried forever
            rows = [{"id": f"s{batch:04d}-empty", "batch": batch, "target": None,
                     "text": "", "empty": True}]
        _append(path, rows)
        kept += sum(1 for r in rows if r["text"])
        batches -= 1
        batch += 1
    return kept


# --- stage 2: rewrites ---------------------------------------------------------------

def candidates_for(index: CandidateIndex, lex: Lexicon, source: dict, k: int) -> list:
    chosen = index.select(source["text"], k)
    target = source.get("target")
    if target and target in lex.by_id and all(e.id != target for e in chosen):
        chosen = [*chosen[: k - 1], lex.by_id[target]]
    return chosen


def student_prompt(lex: Lexicon, text: str, candidates: list) -> str:
    """Exactly the prompt the student model gets: the render prompt over the candidates."""
    ids = {e.id for e in candidates}
    hits = [h for h in lex.matcher.find(text) if h.kind == "synonym" and h.entry_id in ids]
    return _prompt(text, candidates, hits)


def rewrite_prompt(lex: Lexicon, items: list[tuple[dict, list]]) -> str:
    parts = [
        ("Rewrite each ANSWER below with the reader's lexicon, following these rules. The "
        "reader is learning these phrases, so use as many from that answer's LEXICON as "
        "fit: whenever one can replace the wording grammatically and keep the meaning, use "
        "it. Never use one that changes the meaning or the facts. Inflect phrases to fit "
        "(\"fall back to\" may become \"fell back to\"). Mark every phrase you used as "
        "{{entry-id|the words as they appear}}, where the marked words are the phrase "
        "itself; rewrite the sentence around the phrase rather than marking the original "
        "words. Keep everything else exactly as it is: unchanged sentences stay word for "
        "word, and code blocks, inline code, URLs and Markdown never change. Use only "
        "entries from that answer's own LEXICON."),
        "",
        'Reply with only a JSON object: {"<answer id>": "<rewritten answer>", ...}',
    ]
    for source, cands in items:
        index = "\n".join(f"{e.id} | {e.phrase}" for e in cands)
        parts += ["", f"=== ANSWER {source['id']} ===", "LEXICON (id | phrase):", index,
                  "ANSWER:", source["text"]]
    return "\n".join(parts)


def _batches(lex: Lexicon, items: list[tuple[dict, list]], size: int,
             max_bytes: int) -> list[list[tuple[dict, list]]]:
    """Up to ``size`` answers per call, fewer when the prompt would pass ``max_bytes``
    (0 = no byte limit). An answer too long for the limit on its own still goes alone."""
    out: list[list[tuple[dict, list]]] = []
    current: list[tuple[dict, list]] = []
    for item in items:
        trial = current + [item]
        too_big = max_bytes and len(rewrite_prompt(lex, trial).encode()) > max_bytes
        if current and (len(trial) > size or too_big):
            out.append(current)
            trial = [item]
        current = trial
    if current:
        out.append(current)
    return out


def rewrite(lex: Lexicon, index: CandidateIndex, teacher: Teacher, out: Path,
            limit: int = 0, k: int = DEFAULT_K, max_bytes: int = 0) -> int:
    """Rewrite sources that have no rewrite yet. Returns how many were rewritten."""
    path = out / "rewrites.jsonl"
    done = {r["id"] for r in _read(path)}
    todo = [s for s in _read(out / "sources.jsonl") if s["text"] and s["id"] not in done]
    if limit:
        todo = todo[:limit]
    written = failures = 0
    items = [(s, candidates_for(index, lex, s, k)) for s in todo]
    for batch in _batches(lex, items, BATCH, max_bytes):
        try:
            reply = _parse_json(teacher(rewrite_prompt(lex, batch), REWRITE_SYSTEM))
            failures = 0
        except Exception as exc:
            failures += 1
            print(f"rewrite batch at {batch[0][0]['id']}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            if failures >= MAX_FAILURES:
                raise SystemExit(EXIT_RETRY_LATER) from exc
            continue
        rows = []
        for source, cands in batch:
            text = reply.get(source["id"]) if isinstance(reply, dict) else None
            rows.append({"id": source["id"], "rewrite": text if isinstance(text, str) else "",
                         "candidates": [e.id for e in cands]})
        _append(path, rows)
        written += len(rows)
    return written


# --- stage 3: check and export -------------------------------------------------------

def check(lex: Lexicon, source: dict, rewrite_row: dict) -> tuple[bool, str]:
    text, reply = source["text"], (rewrite_row.get("rewrite") or "").strip()
    if not reply:
        return False, "empty"
    offered = set(rewrite_row["candidates"])
    markers = MARKER.findall(reply)
    for eid, words in markers:
        if eid not in offered or not lex.is_phrase(eid, words):
            return False, f"bad marker {eid}|{words}"
    plain = MARKER.sub(lambda m: m.group(2), reply)
    if not all(span in plain for span in _protected(text)):
        return False, "protected text changed"
    original = set(_sentences(text))
    if any(not MARKER.search(s) and s not in original for s in _sentences(reply)):
        return False, "unmarked sentence changed"
    if not 0.6 <= len(plain) / max(1, len(text)) <= 1.8:
        return False, "length changed too much"
    if source.get("target") and not markers:
        return False, "no phrase used"
    return True, ""


def export(lex: Lexicon, out: Path) -> dict:
    sources = {s["id"]: s for s in _read(out / "sources.jsonl") if s["text"]}
    train, valid, rejected = [], [], []
    seen: set[str] = set()
    for row in _read(out / "rewrites.jsonl"):
        source = sources.get(row["id"])
        if source is None or source["text"] in seen:
            continue
        ok, why = check(lex, source, row)
        if not ok:
            rejected.append({"id": row["id"], "why": why, "rewrite": row.get("rewrite")})
            continue
        seen.add(source["text"])
        cands = [lex.by_id[i] for i in row["candidates"] if i in lex.by_id]
        example = {"messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": student_prompt(lex, source["text"], cands)},
            {"role": "assistant", "content": row["rewrite"].strip()},
        ]}
        bucket = hashlib.sha256(row["id"].encode()).digest()[0] / 256
        (valid if bucket < VALID_SHARE else train).append(example)
    for name, rows in (("train", train), ("valid", valid), ("rejected", rejected)):
        (out / f"{name}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return {"train": len(train), "valid": len(valid), "rejected": len(rejected)}


def stats(out: Path) -> dict:
    sources = [s for s in _read(out / "sources.jsonl") if s["text"]]
    rewrites = _read(out / "rewrites.jsonl")
    return {"sources": len(sources),
            "with_target": sum(1 for s in sources if s.get("target")),
            "rewritten": len(rewrites),
            "train": len(_read(out / "train.jsonl")),
            "valid": len(_read(out / "valid.jsonl")),
            "rejected": len(_read(out / "rejected.jsonl"))}


# --- command line --------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def grok_complete(base_url: str, timeout: float = 1800.0, poll: float = 5.0,
                  http=None, sleep=None, clock=None) -> Teacher:
    """Grok through the grokbot gateway: POST ``{base}/v1/runs`` with ``{prompt}``, then
    poll ``GET {base}/v1/runs/{run_id}`` until Grok posts its result back.

    Grok runs in its desktop app and has no "waiting" status: a run stays ``running``
    until the result callback arrives, and the gateway marks it ``timed_out`` after 30
    minutes. Stray control characters in the output are removed, since they would break
    the JSONL files.
    """
    import time
    import urllib.parse

    from .codex import FINAL, _http

    http = http or _http
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    base = base_url.rstrip("/")

    def complete(prompt: str, system: str) -> str:
        try:
            return once(prompt, system)
        except Exception as first:  # one retry: a truncated webhook body usually goes
            print(f"Grok run failed, retrying once: {first}", file=sys.stderr)  # through
            return once(prompt, system)

    def once(prompt: str, system: str) -> str:
        run = http("POST", f"{base}/v1/runs", {"prompt": f"{system}\n\n{prompt}"})
        run_id = run.get("run_id")
        if not run_id:
            raise RuntimeError(f"no run_id in the Grok submit response: {run}")
        url = f"{base}/v1/runs/{urllib.parse.quote(str(run_id), safe='')}"
        deadline = clock() + timeout
        status = run.get("status", "")
        while status not in FINAL:
            if clock() >= deadline:
                raise RuntimeError(f"Grok run {run_id} still {status or 'pending'} "
                                   f"after {timeout:.0f}s")
            sleep(poll)
            run = http("GET", url, None)
            status = run.get("status", "")
        output = run.get("final_output")
        if status != "completed" or not output:
            raise RuntimeError(f"Grok run {run_id} {status} with "
                               f"{'no output' if not output else 'output'}")
        return _CONTROL.sub("", str(output))

    return complete


def _teacher(args: argparse.Namespace) -> Teacher:
    if args.grok_url:
        return grok_complete(args.grok_url, timeout=max(args.timeout, 1800.0))
    if args.codex_url:
        from .codex import CodexConfig, codex_complete

        complete = codex_complete(CodexConfig(
            base_url=args.codex_url.rstrip("/"), users=frozenset({"*"}), model=args.model,
            effort=args.effort, timeout=args.timeout, fresh=True))
        return lambda prompt, system: complete(prompt, system, 4000)
    if args.base_url:
        return openai_complete(args.base_url, args.model, args.api_key, args.timeout)
    raise SystemExit("give --grok-url (Grok gateway), --codex-url (Codex gateway) "
                     "or --base-url (OpenAI-compatible)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lesmots.lexrender.distill",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="working folder")
    ap.add_argument("--lexicon", type=Path, default=LEXICON)
    ap.add_argument("--k", type=int, default=DEFAULT_K, help="candidates per answer")
    ap.add_argument("--embed-model", default="",
                    help="Ollama embedding model for candidates, e.g. nomic-embed-text")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--grok-url", default="", help="Grok gateway base, e.g. .../grokbot")
    ap.add_argument("--codex-url", default="", help="Codex gateway base, e.g. .../codex")
    ap.add_argument("--base-url", default="", help="or an OpenAI-compatible endpoint")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--max-prompt-bytes", type=int, default=MAX_PROMPT_BYTES,
                    help="split rewrite batches above this size (0 = no limit)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed", help="stage 1: write source answers")
    p.add_argument("--batches", type=int, default=10)
    p = sub.add_parser("rewrite", help="stage 2: rewrite source answers")
    p.add_argument("--limit", type=int, default=0)
    p = sub.add_parser("run", help="stages 1 and 2 in turn until --target sources exist")
    p.add_argument("--target", type=int, default=3000)
    sub.add_parser("export", help="stage 3: check rewrites, write train/valid JSONL")
    sub.add_parser("stats", help="counts so far")
    args = ap.parse_args(argv)

    args.out = args.out.expanduser()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "stats":
        print(json.dumps(stats(args.out), indent=2))
        return 0
    lex = Lexicon.load(args.lexicon)
    if args.cmd == "export":
        print(json.dumps(export(lex, args.out), indent=2))
        return 0

    embed = (OllamaEmbedder(args.ollama, args.embed_model, args.out / "cache")
             if args.embed_model else None)
    index = CandidateIndex(lex.entries, lex.matcher, embed)
    teacher = _teacher(args)
    guard = TestGuard()
    if args.cmd == "seed":
        print(f"kept {seed(lex, teacher, args.out, args.batches, guard)} answers")
    elif args.cmd == "rewrite":
        print(f"rewrote {rewrite(lex, index, teacher, args.out, args.limit, args.k,
                                  args.max_prompt_bytes)}")
    else:
        idle = 0
        while stats(args.out)["sources"] < args.target and idle < 3:
            kept = seed(lex, teacher, args.out, 5, guard)
            rewrite(lex, index, teacher, args.out, 0, args.k, args.max_prompt_bytes)
            idle = 0 if kept else idle + 1  # a teacher that returns nothing usable: stop
        rewrite(lex, index, teacher, args.out, 0, args.k, args.max_prompt_bytes)
        print(json.dumps(export(lex, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
