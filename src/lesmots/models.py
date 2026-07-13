"""Core data model: a Word (word / phrase / sentence) tracked in the word bank."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional

FAMILIARITY_THRESHOLDS = [(60, 5), (21, 4), (7, 3), (1, 2)]  # (interval_days, level); below -> 1
FAMILIARITY_INTERVALS = {1: 0, 2: 1, 3: 7, 4: 21, 5: 60}  # level -> SM-2 interval it snaps to


def detect_kind(text: str) -> str:
    """Classify input as word / phrase / sentence."""
    tokens = text.strip().split()
    if len(tokens) == 1:
        return "word"
    if len(tokens) <= 6 and not text.rstrip().endswith((".", "!", "?", "。", "！", "？")):
        return "phrase"
    return "sentence"


@dataclass
class Word:
    """An entry in the word bank.

    Tracks exposure count and SM-2 state; familiarity level (1-5)
    is derived from the SM-2 interval.
    """

    text: str
    explanation: str = ""
    language: str = "Chinese"
    kind: str = ""
    added: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    exposure_count: int = 0
    # SM-2 state
    ease: float = 2.5
    interval: int = 0
    due: str = field(default_factory=lambda: date.today().isoformat())
    reviews: int = 0
    lapses: int = 0

    def __post_init__(self) -> None:
        if not self.kind:
            self.kind = detect_kind(self.text)

    @property
    def familiarity(self) -> int:
        """Familiarity level 1 (completely unfamiliar) to 5 (very familiar).

        Derived from the SM-2 interval, discounted by memory decay: unlike
        raw exposure counts, familiarity sinks back down when a word goes
        unreviewed (issue #20)."""
        return self._level_for(self.effective_interval())

    def effective_interval(self, on: Optional[date] = None) -> float:
        """SM-2 interval discounted by an Ebbinghaus-style forgetting curve:
        it halves for every interval-length period the word stays overdue."""
        if self.interval <= 0:
            return 0.0
        on = on or date.today()
        overdue = (on - date.fromisoformat(self.due)).days
        if overdue <= 0:
            return float(self.interval)
        return self.interval * 0.5 ** (overdue / self.interval)

    @staticmethod
    def _level_for(interval: float) -> int:
        for threshold, level in FAMILIARITY_THRESHOLDS:
            if interval >= threshold:
                return level
        return 1

    def set_familiarity(self, level: int, on: Optional[date] = None) -> "Word":
        """One-click manual override: snap the SM-2 interval/due to the level."""
        if level not in FAMILIARITY_INTERVALS:
            raise ValueError(f"familiarity must be 1-5, got {level}")
        on = on or date.today()
        self.interval = FAMILIARITY_INTERVALS[level]
        self.due = (on + timedelta(days=self.interval)).isoformat()
        return self

    def is_due(self, on: Optional[date] = None) -> bool:
        on = on or date.today()
        return date.fromisoformat(self.due) <= on

    def to_dict(self) -> dict:
        d = asdict(self)
        d["familiarity"] = self.familiarity
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Word":
        data = {k: v for k, v in data.items() if k != "familiarity"}
        return cls(**data)
