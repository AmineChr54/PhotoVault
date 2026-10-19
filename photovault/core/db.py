"""SQLite storage. Every worker thread opens its own connection via connect()."""
import os
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    root TEXT NOT NULL,
    rel TEXT NOT NULL,
    top TEXT NOT NULL,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    hash TEXT,
    dhash TEXT,
    date_taken TEXT,
    date_src TEXT,
    camera TEXT,
    width INTEGER,
    height INTEGER,
    health TEXT,
    seen INTEGER
);
CREATE INDEX IF NOT EXISTS ix_files_size ON files(size);
CREATE INDEX IF NOT EXISTS ix_files_hash ON files(hash);
CREATE INDEX IF NOT EXISTS ix_files_top ON files(top);
CREATE INDEX IF NOT EXISTS ix_files_date ON files(date_taken);

CREATE TABLE IF NOT EXISTS ops (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    undoable INTEGER NOT NULL DEFAULT 0,
    undone INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY,
    op_id INTEGER NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    size INTEGER,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_moves_op ON moves(op_id);

CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY,
    qpath TEXT UNIQUE NOT NULL,
    original TEXT NOT NULL,
    kept TEXT,
    reason TEXT NOT NULL,
    size INTEGER,
    ts REAL NOT NULL,
    op_id INTEGER,
    state TEXT NOT NULL DEFAULT 'in'
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY,
    ts_start REAL NOT NULL,
    ts_end REAL,
    target TEXT NOT NULL,
    copied INTEGER DEFAULT 0,
    updated INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    bytes INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    status TEXT
);

CREATE TABLE IF NOT EXISTS ignored_groups (key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path=None) -> sqlite3.Connection:
    path = str(path or config.DB_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def split_path(path: str, roots: list):
    """Return (root, rel, top) for an absolute path, using the longest matching root."""
    norm = os.path.normcase(os.path.abspath(path))
    best = None
    for r in roots:
        rn = os.path.normcase(os.path.abspath(r))
        if norm.startswith(rn + os.sep) and (best is None or len(rn) > len(os.path.normcase(best))):
            best = r
    if best is None:
        best = os.path.dirname(path)
    rel = os.path.relpath(path, best)
    parts = rel.split(os.sep)
    top = parts[0] if len(parts) > 1 else "(root files)"
    return best, rel, top


def update_path(conn, old: str, new: str, roots: list) -> None:
    root, rel, top = split_path(new, roots)
    conn.execute("DELETE FROM files WHERE path=?", (new,))
    conn.execute(
        "UPDATE files SET path=?, root=?, rel=?, top=?, name=? WHERE path=?",
        (new, root, rel, top, os.path.basename(new), old),
    )


def new_op(conn, kind: str, summary: str, undoable: bool) -> int:
    cur = conn.execute(
        "INSERT INTO ops(ts, kind, summary, undoable) VALUES (?,?,?,?)",
        (time.time(), kind, summary, int(undoable)),
    )
    return cur.lastrowid


def set_op_summary(conn, op_id: int, summary: str) -> None:
    conn.execute("UPDATE ops SET summary=? WHERE id=?", (summary, op_id))


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, str(value)))
