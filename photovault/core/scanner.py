"""Incremental library indexer: only new or changed files are (re)read."""
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, dates, db

BATCH = 400
INDEX_VERSION = 2


def _noop(*a):
    pass


def _meta(item, roots):
    path, size, mtime = item
    ext = os.path.splitext(path)[1].lower()
    kind = config.kind_of(ext)
    when = cam = w = h = None
    try:
        if kind == "image" and size > 0:
            when, cam, w, h = dates.image_info(path)
        d, src = dates.best_date(path, kind, mtime, when)
    except Exception:  # one odd file must never stop a scan
        d, src = dates.from_mtime(mtime), "mtime"
    root, rel, top = db.split_path(path, roots)
    return (path, root, rel, top, os.path.basename(path), ext, kind, size, mtime,
            d.strftime("%Y-%m-%d %H:%M:%S"), src, cam, w, h)


def walk_roots(settings, progress=_noop, cancel=None):
    qdir = os.path.normcase(os.path.abspath(settings.quarantine_dir))
    found = []
    last = 0
    for root in settings.existing_roots():
        for dp, dns, fns in os.walk(root):
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            dns[:] = [d for d in dns if os.path.normcase(os.path.abspath(os.path.join(dp, d))) != qdir]
            for n in fns:
                if n.lower() in config.IGNORE_NAMES or n.endswith(".pvtmp"):
                    continue
                p = os.path.join(dp, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                found.append((p, st.st_size, st.st_mtime))
            if len(found) - last >= 2000:
                last = len(found)
                progress(0, 0, f"Listing files... {last:,}")
    return found


def scan(settings, progress=_noop, cancel=None, conn=None) -> dict:
    conn = conn or db.connect()
    roots = settings.existing_roots()
    t0 = time.time()
    found = walk_roots(settings, progress, cancel)
    existing = {r["path"]: (r["size"], r["mtime"]) for r in conn.execute("SELECT path, size, mtime FROM files")}

    # When the date/metadata rules change, INDEX_VERSION is bumped and every file is re-read once.
    reread_all = db.get_meta(conn, "index_version") != str(INDEX_VERSION)
    todo, same = [], []
    for item in found:
        old = existing.get(item[0])
        if not reread_all and old and old[0] == item[1] and abs(old[1] - item[2]) < 1:
            same.append(item[0])
        else:
            todo.append(item)

    stamp = conn.execute("SELECT COALESCE(MAX(seen), 0) + 1 FROM files").fetchone()[0]
    conn.executemany("UPDATE files SET seen=? WHERE path=?", ((stamp, p) for p in same))
    conn.commit()

    sql = """INSERT INTO files(path, root, rel, top, name, ext, kind, size, mtime, date_taken, date_src,
                               camera, width, height, seen, hash, dhash, health)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)
             ON CONFLICT(path) DO UPDATE SET root=excluded.root, rel=excluded.rel, top=excluded.top,
                name=excluded.name, ext=excluded.ext, kind=excluded.kind, size=excluded.size,
                mtime=excluded.mtime, date_taken=excluded.date_taken, date_src=excluded.date_src,
                camera=excluded.camera, width=excluded.width, height=excluded.height,
                seen=excluded.seen, hash=NULL, dhash=NULL, health=NULL"""
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(0, len(todo), BATCH):
            if cancel is not None and cancel.is_set():
                conn.commit()
                raise InterruptedError
            chunk = todo[i:i + BATCH]
            rows = list(pool.map(lambda it: _meta(it, roots), chunk))
            conn.executemany(sql, (r + (stamp,) for r in rows))
            conn.commit()
            done += len(chunk)
            progress(done, len(todo), f"Reading photo info {done:,}/{len(todo):,}")

    removed = conn.execute("DELETE FROM files WHERE seen IS NULL OR seen<>?", (stamp,)).rowcount
    db.set_meta(conn, "last_scan", time.time())
    db.set_meta(conn, "index_version", INDEX_VERSION)
    conn.commit()
    return {"total": len(found), "new_or_changed": len(todo), "removed": removed,
            "seconds": round(time.time() - t0, 1)}


def index_paths(paths, settings, conn) -> None:
    """Add/refresh specific files (after import or restore) without a full scan."""
    roots = settings.existing_roots()
    stamp = conn.execute("SELECT COALESCE(MAX(seen), 0) FROM files").fetchone()[0]
    items = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        items.append((p, st.st_size, st.st_mtime))
    sql = """INSERT OR REPLACE INTO files(path, root, rel, top, name, ext, kind, size, mtime, date_taken,
             date_src, camera, width, height, seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    conn.executemany(sql, (_meta(it, roots) + (stamp,) for it in items))
