"""SQLite storage for everything HuntAPI collects locally."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .sources.attributes import AttributeSnapshot
from .sources.gamelog import Match

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    key                TEXT PRIMARY KEY,
    lobby_sid          TEXT,
    mission_id         TEXT,
    game_rules         TEXT,
    level              TEXT,
    map                TEXT,
    server_ip          TEXT,
    server_port        INTEGER,
    session_token      TEXT,
    matchmaking_token  TEXT,
    region             TEXT,
    started_at         TEXT,
    ended_at           TEXT,
    duration_s         INTEGER,
    source_log         TEXT
);
CREATE INDEX IF NOT EXISTS matches_started ON matches(started_at);
CREATE INDEX IF NOT EXISTS matches_map     ON matches(map);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at     TEXT NOT NULL,
    digest       TEXT NOT NULL UNIQUE,
    file_version TEXT,
    attr_count   INTEGER,
    source_path  TEXT
);

CREATE TABLE IF NOT EXISTS snapshot_attrs (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    value       TEXT,
    PRIMARY KEY (snapshot_id, name)
) WITHOUT ROWID;
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Store:
    def __init__(self, path: str | Path = "hunt.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- matches ---------------------------------------------------------------

    def add_matches(self, matches: Iterable[Match]) -> int:
        rows = [
            (
                m.key, m.lobby_sid, m.mission_id, m.game_rules, m.level, m.map_name,
                m.server_ip, m.server_port, m.session_token, m.matchmaking_token,
                m.region, _iso(m.started_at), _iso(m.ended_at), m.duration_s, m.source_log,
            )
            for m in matches
        ]
        before = self.count_matches()
        self.db.executemany(
            "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO NOTHING",
            rows,
        )
        self.db.commit()
        return self.count_matches() - before

    def count_matches(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    def recent_matches(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM matches ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def match_summary(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT map, COUNT(*) AS matches, "
            "       CAST(AVG(duration_s) AS INTEGER) AS avg_duration_s, "
            "       SUM(duration_s) AS total_s "
            "FROM matches WHERE map IS NOT NULL GROUP BY map ORDER BY matches DESC"
        ).fetchall()

    # -- attribute snapshots ---------------------------------------------------

    def add_snapshot(self, snapshot: AttributeSnapshot) -> int | None:
        """Store a snapshot. Returns its id, or None if this exact file was already saved."""
        existing = self.db.execute(
            "SELECT id FROM snapshots WHERE digest = ?", (snapshot.digest,)
        ).fetchone()
        if existing:
            return None
        cursor = self.db.execute(
            "INSERT INTO snapshots(taken_at, digest, file_version, attr_count, source_path) "
            "VALUES (?,?,?,?,?)",
            (_utc_now(), snapshot.digest, snapshot.version, len(snapshot.raw), str(snapshot.path)),
        )
        snapshot_id = cursor.lastrowid
        self.db.executemany(
            "INSERT INTO snapshot_attrs(snapshot_id, name, value) VALUES (?,?,?)",
            [(snapshot_id, name, value) for name, value in snapshot.raw.items()],
        )
        self.db.commit()
        return snapshot_id

    def snapshots(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM snapshots ORDER BY id").fetchall()

    def snapshot_attrs(self, snapshot_id: int) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT name, value FROM snapshot_attrs WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchall()
        return {r["name"]: r["value"] for r in rows}

    def diff_snapshots(self, old_id: int, new_id: int) -> dict[str, dict[str, tuple[str, str]]]:
        old, new = self.snapshot_attrs(old_id), self.snapshot_attrs(new_id)
        return {
            "added": {k: ("", v) for k, v in new.items() if k not in old},
            "removed": {k: (v, "") for k, v in old.items() if k not in new},
            "changed": {k: (old[k], new[k]) for k in old.keys() & new.keys() if old[k] != new[k]},
        }
