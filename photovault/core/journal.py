"""Logged file moves with undo."""
import os
import shutil

from . import db


def unique_dest(dst: str, taken: set | None = None) -> str:
    """dst, or dst with _1, _2 ... appended if it already exists (on disk or in `taken`)."""
    base, ext = os.path.splitext(dst)
    cand, n = dst, 0
    while os.path.exists(cand) or (taken is not None and os.path.normcase(cand) in taken):
        n += 1
        cand = f"{base}_{n}{ext}"
    if taken is not None:
        taken.add(os.path.normcase(cand))
    return cand


def safe_move(src: str, dst: str) -> None:
    if os.path.exists(dst):
        raise FileExistsError(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.splitdrive(src)[0].lower() == os.path.splitdrive(dst)[0].lower():
        os.rename(src, dst)
    else:
        shutil.move(src, dst)


def record(conn, op_id: int, src: str, dst: str, size: int | None, reason: str | None) -> None:
    conn.execute("INSERT INTO moves(op_id, src, dst, size, reason) VALUES (?,?,?,?,?)",
                 (op_id, src, dst, size, reason))


def remove_empty_dirs(paths, stop_at: list) -> int:
    """Remove now-empty parent folders of `paths`. Only folders strictly inside one of
    `stop_at` are ever removed."""
    stops = [os.path.normcase(os.path.abspath(s)) for s in stop_at]

    def inside(d):
        n = os.path.normcase(os.path.abspath(d))
        return any(n.startswith(s + os.sep) for s in stops)

    dirs = {os.path.dirname(p) for p in paths}
    removed = 0
    for d in sorted(dirs, key=len, reverse=True):
        cur = d
        while cur and inside(cur):
            try:
                if os.listdir(cur):
                    break
                os.rmdir(cur)
                removed += 1
            except OSError:
                break
            cur = os.path.dirname(cur)
    return removed


def undo(conn, op_id: int, roots: list, extra_dirs=(), progress=lambda *a: None, cancel=None) -> dict:
    """Move every file of an operation back. `extra_dirs` (e.g. quarantine) may have empty
    folders cleaned up as well."""
    op = conn.execute("SELECT * FROM ops WHERE id=?", (op_id,)).fetchone()
    if not op or not op["undoable"] or op["undone"] or op["kind"] == "xmp_export":  # xmp.undo handles those
        return {"restored": 0, "skipped": 0, "error": "This operation can't be undone"}
    moves = conn.execute("SELECT * FROM moves WHERE op_id=? ORDER BY id DESC", (op_id,)).fetchall()
    restored = skipped = 0
    back = []
    for i, m in enumerate(moves, 1):
        if cancel is not None and cancel.is_set():
            break
        src, dst = m["src"], m["dst"]
        if os.path.exists(dst) and not os.path.exists(src):
            try:
                safe_move(dst, src)
            except OSError:
                skipped += 1
                continue
            db.update_path(conn, dst, src, roots)
            conn.execute("UPDATE quarantine SET state='restored' WHERE qpath=?", (dst,))
            restored += 1
            back.append(src)
        else:
            skipped += 1
        if i % 200 == 0:
            conn.commit()
            progress(i, len(moves), f"Undoing {i:,}/{len(moves):,}")
    remove_empty_dirs([m["dst"] for m in moves], stop_at=list(roots) + list(extra_dirs))
    if not (cancel is not None and cancel.is_set()):
        conn.execute("UPDATE ops SET undone=1 WHERE id=?", (op_id,))
    conn.commit()
    return {"restored": restored, "skipped": skipped, "paths": back}
