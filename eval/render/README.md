# Render model test set

A fixed test set for the model behind remote render, so Codex, a stock local model
(Ollama) and a fine-tuned adapter can be compared on the same cases.

- `handwritten.jsonl`: 75 hand-written cases. The match policy is aggressive: exposure
  counts, so the summary also reports `phrases_per_case`.
  - `paraphrase`: a phrase fits, but the answer says it in plain words.
  - `open`: plain answers; any correct use of phrases passes (aggressive policy).
  - `none`: five texts no phrase can touch without changing the facts ("Yes.").
  - `protected`: code, URLs and Markdown that must come back unchanged.
- `cases.jsonl`: the hand-written cases plus 120 `synonym` cases built from lexicon
  examples, each with a stable `dev` / `test` split. Rebuild it after editing the
  hand-written cases or the lexicon:

      python -m lesmots.lexrender.evaluate build

  `test` cases are held out: never use them, or their sentences, as training data.

Score a model on any OpenAI-compatible endpoint, for example Ollama on the Mac Studio:

    python -m lesmots.lexrender.evaluate run --base-url http://127.0.0.1:11434/v1 \
        --model qwen2.5:7b --split test --out qwen-test.jsonl

Replies collected some other way (for example from Codex) can be scored too:

    python -m lesmots.lexrender.evaluate score --outputs replies.jsonl

The checks (all must pass) are listed at the top of `src/lesmots/lexrender/evaluate.py`.
