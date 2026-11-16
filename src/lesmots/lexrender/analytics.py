"""Exposure analytics for the dashboard (paid tier): how many entries, which ones the user
actually met, when and where.

Every function reads one account only and returns plain JSON-ready data. Times are UTC;
the timeline is grouped by UTC date.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .store import LexiconError, Store

MAX_DAYS = 366
ENTRY_SORTS = ("exposures", "recent", "phrase")


def summary(store: Store, account_id: str) -> dict[str, object]:
    entries = store.entries(account_id)
    counts = store.exposure_counts(account_id)
    exposed = {e.id for e in entries if counts.get(e.id)}
    sections: dict[str, dict[str, object]] = {}
    for e in entries:
        row = sections.setdefault(e.section, {"section": e.section, "entries": 0, "exposed": 0})
        row["entries"] += 1  # type: ignore[operator]
        row["exposed"] += e.id in exposed  # type: ignore[operator]
    return {
        "entries": len(entries),
        "version": store.version(account_id),
        "exposures_total": sum(counts.values()),
        "entries_exposed": len(exposed),
        "by_section": list(sections.values()),
    }


def entries(store: Store, account_id: str, sort: str = "exposures") -> list[dict[str, object]]:
    if sort not in ENTRY_SORTS:
        raise LexiconError(f"sort must be one of {', '.join(ENTRY_SORTS)}")
    stats = {
        r["entry_id"]: r
        for r in store._conn.execute(
            "SELECT entry_id, COUNT(*) AS n, MAX(occurred_at) AS last_seen, "
            "SUM(via != '') AS via_n FROM exposures WHERE account_id = ? GROUP BY entry_id",
            (account_id,))
    }
    rows = []
    for e in store.entries(account_id):
        s = stats.get(e.id)
        rows.append({"id": e.id, "phrase": e.phrase, "section": e.section, "link": e.link,
                     "exposures": s["n"] if s else 0,
                     "last_seen": s["last_seen"] if s else None,
                     "via_synonyms": s["via_n"] if s else 0})
    if sort == "exposures":
        rows.sort(key=lambda r: -r["exposures"])  # stable: ties keep lexicon order
    elif sort == "recent":
        rows.sort(key=lambda r: r["last_seen"] or "", reverse=True)
    else:
        rows.sort(key=lambda r: str(r["phrase"]).lower())
    return rows


def timeline(store: Store, account_id: str, days: int = 30,
             today: datetime | None = None) -> list[dict[str, object]]:
    """One row per UTC day for the last ``days`` days, oldest first, zero-filled."""
    store._account(account_id)
    days = max(1, min(int(days), MAX_DAYS))
    end = (today or datetime.now(timezone.utc)).date()
    start = end - timedelta(days=days - 1)
    counts = {
        r["day"]: r["n"]
        for r in store._conn.execute(
            "SELECT substr(occurred_at, 1, 10) AS day, COUNT(*) AS n FROM exposures "
            "WHERE account_id = ? AND substr(occurred_at, 1, 10) >= ? GROUP BY day",
            (account_id, start.isoformat()))
    }
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "count": counts.get((start + timedelta(days=i)).isoformat(), 0)}
            for i in range(days)]


def surfaces(store: Store, account_id: str) -> list[dict[str, object]]:
    store._account(account_id)
    rows = store._conn.execute(
        "SELECT surface, COUNT(*) AS n FROM exposures WHERE account_id = ? "
        "GROUP BY surface ORDER BY n DESC, surface", (account_id,))
    return [{"surface": r["surface"], "count": r["n"]} for r in rows]


def entry_detail(store: Store, account_id: str, entry_id: str,
                 limit: int = 200) -> dict[str, object] | None:
    entry = store.get(account_id, entry_id)
    if entry is None:
        return None
    rows = store._conn.execute(
        "SELECT occurred_at, surface, label, mode, via FROM exposures "
        "WHERE account_id = ? AND entry_id = ? ORDER BY occurred_at DESC LIMIT ?",
        (account_id, entry_id, max(1, min(int(limit), 1000))))
    return {"entry": entry.full(),
            "exposures": [{"at": r["occurred_at"], "surface": r["surface"], "label": r["label"],
                           "mode": r["mode"], "via": r["via"]} for r in rows]}
