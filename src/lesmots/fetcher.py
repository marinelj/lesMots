"""Fetch popular internet content matching the user's interests.

Primary source: Google News RSS — covers every topic (Sports, History,
Politics, ...), needs no API key, and `when:7d` keeps stories fresh.
Fallback: the free Hacker News (Algolia) search API, which only really
covers tech and gets stale for other topics.
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Optional

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
HN_SEARCH = "https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage=10"
FRESH_DAYS = 7
TOP_PICKS = 5  # choose randomly among the best matches so journeys vary
_HEADERS = {"User-Agent": "Mozilla/5.0 (lesmots)"}


# ---------- Google News (primary) ----------

def _google_news_url(topic: str) -> str:
    return GOOGLE_NEWS_RSS.format(q=urllib.parse.quote(f"{topic} when:{FRESH_DAYS}d"))


def _parse_google_rss(xml_bytes: bytes) -> list[dict]:
    stories = []
    for item in ET.fromstring(xml_bytes).findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        if " - " in title:  # Google appends " - Outlet" to headlines
            title = title.rsplit(" - ", 1)[0].rstrip()
        published = ""
        try:
            pub = item.findtext("pubDate")
            if pub:
                published = parsedate_to_datetime(pub).date().isoformat()
        except (ValueError, TypeError):
            pass
        stories.append({
            "title": title,
            "url": (item.findtext("link") or "").strip(),
            "points": 0,
            "source": (item.findtext("source") or "").strip() or "Google News",
            "published": published,
            "text": title,  # search-RSS descriptions are just link markup
        })
    return stories


def _fetch_google_news(topic: str) -> Optional[dict]:
    req = urllib.request.Request(_google_news_url(topic), headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        stories = _parse_google_rss(resp.read())
    return random.choice(stories[:TOP_PICKS]) if stories else None


# ---------- Hacker News (fallback) ----------

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


def _fetch_hn(topic: str) -> dict:
    hits = _search(topic, FRESH_DAYS) or _search(topic)
    if not hits:
        raise RuntimeError(f"No stories found for topic: {topic}")
    hit = max(hits, key=lambda h: h.get("points") or 0)
    return {
        "title": hit["title"],
        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        "points": hit.get("points") or 0,
        "source": "Hacker News",
        "published": (hit.get("created_at") or "")[:10],
        "text": hit.get("story_text") or hit["title"],
    }


# ---------- public API ----------

def fetch_popular(interests: list[str]) -> dict:
    """Return one fresh story: {"title", "url", "points", "source", "published", "text"}."""
    topic = random.choice(interests)
    try:
        story = _fetch_google_news(topic)
        if story:
            return story
    except Exception:
        pass  # Google News unreachable/format change — fall back to HN
    return _fetch_hn(topic)
