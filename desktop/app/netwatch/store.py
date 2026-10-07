"""Local SQLite store. Everything stays on this machine.

Tag private keys and the Apple session blob are sealed with `secretbox` before
they touch disk, so a stolen netwatch.db alone is not enough to locate someone's
tags or use their Apple session.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import secretbox
from .config import DB_PATH, ensure_dirs

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS apple_session (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    email      TEXT,
    sealed     BLOB,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    icon            TEXT NOT NULL DEFAULT 'tag',
    color           TEXT NOT NULL DEFAULT '#2dd4bf',
    sealed_priv     BLOB NOT NULL,
    adv_key         TEXT NOT NULL UNIQUE,
    hashed_adv_key  TEXT NOT NULL UNIQUE,
    printed_mac     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    auto_sync       INTEGER NOT NULL DEFAULT 0,
    show_track      INTEGER NOT NULL DEFAULT 1,
    visible         INTEGER NOT NULL DEFAULT 1,
    notes           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id              INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    timestamp           TEXT NOT NULL,
    latitude            REAL NOT NULL,
    longitude           REAL NOT NULL,
    confidence          INTEGER,
    horizontal_accuracy REAL,
    status              INTEGER,
    UNIQUE (tag_id, timestamp, latitude, longitude)
);

CREATE INDEX IF NOT EXISTS idx_reports_tag_time ON reports (tag_id, timestamp DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    with _lock:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()


# Columns added after the first release. Existing databases are upgraded in
# place rather than recreated — a tag's private key is unrecoverable, so the
# store must never be rebuilt from scratch.
_MIGRATIONS = {
    "tags": {
        "color": "TEXT NOT NULL DEFAULT '#2dd4bf'",
        "show_track": "INTEGER NOT NULL DEFAULT 1",
        # Hide a tag from the map without deleting it. Default on, so upgrading
        # never makes an existing tag vanish.
        "visible": "INTEGER NOT NULL DEFAULT 1",
    },
}


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        for table, cols in _MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: Any = None) -> Any:
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def set_secret(key: str, value: str) -> None:
    """Store a small secret (e.g. a map-provider API key) sealed at rest.

    A leaked tile key is billable, so it does not sit in the database in clear.
    """
    if not value:
        with conn() as c:
            c.execute("DELETE FROM settings WHERE key = ?", (f"secret:{key}",))
        return
    blob = base64.b64encode(secretbox.seal(value.encode())).decode()
    set_setting(f"secret:{key}", blob)


def get_secret(key: str, default: str = "") -> str:
    blob = get_setting(f"secret:{key}")
    if not blob:
        return default
    try:
        return secretbox.open_sealed(base64.b64decode(blob)).decode()
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Apple session (tokens only — never the password)
# --------------------------------------------------------------------------- #
def save_apple_session(email: str, state: dict) -> None:
    sealed = secretbox.seal(json.dumps(state).encode())
    with conn() as c:
        c.execute(
            "INSERT INTO apple_session (id, email, sealed, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET email=excluded.email, sealed=excluded.sealed, "
            "updated_at=excluded.updated_at",
            (email, sealed, _now()),
        )


def load_apple_session() -> tuple[str, dict] | None:
    with conn() as c:
        row = c.execute("SELECT email, sealed FROM apple_session WHERE id = 1").fetchone()
    if not row or not row["sealed"]:
        return None
    try:
        return row["email"], json.loads(secretbox.open_sealed(row["sealed"]).decode())
    except Exception:
        # Sealed under a different user/machine, or corrupted — treat as logged out.
        return None


def clear_apple_session() -> None:
    with conn() as c:
        c.execute("DELETE FROM apple_session WHERE id = 1")


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #
_PALETTE = ["#2dd4bf", "#60a5fa", "#f472b6", "#fbbf24",
            "#a78bfa", "#4ade80", "#fb923c", "#f87171"]


def next_color() -> str:
    """Pick a colour that is not already in use, so tags stay distinguishable."""
    used = {t.get("color") for t in list_tags()}
    for c in _PALETTE:
        if c not in used:
            return c
    return _PALETTE[len(used) % len(_PALETTE)]


def add_tag(
    *, name: str, icon: str, private_key_b64: str, adv_key_b64: str,
    hashed_adv_key_b64: str, printed_mac: str, notes: str = "",
    color: str | None = None,
) -> int:
    sealed = secretbox.seal(base64.b64decode(private_key_b64))
    colour = color or next_color()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO tags (name, icon, color, sealed_priv, adv_key, hashed_adv_key, "
            "printed_mac, created_at, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, icon, colour, sealed, adv_key_b64, hashed_adv_key_b64,
             printed_mac, _now(), notes),
        )
        return int(cur.lastrowid)


def _row_to_tag(row: sqlite3.Row, *, with_private: bool = False) -> dict:
    keys = row.keys()
    tag = {
        "id": row["id"],
        "name": row["name"],
        "icon": row["icon"],
        "color": (row["color"] if "color" in keys else None) or "#2dd4bf",
        "show_track": bool(row["show_track"]) if "show_track" in keys else True,
        "visible": bool(row["visible"]) if "visible" in keys else True,
        "advertisementKey": row["adv_key"],
        "hashedAdvKey": row["hashed_adv_key"],
        "printedMac": row["printed_mac"],
        "created_at": row["created_at"],
        "auto_sync": bool(row["auto_sync"]),
        "notes": row["notes"],
    }
    if with_private:
        tag["privateKey"] = base64.b64encode(secretbox.open_sealed(row["sealed_priv"])).decode()
    return tag


def list_tags(*, with_private: bool = False) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE").fetchall()
    return [_row_to_tag(r, with_private=with_private) for r in rows]


def get_tag(tag_id: int, *, with_private: bool = False) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
    return _row_to_tag(row, with_private=with_private) if row else None


def tag_exists(hashed_adv_key_b64: str) -> bool:
    with conn() as c:
        return c.execute(
            "SELECT 1 FROM tags WHERE hashed_adv_key = ?", (hashed_adv_key_b64,)
        ).fetchone() is not None


def update_tag(tag_id: int, **fields: Any) -> None:
    allowed = {"name", "icon", "color", "auto_sync", "show_track", "visible", "notes"}
    bools = ("auto_sync", "show_track", "visible")
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(int(bool(v)) if k in bools else v)
    if not sets:
        return
    vals.append(tag_id)
    with conn() as c:
        c.execute(f"UPDATE tags SET {', '.join(sets)} WHERE id = ?", vals)


def delete_tag(tag_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


# --------------------------------------------------------------------------- #
# Location reports
# --------------------------------------------------------------------------- #
def add_reports(tag_id: int, reports: list[dict]) -> int:
    """Insert reports, ignoring duplicates. Returns how many were new."""
    if not reports:
        return 0
    with conn() as c:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO reports (tag_id, timestamp, latitude, longitude, "
            "confidence, horizontal_accuracy, status) VALUES (?,?,?,?,?,?,?)",
            [
                (tag_id, r["timestamp"], r["latitude"], r["longitude"],
                 r.get("confidence"), r.get("horizontal_accuracy"), r.get("status"))
                for r in reports
            ],
        )
        return c.total_changes - before


def get_reports(tag_id: int, limit: int = 1000,
                since: str | None = None, until: str | None = None) -> list[dict]:
    """Reports for a tag, newest first, optionally limited to a date window.

    Timestamps are stored as ISO-8601 UTC, which sorts and compares correctly as
    text, so the window can be applied in SQL rather than in Python.
    """
    sql = ("SELECT timestamp, latitude, longitude, confidence, horizontal_accuracy, status "
           "FROM reports WHERE tag_id = ?")
    args: list = [tag_id]
    if since:
        sql += " AND timestamp >= ?"
        args.append(since)
    if until:
        sql += " AND timestamp <= ?"
        args.append(until)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def report_range(tag_id: int) -> dict:
    """Oldest and newest report timestamps, for defaulting the date pickers."""
    with conn() as c:
        row = c.execute(
            "SELECT MIN(timestamp) AS first, MAX(timestamp) AS last, COUNT(*) AS n "
            "FROM reports WHERE tag_id = ?", (tag_id,)
        ).fetchone()
    return {"first": row["first"], "last": row["last"], "count": int(row["n"] or 0)}


def latest_report(tag_id: int) -> dict | None:
    rows = get_reports(tag_id, limit=1)
    return rows[0] if rows else None


def report_count(tag_id: int) -> int:
    with conn() as c:
        return int(c.execute(
            "SELECT COUNT(*) AS n FROM reports WHERE tag_id = ?", (tag_id,)
        ).fetchone()["n"])
