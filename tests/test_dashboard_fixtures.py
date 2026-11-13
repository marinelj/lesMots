"""The demo's totals reconcile across the dashboard API fixtures."""

import json
from collections import Counter
from pathlib import Path


def test_dashboard_fixtures_reconcile():
    root = Path(__file__).parent / "fixtures" / "dashboard"

    def load(name):
        return json.loads((root / f"{name}.json").read_text())

    summary = load("summary")
    entries = load("entries")
    timeline = load("timeline")
    surfaces = load("surfaces")
    histories = []
    assert summary["entries"] == len(entries)
    assert summary["entries_exposed"] == sum(e["exposures"] > 0 for e in entries)
    assert len(timeline) == 30
    assert len({e["id"] for e in entries}) == len(entries)
    for entry in entries:
        detail = load(f"entry-{entry['id']}")
        assert detail["entry"] == entry
        exposures = detail["exposures"]
        assert entry["exposures"] == len(exposures)
        assert entry["via_synonyms"] == sum(bool(e["via"]) for e in exposures)
        assert entry["last_seen"] == max((e["at"] for e in exposures), default=None)
        histories.extend(exposures)
    assert summary["exposures_total"] == len(histories)
    assert summary["exposures_total"] == sum(day["count"] for day in timeline)
    assert Counter(e["at"][:10] for e in histories) == Counter(
        {day["date"]: day["count"] for day in timeline}
    )
    assert Counter(e["surface"] for e in histories) == Counter(
        {surface["surface"]: surface["count"] for surface in surfaces}
    )
    for section in summary["by_section"]:
        members = [e for e in entries if e["section"] == section["section"]]
        assert section["entries"] == len(members)
        assert section["exposed"] == sum(e["exposures"] > 0 for e in members)
