"""LesMots long-term memory: word bank + config + prepared content, in one JSON file.

Location: $LESMOTS_HOME/lesmots.json (default ~/.lesmots/lesmots.json).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from lesmots.models import Word
from lesmots import srs

DEFAULT_CONFIG = {
    "language": "Chinese",
    "interests": ["LLM", "AI", "state-of-the-art tech"],
    "max_words": 120,
    "pick_limit": 10,
}


def data_path() -> Path:
    home = os.environ.get("LESMOTS_HOME")
    base = Path(home) if home else Path.home() / ".lesmots"
    return base / "lesmots.json"


class Memory:
    """The persistent state of LesMots."""

    def __init__(self, config: Optional[dict] = None, words: Optional[list[Word]] = None,
                 prepared: Optional[dict] = None, loved: Optional[list[dict]] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.words: list[Word] = words or []
        self.prepared: Optional[dict] = prepared  # {"date","original","rewritten","words_used","consumed"}
        self.loved: list[dict] = loved or []  # stories the user chose to keep
        for story in self.loved:  # entries saved before ids existed
            story.setdefault("id", uuid.uuid4().hex[:8])

    # ---------- word bank ----------

    def find(self, text: str) -> Optional[Word]:
        key = text.strip().lower()
        for w in self.words:
            if w.text.strip().lower() == key:
                return w
        return None

    def add(self, word: Word) -> Word:
        existing = self.find(word.text)
        if existing:
            return existing
        self.words.append(word)
        return word

    def remove(self, text: str) -> bool:
        w = self.find(text)
        if w:
            self.words.remove(w)
            return True
        return False

    def pick(self, limit: Optional[int] = None) -> list[Word]:
        return srs.pick_words(self.words, limit or self.config["pick_limit"])

    def record_exposure(self, texts: list[str]) -> list[Word]:
        """Called when prepared content is shown: bump exposure count and
        familiarity (SM-2 'good' review) for every bank word used in it."""
        updated = []
        for t in texts:
            w = self.find(t)
            if w:
                w.exposure_count += 1
                srs.review(w, srs.GOOD)
                updated.append(w)
        return updated

    def love_current(self) -> dict:
        """Persist the current story into the loved list (deduped on text)."""
        if not self.prepared:
            raise ValueError("No story to save — start a New Journey first.")
        for story in self.loved:
            if story.get("rewritten") == self.prepared.get("rewritten"):
                return story
        story = {k: v for k, v in self.prepared.items() if k != "consumed"}
        story["id"] = uuid.uuid4().hex[:8]
        story["loved_at"] = date.today().isoformat()
        self.loved.append(story)
        return story

    def unlove(self, story_id: str) -> bool:
        """Remove a story from the loved list by id."""
        for story in self.loved:
            if story.get("id") == story_id:
                self.loved.remove(story)
                return True
        return False

    def set_familiarity(self, text: str, level: int) -> Optional[Word]:
        """Manually set a bank word's familiarity (1-5); returns None if unknown."""
        w = self.find(text)
        if w:
            w.set_familiarity(level)
        return w

    # ---------- persistence ----------

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or data_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "config": self.config,
            "words": [w.to_dict() for w in self.words],
            "prepared": self.prepared,
            "loved": self.loved,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Memory":
        path = path or data_path()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            config=data.get("config"),
            words=[Word.from_dict(w) for w in data.get("words", [])],
            prepared=data.get("prepared"),
            loved=data.get("loved"),
        )
