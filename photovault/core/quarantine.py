"""Quarantine: files are moved here instead of being deleted. Restore or empty (to Recycle Bin) later."""
import csv
import os
import time

from send2trash import send2trash

from . import db, hashing, journal, scanner


def _noop(*a):
    pass


def quarantine_files(conn, settings, items, reason: str, summary: str, progress=_noop, cancel=None) -> dict:
    """items: list of (path, kept_path_or_None). When kept_path is given, the file is only moved
    if it is still byte-identical to the kept copy (duplicates)."""
    roots = settings.existing_roots()
    qdir = settings.quarantine_dir
    op = db.new_op(conn, "quarantine", summary, True)
    moved = skipped = 0
    size_total = 0
    for i, (path, kept) in enumerate(items, 1):
        if cancel is not None and cancel.is_set():
            break
        if not os.path.isfile(path):
            skipped += 1
            continue
        if kept:
            if not os.path.isfile(kept) or os.path.normcase(kept) == os.path.normcase(path):
                skipped += 1
                continue
            try:
                if os.path.getsize(kept) != os.path.getsize(path) or \
                        hashing.full_hash(kept, cancel) != hashing.full_hash(path, cancel):
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
        root, rel, _ = db.split_path(path, roots)
        dst = journal.unique_dest(os.path.join(qdir, os.path.basename(root.rstrip("\\/")), rel))
        size = os.path.getsize(path)
        try:
            journal.safe_move(path, dst)
        except OSError:
            skipped += 1
            continue
        journal.record(conn, op, path, dst, size, reason)
        conn.execute("INSERT OR REPLACE INTO quarantine(qpath, original, kept, reason, size, ts, op_id) "
                     "VALUES (?,?,?,?,?,?,?)", (dst, path, kept, reason, size, time.time(), op))
        conn.execute("DELETE FROM files WHERE path=?", (path,))
        moved += 1
        size_total += size
        if i % 100 == 0:
            conn.commit()
            progress(i, len(items), f"Moving to quarantine {i:,}/{len(items):,}")
    db.set_op_summary(conn, op, f"{summary}: {moved:,} files ({size_total / 1024**3:.2f} GB) moved to quarantine")
    conn.commit()
    return {"moved": moved, "skipped": skipped, "bytes": size_total, "op": op}


def restore(conn, settings, ids, progress=_noop, cancel=None) -> dict:
    roots = settings.existing_roots()
    rows = conn.execute(f"SELECT * FROM quarantine WHERE state='in' AND id IN ({','.join('?' * len(ids))})",
                        list(ids)).fetchall()
    ok = failed = 0
    back = []
    for i, r in enumerate(rows, 1):
        if cancel is not None and cancel.is_set():
            break
        dst = r["original"]
        if os.path.exists(dst):
            dst = journal.unique_dest(dst)
        try:
            journal.safe_move(r["qpath"], dst)
        except OSError:
            failed += 1
            continue
        conn.execute("UPDATE quarantine SET state='restored' WHERE id=?", (r["id"],))
        back.append(dst)
        ok += 1
        if i % 200 == 0:
            progress(i, len(rows), f"Restoring {i:,}/{len(rows):,}")
    scanner.index_paths(back, settings, conn)
    journal.remove_empty_dirs([r["qpath"] for r in rows], stop_at=[settings.quarantine_dir])
    db.new_op(conn, "restore", f"Restored {ok:,} files from quarantine", False)
    conn.commit()
    return {"restored": ok, "failed": failed}


def empty(conn, settings, ids, progress=_noop, cancel=None) -> dict:
    """Send quarantined files to the Windows Recycle Bin."""
    rows = conn.execute(f"SELECT * FROM quarantine WHERE state='in' AND id IN ({','.join('?' * len(ids))})",
                        list(ids)).fetchall()
    done = failed = 0
    size = 0
    batch = 200
    for i in range(0, len(rows), batch):
        if cancel is not None and cancel.is_set():
            break
        chunk = [r for r in rows[i:i + batch] if os.path.exists(r["qpath"])]
        missing = [r for r in rows[i:i + batch] if not os.path.exists(r["qpath"])]
        for r in missing:
            conn.execute("UPDATE quarantine SET state='emptied' WHERE id=?", (r["id"],))
        try:
            if chunk:
                send2trash([r["qpath"] for r in chunk])
        except OSError:
            for r in chunk:
                try:
                    send2trash(r["qpath"])
                except OSError:
                    failed += 1
                    continue
        for r in chunk:
            if not os.path.exists(r["qpath"]):
                conn.execute("UPDATE quarantine SET state='emptied' WHERE id=?", (r["id"],))
                done += 1
                size += r["size"] or 0
        conn.commit()
        progress(min(i + batch, len(rows)), len(rows), f"Sending to Recycle Bin {min(i + batch, len(rows)):,}/{len(rows):,}")
    journal.remove_empty_dirs([r["qpath"] for r in rows], stop_at=[settings.quarantine_dir])
    db.new_op(conn, "empty", f"Sent {done:,} quarantined files ({size / 1024**3:.2f} GB) to the Recycle Bin", False)
    conn.commit()
    return {"emptied": done, "failed": failed, "bytes": size}


def import_legacy_log(conn, settings) -> int:
    """Register files moved by the earlier one-off script (_moved_log.csv) so the app manages them."""
    log = os.path.join(settings.quarantine_dir, "_moved_log.csv")
    if not os.path.isfile(log) or db.get_meta(conn, "legacy_log_imported"):
        return 0
    rows = []
    with open(log, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            q = r.get("quarantine_path")
            if q and os.path.isfile(q):
                rows.append((q, r["original_path"], r.get("kept_copy"), os.path.getsize(q)))
    if rows:
        op = db.new_op(conn, "quarantine", f"Earlier duplicate cleanup: {len(rows):,} files moved to quarantine", True)
        mtime = os.path.getmtime(log)
        for q, orig, kept, size in rows:
            journal.record(conn, op, orig, q, size, "duplicate")
            conn.execute("INSERT OR IGNORE INTO quarantine(qpath, original, kept, reason, size, ts, op_id) "
                         "VALUES (?,?,?,?,?,?,?)", (q, orig, kept, "duplicate", size, mtime, op))
    db.set_meta(conn, "legacy_log_imported", 1)
    conn.commit()
    return len(rows)
