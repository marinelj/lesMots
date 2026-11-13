"""The lexicon bank: private entries per account, a version counter and the exposure log.

SQLite keeps the first version dependency-free and easy to run locally. Every query is
filtered by account; no call reads across accounts. Message text is never stored: the
exposure log holds an entry id, a time and a place.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .forms import Matcher
from .parse import SYNONYMS_RE, ParsedEntry, parse_lexicon, parse_synonyms, slugify

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    link_base   TEXT NOT NULL DEFAULT '',
    render_mode TEXT NOT NULL DEFAULT 'local',
    app_uid     TEXT,
    plan        TEXT NOT NULL DEFAULT 'free',
    version     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    id              TEXT NOT NULL,
    number          TEXT NOT NULL DEFAULT '',
    section         TEXT NOT NULL DEFAULT '',
    phrase          TEXT NOT NULL,
    aliases         TEXT NOT NULL,
    definition      TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    anchor          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_version INTEGER NOT NULL,
    PRIMARY KEY (account_id, id)
);
CREATE TABLE IF NOT EXISTS exposures (
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    event_id     TEXT NOT NULL,
    entry_id     TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    surface      TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    mode         TEXT NOT NULL,
    via          TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL,
    PRIMARY KEY (account_id, event_id, entry_id)
);
CREATE INDEX IF NOT EXISTS exposures_by_entry ON exposures(account_id, entry_id, occurred_at);
"""

MODES = ("local_rewrite", "full")
RENDER_MODES = ("local", "remote")  # /local: the agent renders; /remote: the server renders
# free: local render + lexicon bank. paid: adds MCP render (remote) and the dashboard.
PLANS = ("free", "paid")

# Columns added after the first release: (table, column, definition). Applied on open.
MIGRATIONS = (
    ("accounts", "render_mode", "TEXT NOT NULL DEFAULT 'local'"),
    ("exposures", "via", "TEXT NOT NULL DEFAULT ''"),
    ("accounts", "app_uid", "TEXT"),
    ("accounts", "plan", "TEXT NOT NULL DEFAULT 'free'"),
)
APP_UID_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS accounts_app_uid ON accounts(app_uid) "
                 "WHERE app_uid IS NOT NULL")
_SYNONYM_LINE = re.compile(r"^\s*[*-]\s+\*\*Synonyms\*\*\s*:.*$", re.MULTILINE)
MAX_FIELD = 200  # surface / label length cap


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LexiconError(ValueError):
    """A request the bank refuses; the message is safe to show to the caller."""


