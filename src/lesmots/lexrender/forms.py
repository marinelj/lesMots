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

# Single everyday words are never linked, even when they are lexicon entries (Marine,
# 2026-11-17: linking "have" or "let" is noise, not learning). Exposure is for learning
# phrases, idioms and real vocabulary. Multi-word phrases that contain these words
# ("make the most of") are not affected. Add to this list rather than special-casing.
COMMON_WORDS = frozenset(["a", "an", "the", "and", "or", "but", "if", "so", "as", "at", "by", "for", "from", "in", "into", "of", "off", "on", "onto", "out", "over", "to", "up", "with", "be", "am", "is", "are", "was", "were", "been", "being", "do", "does", "did", "have", "has", "had", "will", "would", "can", "could", "shall", "should", "may", "might", "must", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your", "his", "its", "our", "their", "this", "that", "these", "those", "there", "here", "who", "what", "which", "when", "where", "why", "how", "not", "no", "yes", "ask", "have", "let", "make", "get", "go", "take", "give", "use", "high", "hit", "huge", "latest", "cover", "floor", "flat", "march", "pile", "phrase", "pretty", "several", "stage", "square", "spring", "stick", "stuck", "strip", "swap", "slice", "tray", "trail", "unique", "rough", "recipe", "refer", "reload", "imagine", "identical", "expand", "fancy", "dumb", "dump", "fan", "fence", "formula", "guidance", "narrow", "paragraph", "partial", "presence", "prior", "scratch", "screw", "sidewalk", "signature", "spreadsheet", "stem", "cure", "dare", "dent", "digest", "flesh", "forbid", "grind", "greasy", "harsh", "joint", "lens", "lever", "meter", "oval", "relay", "reject", "rewind", "ridge", "sake", "shrink", "steer", "tier", "unite", "variant", "vocal", "weave", "breast", "breath", "celebrity", "cozy", "cosy", "cottages", "creepy", "crisp", "criticism", "criticize", "debut", "foolish", "hilarious", "popcorn", "hammock", "windshield", "itinerary", "lineup", "strain"])

_AUXILIARIES = frozenset({"be", "is", "am", "are", "was", "were", "been", "being",
                          "have", "has", "had", "do", "does", "did"})

# an object pronoun the writer may add after the head verb: "seeing it firsthand"
_OBJECT_PRONOUN = r"(?:\s+(?:it|them))?"
# articles in a frame are interchangeable: "favor an ... policy" = "favor the strict policy"
_ARTICLES = frozenset({"a", "an", "the"})
_ARTICLE_RE = r"(?:an|a|the)"

# particles that may follow the object of a phrasal verb: "hand the work off to",
# "spin a cluster up". "in" and "on" are left out: "turn left on Main Street" is not
# "turn on".
SEPARABLE_PARTICLES = frozenset({"off", "out", "up", "down", "over", "away", "back"})


def normalize(text: str) -> str:
    """Lower-case and straighten quotes so 'Can’t' and "can't" compare equal."""
    return text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').lower()


def is_common(phrase: str) -> bool:
    """True when ``phrase`` is a single everyday word, which is never linked."""
    tokens = [t for t in _tokens(phrase) if not is_placeholder(t)]
    return len(tokens) == 1 and tokens[0] in COMMON_WORDS


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
    if word.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(word + "es")
    elif word.endswith("o"):
        forms |= {word + "s", word + "es"}  # scenarios, potatoes
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
        # stress-final words double too (incur -> incurring, refer -> referred); spelling
        # alone can't tell them from "visit", so accept both: a misspelling never occurs
        if _ends_consonant_vowel_consonant(word):
            forms |= {word + word[-1] + "ed", word + word[-1] + "ing"}
    return forms


def _ends_consonant_vowel_consonant(word: str) -> bool:
    return (len(word) >= 3 and word[-1] not in _VOWELS and word[-1] not in "wxy"
            and word[-2] in _VOWELS and word[-3] not in _VOWELS)


def _number_forms(word: str) -> set[str]:
    """A plural last noun also matches its singular: "edge cases" matches "edge case"."""
    if not word.isalpha() or len(word) < 4 or word in COMMON_WORDS:
        return {word}
    if word.endswith("ies"):
        return {word, word[:-3] + "y"}
    if word.endswith(("ches", "shes", "xes", "sses")):
        return {word, word[:-2]}
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return {word, word[:-1]}
    # singular stays singular: adding -s turns adjectives into nouns ("left blanks")
    return {word}


def _doubles_final_consonant(word: str) -> bool:
    """Short consonant-vowel-consonant words double the last letter: stop -> stopped."""
    if len(word) > 4 or len(word) < 3 or word[-1] in _VOWELS or word[-1] in "wxy":
        return False
    return word[-2] in _VOWELS and word[-3] not in _VOWELS


_LETTER_SLOT_RE = re.compile(r"^[A-Z]$")


def _tokens(phrase: str) -> list[str]:
    # "..." and "…" are placeholders: keep them, don't strip them as punctuation
    raw = [t if t in ("...", "…") else t.strip(".,;:!?\"")
           for t in re.split(r"\s+", phrase.strip())]
    # single capital letters are slots in frames such as "You can't tell A from B" or
    # "Map A to B": two or more of them, or X/Y/Z. A lone "A" is the article.
    letters = {t for t in raw if _LETTER_SLOT_RE.match(t)}
    slots = letters if len(letters) > 1 else letters & {"X", "Y", "Z"}
    text = " ".join("[" + t + "]" if t in slots else t for t in raw if t)
    text = normalize(text).strip().strip(".,;:!?\"")
    return [t for t in re.split(r"\s+", text) if t]


@dataclass(frozen=True)
class Match:
    entry_id: str
    start: int
    end: int
    text: str
    kind: str = "phrase"  # "phrase": the entry itself; "synonym": one of its synonyms
    via: str = ""  # for a synonym match, the synonym as stored


class Matcher:
    """Finds lexicon phrases in text. Build once per lexicon version, reuse per answer."""

    MAX_WORDS = 8  # longer headings (case excerpts, whole sentences) are not matched

    def __init__(self, phrases: Iterable[tuple[str, Sequence[str]]],
                 synonyms: Iterable[tuple[str, Sequence[str]]] = ()):
        """``phrases`` = (entry_id, [phrase, alias, ...]) pairs; ``synonyms`` =
        (entry_id, [synonym, ...]) pairs. A synonym match means the entry's own phrase
        could replace those words, if it fits the context."""
        self._patterns: list[tuple[str, re.Pattern, int, str, str]] = []
        for kind, pairs in (("phrase", phrases), ("synonym", synonyms)):
            for entry_id, words in pairs:
                for word in words:
                    built = self._compile(word)
                    if built is not None:
                        pattern, weight = built
                        self._patterns.append((entry_id, pattern, weight, kind, word))
        # longest first, so "fall back to" wins over "fall"; on a tie the entry's own
        # phrase wins over another entry's synonym
        self._patterns.sort(key=lambda p: (-p[2], p[3] != "phrase"))

    @classmethod
    def _compile(cls, phrase: str) -> tuple[re.Pattern, int] | None:
        tokens = _tokens(phrase)
        while tokens and is_placeholder(tokens[0]):
            tokens.pop(0)
        while tokens and is_placeholder(tokens[-1]):
            tokens.pop()
        if not tokens or len(tokens) > cls.MAX_WORDS:
            return None
        if len(tokens) == 1 and tokens[0] in COMMON_WORDS:
            return None  # an everyday word on its own is never linked
        # "be baked into" also matches "baked into": the copula is often left out or
        # carried by an earlier verb ("are built and baked into ...")
        optional_be = len(tokens) > 1 and tokens[0] == "be"
        if optional_be:
            tokens = tokens[1:]
        parts = []
        last = len(tokens) - 1
        for i, tok in enumerate(tokens):
            if is_placeholder(tok):
                parts.append(r"(?:\S+\s+){0,3}\S+")
                continue
            # "pull it off" also matches "pulled off" and "pulled the launch off"
            if (tok == "it" and 0 < i < last and tokens[i + 1] in SEPARABLE_PARTICLES):
                parts[-1] += r"(?:\s+[\w'-]+){0,3}?"
                continue
            if tok in _ARTICLES and 0 < i < last:
                parts.append(_ARTICLE_RE)
                continue
            # hyphens and spaces are interchangeable: "feel-the-shake" = "feel the shake"
            pieces = [p for p in tok.split("-") if p] or [tok]
            if i == 0 and not optional_be:
                forms = sorted(inflections(pieces[0]), key=len, reverse=True)
                head = "(?:" + "|".join(re.escape(f) for f in forms) + ")"
            elif i == last and len(pieces) == 1:
                forms = sorted(_number_forms(pieces[0]), key=len, reverse=True)
                head = "(?:" + "|".join(re.escape(f) for f in forms) + ")"
            else:
                head = re.escape(pieces[0])
            parts.append(r"[-\s]+".join([head] + [re.escape(p) for p in pieces[1:]]))
            separable = (len(tokens) > 1 and tokens[1] in SEPARABLE_PARTICLES
                         and not optional_be and tokens[0] not in _AUXILIARIES)
            if i == 0 and separable:
                # a separable phrasal verb: "hand the remaining work off to"
                parts[-1] += r"(?:\s+[\w'-]+){0,3}?"
            elif i == 1 and separable and i < last:
                # or the object after the particle: "hand off the file and notes to"
                parts[-1] += r"(?:\s+[\w'-]+){0,5}?"
            elif (i == 0 and not optional_be and len(tokens) > 1
                  and not is_placeholder(tokens[1]) and tokens[1] != "it"):
                parts[-1] += _OBJECT_PRONOUN
        if optional_be:
            be = "|".join(sorted(inflections("be"), key=len, reverse=True))
            parts[0] = r"(?:(?:" + be + r")\s+)?" + parts[0]
        body = r"\s+".join(parts)
        pattern = re.compile(r"(?<![\w'])" + body + r"(?![\w'])", re.IGNORECASE)
        weight = sum(len(t) for t in tokens if not is_placeholder(t))
        return pattern, weight

    def find(self, text: str) -> list[Match]:
        """Non-overlapping matches in reading order; the longer phrase wins an overlap."""
        haystack = normalize(text)
        taken: list[tuple[int, int]] = []
        found: list[Match] = []
        for entry_id, pattern, _, kind, word in self._patterns:
            for m in pattern.finditer(haystack):
                s, e = m.span()
                if any(s < te and ts < e for ts, te in taken):
                    continue
                taken.append((s, e))
                found.append(Match(entry_id, s, e, text[s:e], kind,
                                   word if kind == "synonym" else ""))
        found.sort(key=lambda m: m.start)
        return found

    def resolve(self, phrase: str) -> str | None:
        """The entry whose phrase, in any form, is exactly ``phrase``."""
        candidate = normalize(phrase).strip()
        for entry_id, pattern, _, kind, _ in self._patterns:
            if kind != "phrase":
                continue
            m = pattern.search(candidate)
            if m and m.start() == 0 and m.end() == len(candidate):
                return entry_id
        return None
