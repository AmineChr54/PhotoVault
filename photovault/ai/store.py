"""ai.db: embeddings, faces, people. Keyed by content hash so moving files never loses work."""
import os
import sqlite3

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS photo (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size INTEGER, mtime REAL,
    date_taken TEXT, width INTEGER, height INTEGER,
    n_faces INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_photo_path ON photo(path);
CREATE TABLE IF NOT EXISTS seen (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, hash TEXT);
CREATE TABLE IF NOT EXISTS embedding (hash TEXT PRIMARY KEY, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS person (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS face (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL,
    x1 REAL, y1 REAL, x2 REAL, y2 REAL,
    score REAL,
    emb BLOB NOT NULL,
    cluster INTEGER,
    person_id INTEGER,
    manual INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_face_hash ON face(hash);
CREATE INDEX IF NOT EXISTS ix_face_cluster ON face(cluster);
CREATE INDEX IF NOT EXISTS ix_face_person ON face(person_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tag (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('person','event','thing')),
    parent_id INTEGER,
    UNIQUE(kind, name)
);
CREATE TABLE IF NOT EXISTS photo_tag (
    hash TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    score REAL,
    rejected INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hash, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_photo_tag_tag ON photo_tag(tag_id);
"""

# Columns added after the first release; existing ai.db files get them via ALTER TABLE.
PHOTO_NEW_COLUMNS = (("lat", "REAL"), ("lon", "REAL"), ("taken_ts", "REAL"))


def _migrate(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(photo)")}
    for name, typ in PHOTO_NEW_COLUMNS:
        if name not in have:
            try:
                conn.execute(f"ALTER TABLE photo ADD COLUMN {name} {typ}")
            except sqlite3.OperationalError:  # another process added it first
                pass
    conn.execute("CREATE INDEX IF NOT EXISTS ix_photo_taken ON photo(taken_ts)")
    conn.commit()


def connect(path=None) -> sqlite3.Connection:
    path = str(path or paths.AI_DB)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def vec_to_blob(v, dtype=None) -> bytes:
    import numpy as np
    dtype = dtype or np.float16
    return np.asarray(v, dtype=dtype).tobytes()


def blob_to_vec(b: bytes, dtype=None):
    import numpy as np
    dtype = dtype or np.float16
    return np.frombuffer(b, dtype=dtype)


def load_embeddings(conn):
    """Return (hashes, matrix float32 [n, d], L2-normalised)."""
    import numpy as np
    rows = conn.execute("SELECT hash, vec FROM embedding").fetchall()
    if not rows:
        return [], np.zeros((0, 768), np.float32)
    hashes = [r["hash"] for r in rows]
    m = np.stack([blob_to_vec(r["vec"]) for r in rows]).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return hashes, m


def stats(conn) -> dict:
    q = lambda s: conn.execute(s).fetchone()[0]
    return {
        "photos": q("SELECT COUNT(*) FROM photo WHERE status='ok'"),
        "errors": q("SELECT COUNT(*) FROM photo WHERE status<>'ok'"),
        "embedded": q("SELECT COUNT(*) FROM embedding"),
        "faces": q("SELECT COUNT(*) FROM face WHERE rejected=0"),
        "faces_named": q("SELECT COUNT(*) FROM face WHERE rejected=0 AND person_id IS NOT NULL"),
        "people": q("SELECT COUNT(*) FROM person"),
        "clusters": q("SELECT COUNT(DISTINCT cluster) FROM face WHERE cluster>=0"),
    }


TAG_KINDS = ("person", "event", "thing")


def get_or_create_tag(conn, name, kind, parent_id=None) -> int:
    name = (name or "").strip()
    if not name or kind not in TAG_KINDS:
        raise ValueError(f"bad tag: {name!r} / {kind!r}")
    row = conn.execute("SELECT id FROM tag WHERE kind=? AND name=?", (kind, name)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO tag(name,kind,parent_id) VALUES (?,?,?)", (name, kind, parent_id))
    conn.commit()
    return cur.lastrowid


def add_photo_tag(conn, hash_, tag_id, source="auto", score=None):
    """Attach a tag. A manual tag overrides auto and clears a rejection; an auto tag never
    overwrites a manual decision (including a manual rejection)."""
    row = conn.execute("SELECT source FROM photo_tag WHERE hash=? AND tag_id=?", (hash_, tag_id)).fetchone()
    if row is None:
        conn.execute("INSERT INTO photo_tag(hash,tag_id,source,score) VALUES (?,?,?,?)",
                     (hash_, tag_id, source, score))
    elif source == "manual":
        conn.execute("UPDATE photo_tag SET source='manual', score=?, rejected=0 WHERE hash=? AND tag_id=?",
                     (score, hash_, tag_id))
    elif row["source"] != "manual":
        conn.execute("UPDATE photo_tag SET score=? WHERE hash=? AND tag_id=?", (score, hash_, tag_id))
    conn.commit()


def reject_photo_tag(conn, hash_, tag_id):
    """User says this tag is wrong for this photo; kept as a manual rejection so auto-tagging won't re-add it."""
    conn.execute("INSERT INTO photo_tag(hash,tag_id,source,rejected) VALUES (?,?,'manual',1) "
                 "ON CONFLICT(hash,tag_id) DO UPDATE SET source='manual', rejected=1", (hash_, tag_id))
    conn.commit()


def tags_for_photo(conn, hash_) -> list:
    return conn.execute(
        "SELECT t.id, t.name, t.kind, pt.source, pt.score FROM photo_tag pt JOIN tag t ON t.id=pt.tag_id "
        "WHERE pt.hash=? AND pt.rejected=0 ORDER BY t.kind, t.name", (hash_,)).fetchall()


def photos_for_tag(conn, tag_id) -> list:
    return [r["hash"] for r in conn.execute(
        "SELECT hash FROM photo_tag WHERE tag_id=? AND rejected=0 ORDER BY hash", (tag_id,))]


def set_photo_geo_date(conn, hash_, lat=None, lon=None, taken_ts=None):
    conn.execute("UPDATE photo SET lat=?, lon=?, taken_ts=? WHERE hash=?", (lat, lon, taken_ts, hash_))
    conn.commit()


# ---------- tag browsing / editing helpers (numpy-free; used by the Tags page and the search index) ----------
def tag_counts(conn) -> list:
    """Every tag with its photo count and how many of those are still unreviewed auto tags."""
    return conn.execute(
        "SELECT t.id, t.name, t.kind, COUNT(pt.hash) AS n, "
        "COALESCE(SUM(pt.source='auto'),0) AS n_auto FROM tag t "
        "JOIN photo_tag pt ON pt.tag_id=t.id AND pt.rejected=0 "
        "JOIN photo p ON p.hash=pt.hash AND p.status='ok' "
        "GROUP BY t.id ORDER BY t.kind, t.name").fetchall()


def tag_photos(conn, tag_id) -> list:
    """Photos carrying a tag: hash, path, source, score (manual first, then best score)."""
    return conn.execute(
        "SELECT p.hash, p.path, pt.source, pt.score FROM photo_tag pt JOIN photo p ON p.hash=pt.hash "
        "WHERE pt.tag_id=? AND pt.rejected=0 AND p.status='ok' "
        "ORDER BY (pt.source='manual') DESC, pt.score DESC, p.path", (tag_id,)).fetchall()


def person_photos(conn, name) -> list:
    """Photos of a named person (derived from faces, not stored as photo_tag)."""
    return conn.execute(
        "SELECT DISTINCT p.hash, p.path, 'face' AS source, NULL AS score FROM face f "
        "JOIN person pe ON pe.id=f.person_id JOIN photo p ON p.hash=f.hash "
        "WHERE f.rejected=0 AND p.status='ok' AND lower(pe.name)=lower(?) ORDER BY p.path", (name,)).fetchall()


def person_tags_for_photo(conn, hash_) -> list:
    return [r["name"] for r in conn.execute(
        "SELECT DISTINCT pe.name FROM face f JOIN person pe ON pe.id=f.person_id "
        "WHERE f.hash=? AND f.rejected=0 ORDER BY pe.name", (hash_,))]


def accept_photo_tag(conn, hash_, tag_id):
    """User confirms an auto tag: it becomes manual so re-tagging never changes it."""
    conn.execute("UPDATE photo_tag SET source='manual', rejected=0 WHERE hash=? AND tag_id=?", (hash_, tag_id))
    conn.commit()


def accept_all_above(conn, tag_id, min_score) -> int:
    cur = conn.execute("UPDATE photo_tag SET source='manual' WHERE tag_id=? AND source='auto' AND rejected=0 "
                       "AND score>=?", (tag_id, float(min_score)))
    conn.commit()
    return cur.rowcount


def list_events(conn) -> list:
    """Events with photo count and first/last taken timestamp, newest first."""
    return conn.execute(
        "SELECT t.id, t.name, COUNT(p.hash) AS n, MIN(p.taken_ts) AS first_ts, MAX(p.taken_ts) AS last_ts "
        "FROM tag t JOIN photo_tag pt ON pt.tag_id=t.id AND pt.rejected=0 "
        "JOIN photo p ON p.hash=pt.hash AND p.status='ok' WHERE t.kind='event' "
        "GROUP BY t.id ORDER BY last_ts DESC, t.name").fetchall()


def tag_hash_map(conn) -> dict:
    """hash -> set of lower-cased tag names (accepted or unreviewed auto tags; rejections excluded)."""
    m = {}
    for r in conn.execute("SELECT pt.hash, t.name FROM photo_tag pt JOIN tag t ON t.id=pt.tag_id "
                          "WHERE pt.rejected=0"):
        m.setdefault(r["hash"], set()).add(r["name"].lower())
    return m


def match_tag_names(conn, text):
    """Find known tag names inside a query: 'sunset beach at night' -> (['beach'], 'sunset at night')."""
    import re
    names = sorted({r["name"] for r in conn.execute("SELECT DISTINCT name FROM tag")}, key=len, reverse=True)
    found, rest = [], text
    for nm in names:
        pat = re.compile(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", re.I)
        if pat.search(rest):
            found.append(nm)
            rest = pat.sub(" ", rest)
    return found, " ".join(rest.split())
