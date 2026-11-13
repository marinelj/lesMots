"""Server-side rendering (remote mode, the paid tier's "MCP render").

The client sends its draft answer; the server's model rewrites it with the user's lexicon,
swaps a synonym for the entry's own phrase where that fits the context, links every
phrase it used, and logs the exposures. The text is processed in memory and never stored.

When the model can't run (no API key, a network error, a reply that doesn't look like
the answer), the result says ``fallback: "local"`` and returns the text unchanged, so the
agent renders it itself. The answer is never blocked.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable

from .forms import Match
from .store import Entry, Store

# (prompt, system, max_tokens) -> reply text. Defaults to lesmots.llm, which reads
# ANTHROPIC_API_KEY or an OpenAI-compatible provider from the environment.
Complete = Callable[[str, str, int], str]

MARKER = re.compile(r"\{\{([a-z0-9][a-z0-9-]*)\|([^{}|\n]+)\}\}")
MAX_INPUT_CHARS = 20_000
MAX_INDEX_WORDS = 8  # the case excerpts are too long to be used as phrases

SYSTEM = (
    "You edit an assistant's answer so it uses the reader's personal lexicon. You keep the "
    "meaning, facts, structure, code, URLs and Markdown exactly as they are."
)


def _default_complete(prompt: str, system: str, max_tokens: int) -> str:
    from lesmots import llm

    return llm._call(prompt, system=system, max_tokens=max_tokens)


def _prompt(text: str, entries: list[Entry], synonym_hits: list[Match]) -> str:
    index = "\n".join(f"{e.id} | {e.phrase}" for e in entries
                      if len(e.phrase.split()) <= MAX_INDEX_WORDS)
    lines = [
        ("Rewrite the ANSWER below so it uses phrases from the LEXICON where they fit "
        "naturally. Inflect them to fit the sentence (\"fall back to\" may become \"fell "
        "back to\"). Never force a phrase in where it changes the meaning or reads "
        "awkwardly; using none is fine."),
    ]
    if synonym_hits:
        lines.append(
            "These words in the answer are synonyms of lexicon entries. For each, decide "
            "from the context whether the entry's phrase is appropriate. If it is, replace "
            "the word with it; if not, leave the word as it is:")
        lines += [f'- "{h.text}" -> {h.entry_id}' for h in synonym_hits]
    lines += [
        ("Mark every lexicon phrase you used, including replaced synonyms, as "
        "{{entry-id|the words as they appear}}, for example {{fall-back-to|fell back to}}. "
        "Do not change anything inside code blocks or inline code. Output only the "
        "rewritten answer, with no preamble."),
        "",
        "LEXICON (id | phrase):",
        index,
        "",
        "ANSWER:",
        text,
    ]
    return "\n".join(lines)


def render(store: Store, account_id: str, text: str, surface: str, label: str = "",
           complete: Complete | None = None) -> dict[str, object]:
    if not text.strip():
        return {"rendered": text, "used": [], "via": {}, "fallback": None}
    if len(text) > MAX_INPUT_CHARS:
        return _fallback(text, f"answer longer than {MAX_INPUT_CHARS} characters")

    entries = store.entries(account_id)
    by_id = {e.id: e for e in entries}
    hits = store.matcher(account_id).find(text)
    synonym_hits = [h for h in hits if h.kind == "synonym"]
    already_there = {h.entry_id for h in hits if h.kind == "phrase"}

    try:
        reply = (complete or _default_complete)(
            _prompt(text, entries, synonym_hits), SYSTEM, max(600, len(text) // 2 + 400))
    except Exception as exc:  # any model failure falls back to local rendering
        return _fallback(text, f"{type(exc).__name__}: {exc}")
    reply = (reply or "").strip()
    plain = MARKER.sub(lambda m: m.group(2), reply)
    if len(plain) < 0.5 * len(text.strip()):
        return _fallback(text, "the model's reply did not look like the answer")

    used: list[str] = []
    via: dict[str, str] = {}
    synonym_of = {h.entry_id: h.via for h in synonym_hits}

    def link(m: re.Match) -> str:
        entry = by_id.get(m.group(1))
        words = m.group(2)
        if entry is None:
            return words
        if entry.id not in used:
            used.append(entry.id)
        if entry.id in synonym_of and entry.id not in already_there:
            via[entry.id] = synonym_of[entry.id]
        return f"[{words}]({entry.link})" if entry.link else words

    rendered = MARKER.sub(link, reply)
    event_id = "render-" + secrets.token_hex(8)
    if used:
        store.log_exposures(account_id, event_id, used, surface=surface, label=label,
                            mode="full", via=via)
    return {"rendered": rendered, "used": used, "via": via, "event_id": event_id,
            "fallback": None}


def _fallback(text: str, reason: str) -> dict[str, object]:
    return {"rendered": text, "used": [], "via": {}, "fallback": "local", "reason": reason}
