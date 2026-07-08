"""Fetch popular internet content matching the user's interests.

Uses the free Hacker News (Algolia) search API — no key needed.
"""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request

HN_SEARCH = "https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage=10"


def fetch_popular(interests: list[str]) -> dict:
    """Return one popular story: {"title", "url", "points", "source", "text"}."""
    query = urllib.parse.quote(random.choice(interests))
    with urllib.request.urlopen(HN_SEARCH.format(q=query), timeout=30) as resp:
        data = json.loads(resp.read())
    hits = [h for h in data.get("hits", []) if h.get("title")]
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
