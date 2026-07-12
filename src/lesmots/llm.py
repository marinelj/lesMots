"""LLM backend (stdlib urllib — zero dependencies).

Two supported backends, picked automatically:

1. Anthropic API:            set ANTHROPIC_API_KEY
2. Any OpenAI-compatible API: set LESMOTS_API_KEY + LESMOTS_API_BASE + LESMOTS_MODEL
   Works with DeepSeek, Qwen/DashScope, Zhipu GLM, Moonshot, OpenAI, Ollama, etc.

   # DeepSeek example:
   export LESMOTS_API_KEY=sk-...
   export LESMOTS_API_BASE=https://api.deepseek.com
   export LESMOTS_MODEL=deepseek-chat

   # Qwen (DashScope) example:
   export LESMOTS_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
   export LESMOTS_MODEL=qwen-plus

   # Local Ollama (free, offline):
   export LESMOTS_API_KEY=ollama
   export LESMOTS_API_BASE=http://localhost:11434/v1
   export LESMOTS_MODEL=qwen2.5

Every function degrades gracefully when nothing is configured.
"""

from __future__ import annotations

import json
import os
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5"


class LLMNotConfigured(RuntimeError):
    pass


def _openai_base() -> str:
    return (os.environ.get("LESMOTS_API_BASE") or "").rstrip("/")


def is_configured() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    return bool(os.environ.get("LESMOTS_API_KEY") and _openai_base())


def _call_anthropic(prompt: str, system: str, max_tokens: int) -> str:
    body = {
        "model": os.environ.get("LESMOTS_MODEL", ANTHROPIC_DEFAULT_MODEL),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return "".join(block.get("text", "") for block in data.get("content", [])).strip()


def _call_openai_compatible(prompt: str, system: str, max_tokens: int) -> str:
    model = os.environ.get("LESMOTS_MODEL")
    if not model:
        raise LLMNotConfigured("Set LESMOTS_MODEL (e.g. deepseek-chat, qwen-plus).")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(
        _openai_base() + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {os.environ['LESMOTS_API_KEY']}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return (data["choices"][0]["message"]["content"] or "").strip()


def _call(prompt: str, system: str = "", max_tokens: int = 500) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_anthropic(prompt, system, max_tokens)
    if os.environ.get("LESMOTS_API_KEY") and _openai_base():
        return _call_openai_compatible(prompt, system, max_tokens)
    raise LLMNotConfigured(
        "Set ANTHROPIC_API_KEY, or LESMOTS_API_KEY + LESMOTS_API_BASE + LESMOTS_MODEL "
        "(any OpenAI-compatible provider: DeepSeek, Qwen, GLM, Moonshot, Ollama...)."
    )


def explain(text: str, language: str = "Chinese") -> str:
    """Explain a word/phrase/sentence in simple English plus the target language."""
    return _call(
        f'Explain this English input for a language learner: "{text}"\n\n'
        f"Give: (1) a one-sentence explanation in very simple English, "
        f"(2) the translation/explanation in {language}, "
        f"(3) one short example sentence. Be brief, no headers.",
        system="You are a concise vocabulary tutor.",
        max_tokens=300,
    )


def extend(story_so_far: str, words: list[str], max_words: int = 120) -> str:
    """Continue an already-rewritten story with one more learner-friendly paragraph."""
    return _call(
        f"Here is the story so far:\n---\n{story_so_far}\n---\n\n"
        f"Write ONLY the next paragraph (maximum {max_words} words). It must pick up "
        f"exactly where the last sentence stops and add NEW information or a next step: "
        f"same topic, same facts, same tone. Do not restart the story, do not summarize, "
        f"and do not repeat anything already written above.\n"
        f"Where they fit naturally, use vocabulary items from this list and wrap each "
        f"one you use in **double asterisks** (never force one in where it breaks the "
        f"flow). If an item is not English, keep it EXACTLY as written in its original "
        f"language — never translate it:\n{', '.join(words)}",
        system="You write simple, clear English for language learners. "
               "You continue stories seamlessly and coherently.",
        max_tokens=400,
    )


def suggest_corrections(text: str) -> list[str]:
    """Up to 3 likely intended spellings if `text` looks misspelled; [] if it's fine."""
    raw = _call(
        f'A learner typed this into an English vocabulary app: "{text}"\n'
        f"If it contains a spelling mistake, reply with a JSON array of up to 3 likely "
        f"intended corrections, best guess first. If it is spelled correctly (including "
        f"proper nouns and acronyms), reply with []. Reply with the JSON array ONLY.",
        system="You are a strict spell-checker. Output a JSON array of strings, nothing else.",
        max_tokens=100,
    )
    return _parse_json_list(raw)


def _parse_json_list(raw: str) -> list[str]:
    """Extract a JSON array of strings from a model reply (tolerates fences/prose)."""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [i.strip() for i in items if isinstance(i, str) and i.strip()][:3]


def rewrite(title: str, summary: str, words: list[str], max_words: int = 120) -> str:
    """Rewrite fetched content using as many of the given bank words as possible."""
    return _call(
        f"Rewrite the following news into one short, simple English paragraph "
        f"(maximum {max_words} words). Naturally use as MANY of these vocabulary items "
        f"as possible, and wrap each one you use in **double asterisks**. If an item is "
        f"not English, keep it EXACTLY as written in its original language — never "
        f"translate it:\n{', '.join(words)}\n\n"
        f"Title: {title}\n"
        f"Content: {summary}",
        system="You write simple, clear English for language learners.",
        max_tokens=400,
    )
