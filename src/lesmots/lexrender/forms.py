"""Word-form matching: find lexicon phrases in text whatever form their head word takes.

An entry stores its phrase in base form ("fall back to"). The matcher also accepts
"falls back to", "fell back to", "fallen back to" and "falling back to": every inflected
form links to the same entry and counts as one exposure.

Only the first word of a phrase is inflected. That covers phrasal verbs ("take over"),
verb + adverb ("degrade gracefully") and verb idioms ("hit the nail on the head").
Regular forms come from spelling rules; irregular ones from the table below.

Placeholders such as ``[X]``, ``someone``, ``something``, ``sth`` and ``...`` match one to
four words in the middle of a phrase and are dropped at either end.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# base -> (third person, past, past participle, -ing). Only forms that the spelling rules
# below would get wrong need to be here.
IRREGULAR: dict[str, tuple[str, ...]] = {
    "be": ("is", "am", "are", "was", "were", "been", "being"),
    "have": ("has", "had", "having"),
    "do": ("does", "did", "done", "doing"),
    "go": ("goes", "went", "gone", "going"),
    "bet": ("bets", "bet", "betting"),
    "break": ("breaks", "broke", "broken", "breaking"),
    "bring": ("brings", "brought", "bringing"),
    "build": ("builds", "built", "building"),
    "catch": ("catches", "caught", "catching"),
    "choose": ("chooses", "chose", "chosen", "choosing"),
    "come": ("comes", "came", "coming"),
    "cut": ("cuts", "cut", "cutting"),
    "draw": ("draws", "drew", "drawn", "drawing"),
    "drive": ("drives", "drove", "driven", "driving"),
    "fall": ("falls", "fell", "fallen", "falling"),
    "feel": ("feels", "felt", "feeling"),
    "find": ("finds", "found", "finding"),
    "get": ("gets", "got", "gotten", "getting"),
    "give": ("gives", "gave", "given", "giving"),
    "grow": ("grows", "grew", "grown", "growing"),
    "hang": ("hangs", "hung", "hanging"),
    "hear": ("hears", "heard", "hearing"),
    "hit": ("hits", "hit", "hitting"),
    "hold": ("holds", "held", "holding"),
    "keep": ("keeps", "kept", "keeping"),
    "know": ("knows", "knew", "known", "knowing"),
    "lay": ("lays", "laid", "laying"),
    "lead": ("leads", "led", "leading"),
    "leave": ("leaves", "left", "leaving"),
    "let": ("lets", "let", "letting"),
    "lose": ("loses", "lost", "losing"),
    "make": ("makes", "made", "making"),
    "mean": ("means", "meant", "meaning"),
    "meet": ("meets", "met", "meeting"),
    "pay": ("pays", "paid", "paying"),
    "put": ("puts", "put", "putting"),
    "read": ("reads", "read", "reading"),
    "run": ("runs", "ran", "running"),
    "say": ("says", "said", "saying"),
    "see": ("sees", "saw", "seen", "seeing"),
    "sell": ("sells", "sold", "selling"),
    "send": ("sends", "sent", "sending"),
    "set": ("sets", "set", "setting"),
    "shake": ("shakes", "shook", "shaken", "shaking"),
    "show": ("shows", "showed", "shown", "showing"),
    "shut": ("shuts", "shut", "shutting"),
    "sit": ("sits", "sat", "sitting"),
    "speak": ("speaks", "spoke", "spoken", "speaking"),
    "spend": ("spends", "spent", "spending"),
    "spread": ("spreads", "spread", "spreading"),
    "stand": ("stands", "stood", "standing"),
    "stick": ("sticks", "stuck", "sticking"),
    "strike": ("strikes", "struck", "striking"),
    "take": ("takes", "took", "taken", "taking"),
    "teach": ("teaches", "taught", "teaching"),
    "tell": ("tells", "told", "telling"),
    "think": ("thinks", "thought", "thinking"),
    "throw": ("throws", "threw", "thrown", "throwing"),
    "understand": ("understands", "understood", "understanding"),
    "undertake": ("undertakes", "undertook", "undertaken", "undertaking"),
    "win": ("wins", "won", "winning"),
    "write": ("writes", "wrote", "written", "writing"),
}

PLACEHOLDER_WORDS = {"someone", "something", "somebody", "sth", "sb", "...", "…"}
_PLACEHOLDER_RE = re.compile(r"^\[[^\]]*\]$")
_VOWELS = set("aeiou")


def normalize(text: str) -> str:
    """Lower-case and straighten quotes so 'Can’t' and "can't" compare equal."""
    return text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').lower()


def is_placeholder(token: str) -> bool:
    return token in PLACEHOLDER_WORDS or bool(_PLACEHOLDER_RE.match(token))


def inflections(word: str) -> set[str]:
    """All forms of ``word`` the matcher accepts, the base included."""
    word = normalize(word)
    if word in IRREGULAR:
        return {word, *IRREGULAR[word]}
    if not word.isalpha() or len(word) < 2:
        return {word}
    forms = {word}
    # third person / plural
    if word.endswith(("s", "x", "z", "ch", "sh", "o")):
        forms.add(word + "es")
    elif word.endswith("y") and word[-2] not in _VOWELS:
        forms.add(word[:-1] + "ies")
    else:
        forms.add(word + "s")
    # past and -ing
    if word.endswith("ie"):
        forms |= {word + "d", word[:-2] + "ying"}
    elif word.endswith("ee"):
        forms |= {word + "d", word + "ing"}
    elif word.endswith("e"):
        forms |= {word + "d", word[:-1] + "ing"}
    elif word.endswith("y") and word[-2] not in _VOWELS:
        forms |= {word[:-1] + "ied", word + "ing"}
    elif _doubles_final_consonant(word):
        forms |= {word + word[-1] + "ed", word + word[-1] + "ing"}
    else:
        forms |= {word + "ed", word + "ing"}
    return forms


def _doubles_final_consonant(word: str) -> bool:
    """Short consonant-vowel-consonant words double the last letter: stop -> stopped."""
    if len(word) > 4 or len(word) < 3 or word[-1] in _VOWELS or word[-1] in "wxy":
        return False
    return word[-2] in _VOWELS and word[-3] not in _VOWELS


def _tokens(phrase: str) -> list[str]:
    text = normalize(phrase).strip().strip(".,;:!?\"")
    return [t for t in re.split(r"\s+", text) if t]


@dataclass(frozen=True)
class Match:
    entry_id: str
    start: int
    end: int
    text: str


class Matcher:
    """Finds lexicon phrases in text. Build once per lexicon version, reuse per answer."""

    MAX_WORDS = 8  # longer headings (case excerpts, whole sentences) are not matched

    def __init__(self, phrases: Iterable[tuple[str, Sequence[str]]]):
        """``phrases`` = (entry_id, [phrase, alias, ...]) pairs."""
        self._patterns: list[tuple[str, re.Pattern, int]] = []
        for entry_id, aliases in phrases:
            for alias in aliases:
                built = self._compile(alias)
                if built is not None:
                    pattern, weight = built
                    self._patterns.append((entry_id, pattern, weight))
        # longest phrase first, so "fall back to" wins over "fall"
        self._patterns.sort(key=lambda p: -p[2])

    @classmethod
    def _compile(cls, phrase: str) -> tuple[re.Pattern, int] | None:
        tokens = _tokens(phrase)
        while tokens and is_placeholder(tokens[0]):
            tokens.pop(0)
        while tokens and is_placeholder(tokens[-1]):
            tokens.pop()
        if not tokens or len(tokens) > cls.MAX_WORDS:
            return None
        parts = []
        for i, tok in enumerate(tokens):
            if is_placeholder(tok):
                parts.append(r"(?:\S+\s+){0,3}\S+")
            elif i == 0:
                forms = sorted(inflections(tok), key=len, reverse=True)
                parts.append("(?:" + "|".join(re.escape(f) for f in forms) + ")")
            else:
                parts.append(re.escape(tok))
        body = r"\s+".join(parts)
        pattern = re.compile(r"(?<![\w'])" + body + r"(?![\w'])", re.IGNORECASE)
        weight = sum(len(t) for t in tokens if not is_placeholder(t))
        return pattern, weight

    def find(self, text: str) -> list[Match]:
        """Non-overlapping matches in reading order; the longer phrase wins an overlap."""
        haystack = normalize(text)
        taken: list[tuple[int, int]] = []
        found: list[Match] = []
        for entry_id, pattern, _ in self._patterns:
            for m in pattern.finditer(haystack):
                s, e = m.span()
                if any(s < te and ts < e for ts, te in taken):
                    continue
                taken.append((s, e))
                found.append(Match(entry_id, s, e, text[s:e]))
        found.sort(key=lambda m: m.start)
        return found

    def resolve(self, phrase: str) -> str | None:
        """The entry whose phrase, in any form, is exactly ``phrase``."""
        candidate = normalize(phrase).strip()
        for entry_id, pattern, _ in self._patterns:
            m = pattern.search(candidate)
            if m and m.start() == 0 and m.end() == len(candidate):
                return entry_id
        return None
