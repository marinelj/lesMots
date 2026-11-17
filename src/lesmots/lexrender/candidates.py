"""Pick the lexicon entries worth offering a render model for one answer.

The production prompt lists the whole lexicon (about 600 phrases, 5,500 tokens). A small
local model reads that slowly and is easily distracted by it, and training on prompts that
long is slow. A dedicated model instead gets a short candidate list per answer, chosen here,
and the same function builds the training prompts and the live ones, so the model is
always asked the question it was trained on.

Candidates, in order, up to ``k``:

1. entries whose phrase or a synonym already appears in the answer (the matcher's hits);
2. with an embedder (``OllamaEmbedder``, e.g. ``nomic-embed-text``), the entries closest in
   meaning to any sentence of the answer. This is what finds paraphrase opportunities
   ("uses the database instead" -> "fall back to"); word overlap alone misses about half
   of the hand-written paraphrase cases;
3. entries whose phrase, synonyms or definition share the most content words with the
   answer;
4. a few stable fillers, so the model always sees phrases it should *not* use.

Deterministic for a given embedder. Without one it is pure Python, with no network.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .forms import Matcher

DEFAULT_K = 40
FILLERS = 5  # distractors per list
MAX_PHRASE_WORDS = 8

_WORD = re.compile(r"[a-z][a-z'-]+")
STOPWORDS = frozenset(["a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "someone", "something", "sth", "sb", "etc", "use", "used", "using", "one", "ones", "get", "gets", "make", "makes"])


class EntryLike(Protocol):
    id: str
    phrase: str
    definition: str
    synonyms: list[str]


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower().replace("’", "'"))
            if w not in STOPWORDS and len(w) > 2}


# texts -> one vector per text
Embed = Callable[[list[str]], list[list[float]]]
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
EMBED_SHARE = 0.5  # at most this share of the list comes from embeddings


class OllamaEmbedder:
    """Embeddings from Ollama's ``/api/embed`` (e.g. ``nomic-embed-text``), with entry
    vectors cached on disk so they are computed once per lexicon."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 model: str = "nomic-embed-text", cache_dir: Path | None = None):
        self.url = base_url.rstrip("/").removesuffix("/v1") + "/api/embed"
        self.model = model
        self.cache_dir = cache_dir

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            body = json.dumps({"model": self.model, "input": texts[i:i + 64]}).encode()
            req = urllib.request.Request(self.url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                out += json.load(resp)["embeddings"]
        return out

    def cached(self, key: str, texts: list[str]) -> list[list[float]]:
        if self.cache_dir is None:
            return self(texts)
        path = self.cache_dir / f"emb-{self.model.replace(':', '_')}-{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        vectors = self(texts)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vectors))
        return vectors


def _unit(v: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class CandidateIndex:
    """Build once per lexicon version; ``select`` per answer."""

    def __init__(self, entries: Sequence[EntryLike], matcher: Matcher,
                 embed: Embed | None = None):
        self.entries = [e for e in entries if len(e.phrase.split()) <= MAX_PHRASE_WORDS]
        self.matcher = matcher
        self.embed = embed
        self._vectors: list[list[float]] = []
        if embed is not None:
            docs = [f"{e.phrase}: {e.definition}" for e in self.entries]
            key = hashlib.sha256("\n".join(docs).encode()).hexdigest()[:16]
            cached = getattr(embed, "cached", None)
            raw = cached(key, docs) if cached else embed(docs)
            self._vectors = [_unit(v) for v in raw]
        self._by_id = {e.id: e for e in self.entries}
        # phrase and synonym words count double: they are what a rewrite would replace
        self._strong = {e.id: content_words(" ".join([e.phrase, *e.synonyms]))
                        for e in self.entries}
        self._weak = {e.id: content_words(e.definition) for e in self.entries}

    def select(self, text: str, k: int = DEFAULT_K) -> list[EntryLike]:
        chosen: list[str] = []
        for hit in self.matcher.find(text):
            if hit.entry_id in self._by_id and hit.entry_id not in chosen:
                chosen.append(hit.entry_id)
        if self._vectors:
            sentences = [p for p in (x.strip() for x in _SENTENCES.split(text)) if p][:20]
            best = [0.0] * len(self.entries)
            for sv in self.embed(sentences):
                u = _unit(sv)
                for i, ev in enumerate(self._vectors):
                    sim = sum(a * b for a, b in zip(u, ev))
                    best[i] = max(best[i], sim)
            ranked = sorted(range(len(self.entries)), key=lambda i: -best[i])
            quota = int(k * EMBED_SHARE)
            for i in ranked:
                if quota <= 0 or len(chosen) >= k - FILLERS:
                    break
                if self.entries[i].id not in chosen:
                    chosen.append(self.entries[i].id)
                    quota -= 1
        words = content_words(text)
        scored = []
        for e in self.entries:
            if e.id in chosen:
                continue
            score = 2 * len(words & self._strong[e.id]) + len(words & self._weak[e.id])
            if score:
                scored.append((-score, e.id))
        scored.sort()
        room = max(0, k - FILLERS - len(chosen))
        chosen += [eid for _, eid in scored[:room]]
        # stable fillers: same answer, same list
        seed = hashlib.sha256(text.encode()).hexdigest()
        ranked = sorted(self.entries, key=lambda e: hashlib.sha256(
            (seed + e.id).encode()).hexdigest())
        for e in ranked:
            if len(chosen) >= k:
                break
            if e.id not in chosen:
                chosen.append(e.id)
        return [self._by_id[eid] for eid in chosen[:k]]
