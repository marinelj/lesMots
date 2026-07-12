"""Fetch popular internet content matching the user's interests.

Uses the free Hacker News (Algolia) search API — no key needed.
Prefers fresh stories (last FRESH_DAYS days); widens the window only
when nothing fresh matches.
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from typing import Optional

HN_SEARCH = "https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage=10"
FRESH_DAYS = 7


def _url(query: str, since_days: Optional[int] = None) -> str:
    url = HN_SEARCH.format(q=urllib.parse.quote(query))
    if since_days:
        cutoff = int(time.time()) - since_days * 86400
        url += "&numericFilters=" + urllib.parse.quote(f"created_at_i>{cutoff}")
    return url


def _search(query: str, since_days: Optional[int] = None) -> list[dict]:
    with urllib.request.urlopen(_url(query, since_days), timeout=30) as resp:
        data = json.loads(resp.read())
    return [h for h in data.get("hits", []) if h.get("title")]


def fetch_popular(interests: list[str]) -> dict:
    """Return one popular story: {"title", "url", "points", "source", "text"}."""
    query = random.choice(interests)
    hits = _search(query, FRESH_DAYS)
    if not hits:  # nothing recent — fall back to all-time
        hits = _search(query)
    if not hits:
        raise RuntimeError(f"No stories found for interests: {interests}")
    hit = max(hits, key=lambda h: h.get("points") or 0)
    text = hit.get("story_text") or hit.get("title")
    return {
        "title": hit["title"],
        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        "points": hit.get("points") or 0,
        "source": "Hacker News",
        "text": text,
    }
