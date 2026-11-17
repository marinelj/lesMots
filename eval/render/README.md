# Render model test set

A fixed test set for the model behind remote render, so Codex, a stock local model
(Ollama) and a fine-tuned adapter can be compared on the same cases.

- `handwritten.jsonl`: 75 hand-written cases. The match policy is aggressive: exposure
  counts, so the summary also reports `phrases_per_case`.
  - `paraphrase`: a phrase fits, but the answer says it in plain words.
  - `open`: plain answers; any correct use of phrases passes (aggressive policy).
  - `none`: five texts no phrase can touch without changing the facts ("Yes.").
  - `protected`: code, URLs and Markdown that must come back unchanged.
- `test-frozen.jsonl`: the 95 held-out `test` cases, frozen on 2026-11-17 as they were
  when every step 2 baseline was scored. `build` copies them as they are and never
  rebuilds them, so scores stay comparable across lexicon versions (rebuilding from v17
  used to swap 4 synonym cases between the splits). Never use them, or their sentences,
  as training data. Don't add, remove or reword cases: a new test set is a new file.
  The only allowed edit is an answer-key fix that accepts another valid answer, logged
  here with the date:
  - 2026-11-17: four synonym cases swapped in a word that is itself a lexicon entry, so
    linking that entry is also right: `syn-divisor` (+`denominator`), `syn-inherit`
    (+`derive`), `syn-underwrite` (+`guarantee-noun`), `syn-lucrative` (+`profitable`).
    `build` no longer makes such cases. Two judgment calls: `syn-capable`
    (+`capable-of-gerund`, the entry for "capable of doing") and `syn-meantime`
    (+`meanwhile`). The matcher also started accepting
    "baked into" for "be baked into", "feel the shake" for "feel-the-shake" and
    separable phrasal verbs ("hand the work off to"), which fixes `para-24`, `para-12`
    and `syn-feel-the-shake` without editing them.
  - 2026-11-17: single everyday words are never linked (`forms.COMMON_WORDS`, Marine's
    rule). Six synonym cases targeted such an entry, so they now expect no link:
    `syn-latest`, `syn-reject`, `syn-spring`, `syn-square`, `syn-stick`, `syn-swap`.
- `cases.jsonl`: the frozen test cases plus every other hand-written case and up to 120
  `synonym` cases built from lexicon examples, all as `dev`. Rebuild it after editing
  the hand-written cases or the lexicon:

      python -m lesmots.lexrender.evaluate build

- `challenge.jsonl`: 20 public edge cases kept apart from the test set, because the
  reference models already pass almost every test case: prompt injection, other
  languages, Markdown tables, nested lists with code, existing links, JSON-only and
  emoji-only answers, HTML, template braces, LaTeX, shell transcripts, quotes, a long
  answer with four openings, curly quotes. Score it with `--cases eval/render/challenge.jsonl
  --split all`.

Check that the shortlist the student model sees holds the expected entry (add
`--embed-model nomic-embed-text` on the Mac Studio):

    python -m lesmots.lexrender.evaluate recall --split test

Score a model on any OpenAI-compatible endpoint, for example Ollama on the Mac Studio:

    python -m lesmots.lexrender.evaluate run --base-url http://127.0.0.1:11434/v1 \
        --model qwen2.5:7b --split test --out qwen-test.jsonl

Replies collected some other way (for example from Codex) can be scored too:

    python -m lesmots.lexrender.evaluate score --outputs replies.jsonl

The checks (all must pass) are listed at the top of `src/lesmots/lexrender/evaluate.py`.
