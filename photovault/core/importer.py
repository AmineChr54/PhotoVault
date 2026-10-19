"""Import a folder (e.g. photos copied from a phone) into the Inbox, skipping photos already in the library."""
import collections
import os
import time

from . import config, db, hashing, journal, scanner


def _noop(*a):
    pass


def import_folder(conn, settings, source: str, label: str, progress=_noop, cancel=None) -> dict:
    source = os.path.abspath(source)
    files = []
    for dp, dns, fns in os.walk(source):
        for n in fns:
            if n.lower() in config.IGNORE_NAMES or n.lower() == ".nomedia":
                continue
            p = os.path.join(dp, n)
            try:
                files.append((p, os.path.getsize(p)))
            except OSError:
                pass

    # Library hashes by size, computed lazily only for sizes that collide with an incoming file.
    sizes = {s for _, s in files}
    lib_by_size = collections.defaultdict(list)
    for r in conn.execute("SELECT id, path, size, hash FROM files WHERE size>0"):
        if r["size"] in sizes:
            lib_by_size[r["size"]].append(dict(r))

    def library_has(h, size):
        for r in lib_by_size.get(size, []):
            if not r["hash"]:
                try:
                    r["hash"] = hashing.full_hash(r["path"], cancel)
                    conn.execute("UPDATE files SET hash=? WHERE id=?", (r["hash"], r["id"]))
                except OSError:
                    continue
            if r["hash"] == h:
                return r["path"]
        return None

    dest_root = os.path.join(settings.inbox, f"{_safe(label)} {time.strftime('%Y-%m-%d')}")
    op = db.new_op(conn, "import", f"Import from {source}", False)
    copied, already, failed = [], [], []
    seen_here = {}
    done_bytes = 0
    total_bytes = sum(s for _, s in files) or 1
    for i, (path, size) in enumerate(files, 1):
        if cancel is not None and cancel.is_set():
            break
        try:
            h = hashing.full_hash(path, cancel)
        except OSError as e:
            failed.append((path, str(e)))
            continue
        dup = library_has(h, size) if size > 0 else None
        if dup or (h, size) in seen_here:
            already.append((path, dup or seen_here[(h, size)]))
        else:
            dst = journal.unique_dest(os.path.join(dest_root, os.path.relpath(path, source)))
            tmp = dst + ".pvtmp"
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                hashing.copy_with_hash(path, tmp, cancel)
                if hashing.full_hash(tmp, cancel) != h:
                    raise OSError("verification failed")
                st = os.stat(path)
                os.utime(tmp, (st.st_atime, st.st_mtime))
                os.replace(tmp, dst)
                copied.append(dst)
                seen_here[(h, size)] = dst
                lib_by_size[size].append({"id": None, "path": dst, "size": size, "hash": h})
            except InterruptedError:
                if os.path.exists(tmp):
                    os.remove(tmp)
                break
            except OSError as e:
                failed.append((path, str(e)))
                if os.path.exists(tmp):
                    os.remove(tmp)
        done_bytes += size
        if i % 10 == 0 or i == len(files):
            progress(done_bytes // 1024**2, max(1, total_bytes // 1024**2), f"Importing {i:,}/{len(files):,}")

    scanner.index_paths(copied, settings, conn)
    conn.commit()
    complete = not (cancel is not None and cancel.is_set())
    accounted = len(copied) + len(already)
    db.set_op_summary(conn, op, f"Import from {source}: {len(copied):,} new, {len(already):,} already in library, "
                                f"{len(failed):,} failed")
    conn.commit()
    return {"source": source, "dest": dest_root, "total": len(files), "copied": len(copied),
            "already": len(already), "failed": failed, "complete": complete,
            "safe_to_delete": complete and not failed and accounted == len(files),
            "already_list": already[:500]}


def count_check(source: str) -> dict:
    """Quick count of a folder (to compare with what the phone shows) - photos, videos, other."""
    c = collections.Counter()
    for dp, dns, fns in os.walk(source):
        for n in fns:
            c[config.kind_of(os.path.splitext(n)[1])] += 1
    return dict(c)


def _safe(s: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in s).strip()
    return keep or "Import"