@dataclass
class Entry:
    id: str
    number: str
    section: str
    phrase: str
    aliases: list[str]
    definition: str
    body: str
    anchor: str
    status: str
    created_at: str
    updated_at: str
    updated_version: int
    link: str = ""
    synonyms: list[str] = field(default_factory=list)

    def index_row(self) -> dict[str, object]:
        return {"id": self.id, "phrase": self.phrase, "section": self.section,
                "link": self.link, "status": self.status}

    def full(self) -> dict[str, object]:
        row = self.index_row()
        row.update(number=self.number, aliases=self.aliases, synonyms=self.synonyms,
                   synonyms_link=self.link, definition=self.definition,
                   body=self.body, created_at=self.created_at, updated_at=self.updated_at)
        return row


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self._conn.execute(APP_UID_INDEX)
        self._conn.commit()
        self._lock = threading.Lock()
        self._matchers: dict[str, tuple[int, Matcher]] = {}

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---------- accounts ----------

    def create_account(self, name: str, link_base: str = "",
                       app_uid: str | None = None) -> str:
        """Create an account and return its internal id.

        ``app_uid`` is the LesMots app user_id (a Google ``sub`` or ``wx-<openid>``) that
        signs in to this account. The internal id only keys the rows; it is never a login."""
        account_id = "acct_" + secrets.token_hex(8)
        with self._tx() as c:
            # token_hash is a legacy column: filled with an unusable value, never checked
            c.execute("INSERT INTO accounts (id, name, token_hash, link_base, app_uid, "
                      "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (account_id, name, hash_token(secrets.token_hex(32)), link_base,
                       app_uid, _now()))
        return account_id

    def account_for_app_user(self, app_uid: str, create: bool = True) -> str | None:
        """The account that this LesMots user_id signs in to. With ``create``, the first
        sign-in creates an empty account."""
        if not app_uid:
            return None
        row = self._conn.execute("SELECT id FROM accounts WHERE app_uid = ?",
                                 (app_uid,)).fetchone()
        if row:
            return row["id"]
        if not create:
            return None
        try:
            return self.create_account(app_uid, app_uid=app_uid)
        except sqlite3.IntegrityError:  # a concurrent first sign-in won the race
            return self.account_for_app_user(app_uid, create=False)

    def set_link_base(self, account_id: str, link_base: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE accounts SET link_base = ? WHERE id = ?", (link_base, account_id))

    def plan(self, account_id: str) -> str:
        return self._account(account_id)["plan"]

    def is_paid(self, account_id: str) -> bool:
        return self.plan(account_id) == "paid"

    def set_plan(self, account_id: str, plan: str) -> str:
        if plan not in PLANS:
            raise LexiconError(f"plan must be one of {', '.join(PLANS)}")
        self._account(account_id)
        with self._tx() as c:
            c.execute("UPDATE accounts SET plan = ? WHERE id = ?", (plan, account_id))
        return plan

    def render_mode(self, account_id: str) -> str:
        return self._account(account_id)["render_mode"]

    def set_render_mode(self, account_id: str, mode: str) -> str:
        if mode not in RENDER_MODES:
            raise LexiconError(f"render mode must be one of {', '.join(RENDER_MODES)}")
        self._account(account_id)
        with self._tx() as c:
            c.execute("UPDATE accounts SET render_mode = ? WHERE id = ?", (mode, account_id))
        return mode

    def _account(self, account_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise LexiconError("unknown account")
        return row

    def version(self, account_id: str) -> int:
        return int(self._account(account_id)["version"])

    # ---------- entries ----------

    def _row_to_entry(self, row: sqlite3.Row, link_base: str) -> Entry:
        entry = Entry(
            id=row["id"], number=row["number"], section=row["section"], phrase=row["phrase"],
            aliases=json.loads(row["aliases"]), definition=row["definition"], body=row["body"],
            anchor=row["anchor"], status=row["status"], created_at=row["created_at"],
            updated_at=row["updated_at"], updated_version=row["updated_version"],
        )
        syn = SYNONYMS_RE.search(entry.body)
        entry.synonyms = parse_synonyms(syn.group(1)) if syn else []
        if link_base:
            entry.link = f"{link_base}#{entry.anchor}" if entry.anchor else link_base
        return entry

    def entries(self, account_id: str, since_version: int = 0,
                include_archived: bool = False) -> list[Entry]:
        acct = self._account(account_id)
        sql = "SELECT * FROM entries WHERE account_id = ? AND updated_version > ?"
        if not include_archived:
            sql += " AND status = 'active'"
        rows = self._conn.execute(sql + " ORDER BY rowid", (account_id, since_version))
        return [self._row_to_entry(r, acct["link_base"]) for r in rows]

    def get(self, account_id: str, entry_id: str) -> Entry | None:
        acct = self._account(account_id)
        row = self._conn.execute("SELECT * FROM entries WHERE account_id = ? AND id = ?",
                                 (account_id, entry_id)).fetchone()
        return self._row_to_entry(row, acct["link_base"]) if row else None

    def matcher(self, account_id: str) -> Matcher:
        version = self.version(account_id)
        cached = self._matchers.get(account_id)
        if cached and cached[0] == version:
            return cached[1]
        entries = self.entries(account_id)
        matcher = Matcher([(e.id, e.aliases) for e in entries],
                          [(e.id, e.synonyms) for e in entries if e.synonyms])
        self._matchers[account_id] = (version, matcher)
        return matcher

    def lookup(self, account_id: str, id_or_phrase: str) -> Entry | None:
        """By id, or by the phrase in any word form ("fell back to" finds "fall back to")."""
        entry = self.get(account_id, id_or_phrase.strip())
        if entry is not None:
            return entry
        entry_id = self.matcher(account_id).resolve(id_or_phrase)
        return self.get(account_id, entry_id) if entry_id else None

    def import_markdown(self, account_id: str, markdown: str) -> dict[str, int]:
        """Load a whole lexicon file. Entries keep their ids across imports; entries missing
        from the new file are archived, not deleted, so their exposure history survives."""
        parsed = parse_lexicon(markdown)
        if not parsed:
            raise LexiconError("no entries found; expected '### N.M `phrase`' headings")
        return self._apply(account_id, parsed, archive_missing=True)

    def add_entry(self, account_id: str, phrase: str, definition: str, example: str = "",
                  section: str = "Added", synonyms: Sequence[str] = ()) -> Entry:
        phrase = phrase.strip()
        definition = definition.strip()
        if not phrase or not definition:
            raise LexiconError("phrase and definition are both required")
        existing = self.lookup(account_id, phrase)
        if existing is not None and existing.status == "active":
            raise LexiconError(f"already in the lexicon as '{existing.id}'")
        body = f"* **Definition**: {definition}"
        if example.strip():
            body += f"\n* **Example**: {example.strip()}"
        clean = parse_synonyms(", ".join(synonyms))
        if clean:
            body += f"\n* **Synonyms**: {', '.join(clean)}"
        entry_id = slugify(phrase)
        taken = {e.id for e in self.entries(account_id, include_archived=True)}
        n = 1
        while entry_id in taken and (existing is None or existing.id != entry_id):
            n += 1
            entry_id = f"{slugify(phrase)}-{n}"
        new = ParsedEntry(id=entry_id, number="", section=section, phrase=phrase,
                          aliases=[phrase], body=body, anchor="", definition=definition)
        self._apply(account_id, [new], archive_missing=False)
        return self.get(account_id, entry_id)  # type: ignore[return-value]

    def set_synonyms(self, account_id: str, id_or_phrase: str,
                     synonyms: Sequence[str]) -> Entry:
        """Replace an entry's synonyms. A synonym found in an answer can be swapped for the
        entry's own phrase when it fits the context, and is linked to this entry."""
        entry = self.lookup(account_id, id_or_phrase)
        if entry is None:
            raise LexiconError(f"no entry matches '{id_or_phrase}'")
        clean = [w for w in parse_synonyms(", ".join(synonyms))
                 if w.lower() != entry.phrase.lower()]
        body = _SYNONYM_LINE.sub("", entry.body).rstrip()
        if clean:
            body = (body + "\n" if body else "") + f"* **Synonyms**: {', '.join(clean)}"
        updated = ParsedEntry(id=entry.id, number=entry.number, section=entry.section,
                              phrase=entry.phrase, aliases=entry.aliases, body=body,
                              anchor=entry.anchor, definition=entry.definition)
        self._apply(account_id, [updated], archive_missing=False)
        return self.get(account_id, entry.id)  # type: ignore[return-value]

    def _apply(self, account_id: str, parsed: Sequence[ParsedEntry],
               archive_missing: bool) -> dict[str, int]:
        self._account(account_id)
        now = _now()
        counts = {"added": 0, "updated": 0, "unchanged": 0, "archived": 0}
        with self._tx() as c:
            version = c.execute("SELECT version FROM accounts WHERE id = ?",
                                (account_id,)).fetchone()["version"] + 1
            current = {r["id"]: r for r in c.execute(
                "SELECT * FROM entries WHERE account_id = ?", (account_id,))}
            for p in parsed:
                values = (p.number, p.section, p.phrase, json.dumps(p.aliases), p.definition,
                          p.body, p.anchor)
                old = current.get(p.id)
                if old is None:
                    c.execute("INSERT INTO entries (account_id, id, number, section, phrase, "
                              "aliases, definition, body, anchor, status, created_at, "
                              "updated_at, updated_version) VALUES "
                              "(?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                              (account_id, p.id, *values, now, now, version))
                    counts["added"] += 1
                    continue
                before = (old["number"], old["section"], old["phrase"], old["aliases"],
                          old["definition"], old["body"], old["anchor"])
                if before == values and old["status"] == "active":
                    counts["unchanged"] += 1
                    continue
                c.execute("UPDATE entries SET number = ?, section = ?, phrase = ?, aliases = ?, "
                          "definition = ?, body = ?, anchor = ?, status = 'active', "
                          "updated_at = ?, updated_version = ? WHERE account_id = ? AND id = ?",
                          (*values, now, version, account_id, p.id))
                counts["updated"] += 1
            if archive_missing:
                keep = {p.id for p in parsed}
                for entry_id, old in current.items():
                    if entry_id not in keep and old["status"] == "active":
                        c.execute("UPDATE entries SET status = 'archived', updated_at = ?, "
                                  "updated_version = ? WHERE account_id = ? AND id = ?",
                                  (now, version, account_id, entry_id))
                        counts["archived"] += 1
            if counts["added"] or counts["updated"] or counts["archived"]:
                c.execute("UPDATE accounts SET version = ? WHERE id = ?", (version, account_id))
        counts["version"] = self.version(account_id)
        return counts

    def export_markdown(self, account_id: str) -> str:
        """The lexicon in the import format, so an export can be imported again unchanged."""
        lines = ["# Lexicon", ""]
        section, n_section, n_entry = None, 0, 0
        for e in self.entries(account_id):
            if e.section != section:
                section, n_section, n_entry = e.section, n_section + 1, 0
                lines += [f"## {n_section}. {section}", ""]
            n_entry += 1
            heading = " vs. ".join(f"`{a}`" for a in e.aliases)
            lines += [f"### {n_section}.{n_entry} {heading}", e.body, ""]
        return "\n".join(lines).rstrip() + "\n"

    # ---------- exposures ----------

    def log_exposures(self, account_id: str, event_id: str, entry_ids: Iterable[str],
                      surface: str, label: str = "", occurred_at: str | None = None,
                      mode: str = "local_rewrite",
                      via: dict[str, str] | None = None) -> dict[str, object]:
        """Record that entries appeared in one rendered answer. Idempotent per
        (event_id, entry_id): a retried call cannot count twice. ``via`` maps an entry id to
        the synonym it replaced, when the entry got into the answer through a synonym."""
        via = via or {}
        self._account(account_id)
        event_id = (event_id or "").strip()
        surface = (surface or "").strip()[:MAX_FIELD]
        label = (label or "").strip()[:MAX_FIELD]
        if not event_id or len(event_id) > MAX_FIELD:
            raise LexiconError("event_id is required (at most 200 characters)")
        if not surface:
            raise LexiconError("surface is required, for example 'Claude Code'")
        if mode not in MODES:
            raise LexiconError(f"mode must be one of {', '.join(MODES)}")
        occurred = _parse_time(occurred_at) if occurred_at else _now()
        known = {e.id for e in self.entries(account_id, include_archived=True)}
        accepted, duplicate, unknown = 0, 0, []
        received = _now()
        with self._tx() as c:
            for entry_id in dict.fromkeys(entry_ids):  # once per answer
                if entry_id not in known:
                    unknown.append(entry_id)
                    continue
                cur = c.execute("INSERT OR IGNORE INTO exposures (account_id, event_id, entry_id, "
                                "occurred_at, surface, label, mode, via, received_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (account_id, event_id, entry_id, occurred, surface, label, mode,
                                 str(via.get(entry_id, ""))[:MAX_FIELD], received))
                if cur.rowcount:
                    accepted += 1
                else:
                    duplicate += 1
        return {"accepted": accepted, "duplicate": duplicate, "unknown": unknown}

    def exposure_counts(self, account_id: str) -> dict[str, int]:
        rows = self._conn.execute("SELECT entry_id, COUNT(*) AS n FROM exposures "
                                  "WHERE account_id = ? GROUP BY entry_id", (account_id,))
        return {r["entry_id"]: r["n"] for r in rows}


def _parse_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LexiconError("occurred_at must be an ISO 8601 time, e.g. 2026-11-13T15:58:28Z")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
