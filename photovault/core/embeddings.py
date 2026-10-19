"""Optional second signal for similar photos: SigLIP image embeddings from ai.db (read-only, pure Python).

ai.db is only ever opened read-only, and everything here degrades to "no signal" (None / empty) when it is
missing, unreadable or has no matching rows, so similar-photo search works the same without AI.
"""
import array
import os
import sqlite3
import struct
from operator import mul
from pathlib import Path

# Cosine similarity of SigLIP image vectors. Starting guesses, not tuned on a labelled sample (see issue #5).
SPLIT_MIN_COS = 0.80     # a dHash-close pair below this is treated as a false match and not grouped
MERGE_MIN_COS = 0.93     # a pair above this is grouped even if the dHash differs (edits, crops)
MERGE_WINDOW_S = 600     # merge candidates must have been taken within this many seconds of each other
MERGE_NEIGHBOURS = 8     # ... and be within this many positions of each other in time order


def open_ai_db(path=None):
    """Read-only connection to ai.db, or None if there is none (or it can't be read)."""
    if path is None:
        from ..ai import paths
        path = paths.AI_DB
    path = Path(path)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        conn.execute("SELECT 1 FROM photo JOIN embedding ON embedding.hash=photo.hash LIMIT 1").fetchall()
        return conn
    except sqlite3.Error:
        return None


def _unit(blob):
    """float16 blob -> L2-normalised array('f'); None if it is not a usable vector."""
    n = len(blob) // 2
    if n == 0 or len(blob) % 2:
        return None
    v = array.array("f", struct.unpack(f"<{n}e", blob))
    norm = sum(map(mul, v, v)) ** 0.5
    if not norm or norm != norm:
        return None
    return array.array("f", (x / norm for x in v))


def cosine(a, b) -> float:
    return sum(map(mul, a, b)) if len(a) == len(b) else 0.0


def vectors_for_paths(ai, paths) -> dict:
    """{normcase(path): unit vector} for the given library paths that have an embedding."""
    out = {}
    wanted = {os.path.normcase(p): p for p in paths}
    items = list(wanted.values())
    for i in range(0, len(items), 500):
        chunk = items[i:i + 500]
        q = ",".join("?" * len(chunk))
        for path, blob in ai.execute(
                f"SELECT p.path, e.vec FROM photo p JOIN embedding e ON e.hash=p.hash WHERE p.path IN ({q})", chunk):
            key = os.path.normcase(path)
            if key in wanted and key not in out:
                v = _unit(blob)
                if v is not None:
                    out[key] = v
    return out


def merge_pairs(ai, eligible: dict, cancel=None):
    """Yield (path_a, path_b) of photos taken close together whose embeddings are near-identical.

    eligible maps normcase(path) -> original path. Streams ai.db in time order so memory stays small."""
    window = []   # last few (normcase path, taken_ts, vector)
    cur = ai.execute("SELECT p.path, p.taken_ts, e.vec FROM photo p JOIN embedding e ON e.hash=p.hash "
                     "WHERE p.taken_ts IS NOT NULL ORDER BY p.taken_ts, p.path")
    for n, (path, ts, blob) in enumerate(cur):
        key = os.path.normcase(path)
        if key not in eligible:
            continue
        if cancel is not None and n % 1000 == 0 and cancel.is_set():
            raise InterruptedError
        v = _unit(blob)
        if v is None:
            continue
        for okey, ots, ov in window:
            if ts - ots <= MERGE_WINDOW_S and okey != key and cosine(v, ov) >= MERGE_MIN_COS:
                yield eligible[okey], eligible[key]
        window.append((key, ts, v))
        del window[:-MERGE_NEIGHBOURS]
