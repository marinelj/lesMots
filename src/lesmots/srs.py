"""Spaced-repetition scheduling (simplified SM-2) for word exposure.

Grades:
    0 = again  (didn't recognize; reset)
    1 = hard
    2 = good   (default when a word is exposed in generated content)
    3 = easy
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from lesmots.models import Word

AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3

MIN_EASE = 1.3
MAX_INTERVAL = 365  # days; uncapped growth overflows date arithmetic (issue #12)


def review(word: Word, grade: int, on: Optional[date] = None) -> Word:
    """Apply a review/exposure grade to a word, mutating its SM-2 state."""
    if grade not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError(f"grade must be 0-3, got {grade}")
    on = on or date.today()
    word.reviews += 1

    if grade == AGAIN:
        word.lapses += 1
        word.ease = max(MIN_EASE, word.ease - 0.2)
        word.interval = 0
        word.due = on.isoformat()
        return word

    if grade == HARD:
        word.ease = max(MIN_EASE, word.ease - 0.15)
        word.interval = max(1, int(word.interval * 1.2)) if word.interval else 1
    elif grade == GOOD:
        word.interval = max(1, int(word.interval * word.ease)) if word.interval else 1
    else:  # EASY
        word.ease += 0.15
        word.interval = max(2, int(word.interval * word.ease * 1.3)) if word.interval else 3

    word.interval = min(word.interval, MAX_INTERVAL)
    word.due = (on + timedelta(days=word.interval)).isoformat()
    return word


def pick_words(words: list[Word], limit: int = 10, on: Optional[date] = None) -> list[Word]:
    """SM-2 picking rule: words due for exposure, most overdue first.

    If fewer than `limit` are due, tops up with the least-familiar remaining words
    so generated content always has material to work with.
    """
    due = sorted((w for w in words if w.is_due(on)), key=lambda w: w.due)
    picked = due[:limit]
    if len(picked) < limit:
        rest = [w for w in words if w not in picked]
        rest.sort(key=lambda w: (w.familiarity, w.exposure_count))
        picked += rest[: limit - len(picked)]
    return picked
