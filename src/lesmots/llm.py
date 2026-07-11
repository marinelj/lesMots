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
        f"Continue the following short English story with ONE more simple paragraph "
        f"(maximum {max_words} words). Naturally use as MANY of these vocabulary items "
        f"as possible, and wrap each one you use in **double asterisks**:\n"
        f"{', '.join(words)}\n\n"
        f"Story so far:\n{story_so_far}",
        system="You write simple, clear English for language learners.",
        max_tokens=400,
    )


def rewrite(title: str, summary: str, words: list[str], max_words: int = 120) -> str:
    """Rewrite fetched content using as many of the given bank words as possible."""
    return _call(
        f"Rewrite the following tech news into one short, simple English paragraph "
        f"(maximum {max_words} words). Naturally use as MANY of these vocabulary items "
        f"as possible, and wrap each one you use in **double asterisks**:\n"
        f"{', '.join(words)}\n\n"
        f"Title: {title}\n"
        f"Content: {summary}",
        system="You write simple, clear English for language learners.",
        max_tokens=400,
    )
