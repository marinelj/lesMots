"""Parse a lexicon Markdown file (the format of ``lexicon/lexicon.md``) into entries.

Each ``### N.M heading`` under a numbered ``## N. Section`` is one entry. The phrase is the
first backticked chunk of the heading (``### 1.57 `degrade gracefully` ``); further chunks
("`well-worn` vs. `well-trodden`") and slash variants become aliases. Headings without
backticks (the case excerpts in section 5) use the heading text as the phrase.

Sections listed in ``SKIP_SECTIONS`` hold instructions for agents, not phrases, and are
left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SKIP_SECTIONS = ("slash-command", "sync protocol")

_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.*?)\s*(?:\(\d+\s+items?\))?\s*$", re.IGNORECASE)
_ENTRY_RE = re.compile(r"^###\s+(\d+\.\d+)\s+(.*\S)\s*$")
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_DEFINITION_RE = re.compile(r"\*\*Definition\*\*\s*:\s*(.+)")
SYNONYMS_RE = re.compile(r"^\s*[*-]\s+\*\*Synonyms\*\*\s*:\s*(.+)$", re.MULTILINE)


@dataclass
class ParsedEntry:
    id: str
    number: str
    section: str
    phrase: str
    aliases: list[str]
    body: str
    anchor: str
    definition: str = ""
    synonyms: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)


def parse_synonyms(text: str) -> list[str]:
    """'refined, elaborate; `polished`' -> ['refined', 'elaborate', 'polished']"""
    out: list[str] = []
    for part in re.split(r"[,;|]", text):
        word = part.strip().strip("`*_").strip()
        if word and word.lower() not in (w.lower() for w in out):
            out.append(word)
    return out


def github_anchor(heading_text: str) -> str:
    """The anchor GitHub gives a heading (before de-duplication)."""
    text = heading_text.strip().lower()
    kept = "".join(c for c in text if c.isalnum() or c in " -_")
    return kept.replace(" ", "-")


def slugify(phrase: str, max_len: int = 64) -> str:
    text = phrase.replace("’", "'").lower()
    text = text.replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "entry"


def _phrase_and_aliases(heading: str) -> (str, list[str]):
    chunks = re.findall(r"`([^`]+)`", heading)
    if not chunks:
        phrase = heading.strip()
        return phrase, [phrase]
    aliases: list[str] = []
    for chunk in chunks:
        for part in chunk.split(" / "):
            part = part.strip()
            if part and part not in aliases:
                aliases.append(part)
    return aliases[0], aliases


def parse_lexicon(markdown: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    anchor_counts: dict[str, int] = {}
    id_counts: dict[str, int] = {}
    section: str | None = None
    skipping = False
    current: ParsedEntry | None = None
    body_lines: list[str] = []

    def close() -> None:
        nonlocal current, body_lines
        if current is not None:
            body = "\n".join(body_lines).strip()
            body = re.sub(r"\n-{3,}\s*$", "", body).strip()
            current.body = body
            syn = SYNONYMS_RE.search(body)
            if syn:
                current.synonyms = parse_synonyms(syn.group(1))
            m = _DEFINITION_RE.search(body)
            if m:
                current.definition = m.group(1).strip()
            else:
                first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
                current.definition = first
            entries.append(current)
        current, body_lines = None, []

    for line in markdown.splitlines():
        heading = _ANY_HEADING_RE.match(line)
        if heading:
            base = github_anchor(heading.group(2).replace("`", ""))
            seen = anchor_counts.get(base, 0)
            anchor_counts[base] = seen + 1
            anchor = base if seen == 0 else f"{base}-{seen}"

            level = len(heading.group(1))
            if level <= 2:
                close()
                sec = _SECTION_RE.match(line)
                section = sec.group(2).strip() if sec else None
                skipping = section is None or any(s in section.lower() for s in SKIP_SECTIONS)
                continue
            entry = _ENTRY_RE.match(line) if level == 3 else None
            if entry:
                close()
                if skipping or section is None:
                    continue
                number, rest = entry.group(1), entry.group(2)
                phrase, aliases = _phrase_and_aliases(rest)
                slug = slugify(phrase)
                n = id_counts.get(slug, 0) + 1
                id_counts[slug] = n
                current = ParsedEntry(
                    id=slug if n == 1 else f"{slug}-{n}",
                    number=number,
                    section=section,
                    phrase=phrase,
                    aliases=aliases,
                    body="",
                    anchor=anchor,
                )
                continue
        if current is not None:
            body_lines.append(line)
    close()
    return entries
