"""Remember which postings have already been reported.

A daily digest is only useful if it contains what is *new*. Boards re-serve the same
openings for weeks, so without a memory the second run repeats the first and the habit
of reading it dies.

SQLite rather than a JSON file because the digest runs unattended: a crash midway
through a JSON rewrite loses the whole history, while SQLite either commits the
transaction or does not.

Postings are keyed by ``sha256(source + company + url)``. Board-native ids are not
used: Greenhouse serves integers, Lever serves UUIDs, and a posting reachable from two
boards would otherwise be reported twice.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".resume-radar" / "seen.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    id          TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    source      TEXT NOT NULL,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    fit         REAL NOT NULL DEFAULT 0,
    blockers    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS seen_first_seen ON seen (first_seen);
"""


def posting_id(source: str, company: str, url: str) -> str:
    """Stable id for one posting, independent of the board's own id scheme."""
    raw = f"{source}\x1f{company}\x1f{url}".encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass
class SeenRow:
    id: str
    first_seen: str
    source: str
    company: str
    title: str
    url: str
    fit: float
    blockers: str


class Watchlist:
    """A record of every posting already reported, backed by SQLite."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript(SCHEMA)

    # -------------------------------------------------------------- queries
    def known_ids(self) -> set[str]:
        with closing(self._connection.execute("SELECT id FROM seen")) as cursor:
            return {row["id"] for row in cursor}

    def filter_new(self, postings: list) -> list:
        """Return only the postings not already recorded."""
        known = self.known_ids()
        fresh, batch_ids = [], set()
        for posting in postings:
            key = posting_id(posting.source, posting.company, posting.url)
            # A board can list the same url twice; do not report it twice either.
            if key in known or key in batch_ids:
                continue
            batch_ids.add(key)
            fresh.append(posting)
        return fresh

    def rows(self) -> list[SeenRow]:
        with closing(self._connection.execute(
                "SELECT * FROM seen ORDER BY first_seen, company, title")) as cursor:
            return [SeenRow(**dict(row)) for row in cursor]

    def __len__(self) -> int:
        with closing(self._connection.execute("SELECT COUNT(*) AS n FROM seen")) as cursor:
            return cursor.fetchone()["n"]

    # -------------------------------------------------------------- writes
    def mark_seen(self, postings: list, *, when: datetime | None = None) -> int:
        """Record postings. Returns how many rows were newly inserted."""
        stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        before = len(self)
        with self._connection:
            self._connection.executemany(
                # INSERT OR IGNORE so re-running a digest is harmless and never
                # rewrites an earlier first_seen date.
                "INSERT OR IGNORE INTO seen "
                "(id, first_seen, source, company, title, url, fit, blockers) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        posting_id(p.source, p.company, p.url), stamp, p.source,
                        p.company, p.title, p.url, float(getattr(p, "fit", 0.0)),
                        ";".join(getattr(p, "blockers", []) or []),
                    )
                    for p in postings
                ],
            )
        return len(self) - before

    def prune(self, days: int = 90, *, now: datetime | None = None) -> int:
        """Forget postings first seen more than ``days`` ago. Returns rows removed."""
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days))
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM seen WHERE first_seen < ?",
                (cutoff.isoformat(timespec="seconds"),),
            )
        return cursor.rowcount

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Watchlist:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
