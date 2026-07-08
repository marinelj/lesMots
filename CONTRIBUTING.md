# Contributing to LesMots

Merci ! Contributions of all kinds are welcome: bug reports, docs, word lists, code.

## Setup

```bash
git clone https://github.com/marine/lesmots
cd lesmots
pip install -e ".[dev]"
```

## Before opening a PR

1. Run the tests: `pytest`
2. Lint: `ruff check .`
3. Keep the zero-dependency rule — the core package uses only the standard library.
4. Add tests for new behavior.

## Ideas that would make great first PRs

- `lesmots export` (CSV/Anki format)
- Reverse-direction study (back → front)
- Per-tag study filters
- Starter decks (common French/Spanish/German word lists as CSVs in `decks/`)

## Reporting bugs

Open a GitHub issue with your OS, Python version, and the command that failed.
