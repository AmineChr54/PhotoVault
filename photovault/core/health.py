"""Library health: corrupt images, empty files, junk, tiny thumbnails, missing dates, empty folders."""
import os
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from . import organize

TINY_PIXELS = 160 * 160


def _noop(*a):
    pass


def check_image(path: str) -> str | None:
    """None if the image decodes fully, otherwise a short error message."""
    try:
        with Image.open(path) as img:
            w, h = img.size
            img.draft("RGB", (max(1, w // 8), max(1, h // 8)))
            img.load()
        return None
    except Exception as e:  # truncated, bad header, unsupported...
        msg = str(e) or e.__class__.__name__
        return msg[:120]


def _check_job(row):
    err = check_image(row["path"])
    return row["id"], ("ok" if err is None else "corrupt: " + err)


def scan(conn, settings, deep=True, progress=_noop, cancel=None) -> dict:
    rows = conn.execute("SELECT * FROM files").fetchall()
    res = {"corrupt": [], "zero": [], "junk": [], "tiny": [], "no_date": [], "empty_dirs": []}

    if deep:
        todo = [r for r in rows if r["kind"] == "image" and r["size"] > 0 and r["health"] is None]
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i in range(0, len(todo), 200):
                if cancel is not None and cancel.is_set():
                    conn.commit()
                    raise InterruptedError
                out = list(pool.map(_check_job, todo[i:i + 200]))
                conn.executemany("UPDATE files SET health=? WHERE id=?", [(h, fid) for fid, h in out])
                conn.commit()
                progress(min(i + 200, len(todo)), len(todo), f"Checking images {min(i + 200, len(todo)):,}/{len(todo):,}")
        rows = conn.execute("SELECT * FROM files").fetchall()

    for r in rows:
        if r["size"] == 0:
            res["zero"].append(r)
        elif organize.is_junk(r["rel"]):
            res["junk"].append(r)
        elif r["health"] and r["health"].startswith("corrupt"):
            res["corrupt"].append(r)
        elif r["kind"] == "image" and r["width"] and r["width"] * r["height"] < TINY_PIXELS:
            res["tiny"].append(r)
        if r["kind"] in ("image", "video") and r["date_src"] == "mtime":
            res["no_date"].append(r)

    progress(0, 0, "Looking for empty folders...")
    for root in settings.existing_roots():
        for dp, dns, fns in os.walk(root, topdown=False):
            if dp != root and not os.listdir(dp):
                res["empty_dirs"].append(dp)
    return res


def remove_empty_dirs(paths) -> int:
    n = 0
    for p in sorted(paths, key=len, reverse=True):
        try:
            if not os.listdir(p):
                os.rmdir(p)
                n += 1
        except OSError:
            pass
    return n


def set_manual_date(conn, ids, date_str: str) -> None:
    """date_str: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'. Stored in the library only (files are not changed)."""
    if len(date_str) == 10:
        date_str += " 12:00:00"
    conn.executemany("UPDATE files SET date_taken=?, date_src='manual' WHERE id=?", [(date_str, i) for i in ids])
    conn.commit()
