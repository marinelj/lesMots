"""The daily job: fetch popular content, rewrite it with SM-2-picked bank words.

Run via `lesmots daily` (cron/scheduler-friendly) or on demand from show-me.
fetch_fn / rewrite_fn are injectable for testing and offline fallback.
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Callable, Optional

from lesmots import llm, fetcher
from lesmots.memory import Memory


def _default_fetch() -> Callable:
    if os.environ.get("LESMOTS_NEWS_SOURCE") == "cn":  # China edition (M2)
        from lesmots import fetcher_cn
        return fetcher_cn.fetch_popular_cn
    return fetcher.fetch_popular


def _fallback_rewrite(title: str, summary: str, words: list[str], max_words: int) -> str:
    """No-LLM fallback: simple digest that still exposes the picked words."""
    body = f"Today's pick: {title}. {summary} "
    body += "Practice items: " + ", ".join(f"**{w}**" for w in words) + "."
    tokens = body.split()
    return " ".join(tokens[:max_words])


def _fallback_extend(story_so_far: str, words: list[str], max_words: int) -> str:
    """No-LLM fallback continuation that still exposes the picked words."""
    body = "The story continues. More practice items: " + ", ".join(f"**{w}**" for w in words) + "."
    tokens = body.split()
    return " ".join(tokens[:max_words])


def words_used_in(text: str, candidates: list[str]) -> list[str]:
    """Which candidate words actually appear in the generated text."""
    used = []
    for w in candidates:
        if re.search(re.escape(w), text, flags=re.IGNORECASE):
            used.append(w)
    return used


def generate(memory: Memory,
             fetch_fn: Optional[Callable] = None,
             rewrite_fn: Optional[Callable] = None) -> dict:
    """Produce prepared content and store it (unconsumed) in memory."""
    fetch_fn = fetch_fn or _default_fetch()
    story = fetch_fn(memory.config["interests"])

    picked = [w.text for w in memory.pick()]
    max_words = memory.config["max_words"]

    if rewrite_fn is None:
        if llm.is_configured():
            rewrite_fn = llm.rewrite
        else:
            rewrite_fn = _fallback_rewrite
    rewritten = rewrite_fn(story["title"], story["text"], picked, max_words)

    memory.prepared = {
        "date": date.today().isoformat(),
        "original": story,
        "rewritten": rewritten,
        "words_used": words_used_in(rewritten, picked),
        "consumed": False,
    }
    return memory.prepared


def show_me(memory: Memory,
            fetch_fn: Optional[Callable] = None,
            rewrite_fn: Optional[Callable] = None) -> dict:
    """Return prepared content; generate fresh if none/consumed.
    Updates exposure counts + familiarity for the words used, marks consumed."""
    if not memory.prepared or memory.prepared.get("consumed"):
        generate(memory, fetch_fn, rewrite_fn)
    prepared = memory.prepared
    memory.record_exposure(prepared.get("words_used", []))
    prepared["consumed"] = True
    return prepared


def keep_reading(memory: Memory, extend_fn: Optional[Callable] = None) -> dict:
    """Extend the current story with one more paragraph of bank words.
    Updates exposure counts + familiarity for the newly used words."""
    if not memory.prepared:
        raise ValueError("No story to extend — start a New Journey first.")

    picked = [w.text for w in memory.pick()]
    max_words = memory.config["max_words"]

    if extend_fn is None:
        extend_fn = llm.extend if llm.is_configured() else _fallback_extend
    story_ctx = memory.prepared["rewritten"]
    title = (memory.prepared.get("original") or {}).get("title")
    if title:  # anchor the continuation to the source topic
        story_ctx = f"Topic: {title}\n\n{story_ctx}"
    continuation = extend_fn(story_ctx, picked, max_words)

    used = words_used_in(continuation, picked)
    memory.prepared["rewritten"] += "\n\n" + continuation
    memory.prepared["words_used"] = sorted(set(memory.prepared.get("words_used", [])) | set(used))
    memory.record_exposure(used)
    return memory.prepared
