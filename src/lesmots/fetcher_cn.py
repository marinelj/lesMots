"""Domestic (China mainland) news sources for the WeChat edition (M2).

Google News and Hacker News are unreachable from the mainland, so this
module offers the same fetch_popular() contract backed by domestic hot
lists: 百度热搜 first, 微博热搜 as fallback. Select it by setting
LESMOTS_NEWS_SOURCE=cn.

These endpoints are public but unofficial — parsers are defensive and
the sources sit behind one interface so more can be added when one
churns.
"""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from datetime import date

BAIDU_HOT_URL = "https://top.baidu.com/api/board?platform=wap&tab=realtime"
WEIBO_HOT_URL = "https://weibo.com/ajax/side/hotSearch"
_HEADERS = {"User-Agent": "Mozilla/5.0 (lesmots)"}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _parse_baidu(data: dict) -> list[dict]:
    stories = []
    for card in (data.get("data") or {}).get("cards", []):
        for item in card.get("content", []):
            title = (item.get("word") or "").strip()
            if not title:
                continue
            stories.append({
                "title": title,
                "url": item.get("url") or item.get("rawUrl") or "",
                "points": int(item.get("hotScore") or 0),
                "source": "百度热搜",
                "published": date.today().isoformat(),
                "text": (item.get("desc") or "").strip() or title,
            })
    return stories


def _parse_weibo(data: dict) -> list[dict]:
    stories = []
    for item in (data.get("data") or {}).get("realtime", []):
        title = (item.get("word") or "").strip()
        if not title:
            continue
        stories.append({
            "title": title,
            "url": "https://s.weibo.com/weibo?q=" + urllib.parse.quote(title),
            "points": int(item.get("num") or 0),
            "source": "微博热搜",
            "published": date.today().isoformat(),
            "text": (item.get("note") or "").strip() or title,
        })
    return stories


_SOURCES = [(BAIDU_HOT_URL, _parse_baidu), (WEIBO_HOT_URL, _parse_weibo)]


def fetch_popular_cn(interests: list[str]) -> dict:
    """Same contract as fetcher.fetch_popular, from domestic hot lists.

    Hot lists aren't topic-searchable, so prefer entries whose title
    mentions an interest; otherwise pick from the hottest ten.
    """
    for url, parse in _SOURCES:
        try:
            stories = parse(_get_json(url))
        except Exception:
            continue  # source down or format changed — try the next one
        if not stories:
            continue
        matched = [s for s in stories
                   if any(i.lower() in s["title"].lower() for i in interests)]
        pool = matched or sorted(stories, key=lambda s: -s["points"])[:10]
        return random.choice(pool)
    raise RuntimeError("No domestic stories available (百度/微博 unreachable)")
