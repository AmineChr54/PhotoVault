"""Backup = mirror + safety archive.

The backup folder ends up identical to the library, but nothing on the backup disk is ever deleted:
files that were removed or changed on the laptop are moved into _Deleted_Archive\\<date> first.
"""
import os
import shutil
import sqlite3
import time

from . import config, db, hashing, journal

ARCHIVE = "_Deleted_Archive"
DB_DIR = "_PhotoVault_Database"
RESTORED = os.path.join(config._pictures(), "_Restored")
MTIME_SLACK = 2.0  # FAT/exFAT store times with 2 s resolution


def _noop(*a):
    pass


def _listing(root, cancel=None):
    out = {}
    if not os.path.isdir(root):
        return out
    for dp, dns, fns in os.walk(root):
        if cancel is not None and cancel.is_set():
            raise InterruptedError
        for n in fns:
            if n.lower() in config.IGNORE_NAMES or n.endswith(".pvtmp"):
                continue
            p = os.path.join(dp, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime)
    return out


def target_ready(settings):
    """(ok, message) - is the backup disk connected?"""
    if not settings.backup_target.strip():
        return False, "No backup folder chosen yet - pick one under Backup > Backup to"
    drive = os.path.splitdrive(settings.backup_target)[0]
    if drive and not os.path.exists(drive + "\\"):
        return False, f"Backup disk {drive} is not connected"
    return True, ""


def dest_for(settings, source):
    return os.path.join(settings.backup_target, os.path.basename(source.rstrip("\\/")))


def plan(settings, progress=_noop, cancel=None) -> dict:
    result = {"sources": [], "new": 0, "changed": 0, "removed": 0, "bytes": 0}
    for src in settings.backup_sources:
        if not os.path.isdir(src):
            continue
        progress(0, 0, f"Comparing {src} with backup...")
        dst = dest_for(settings, src)
        s = _listing(src, cancel)
        t = _listing(dst, cancel)
        new = [r for r in s if r not in t]
        changed = [r for r in s if r in t and (s[r][0] != t[r][0] or abs(s[r][1] - t[r][1]) > MTIME_SLACK)]
        removed = [r for r in t if r not in s]
        nbytes = sum(s[r][0] for r in new + changed)
        result["sources"].append({"src": src, "dst": dst, "new": new, "changed": changed,
                                  "removed": removed, "bytes": nbytes, "files": len(s)})
        result["new"] += len(new)
        result["changed"] += len(changed)
        result["removed"] += len(removed)
        result["bytes"] += nbytes
    return result


def run(conn, settings, progress=_noop, cancel=None) -> dict:
    ok, msg = target_ready(settings)
    if not ok:
        raise RuntimeError(msg)
    os.makedirs(settings.backup_target, exist_ok=True)
    p = plan(settings, progress, cancel)
    free = shutil.disk_usage(settings.backup_target).free
    if p["bytes"] > free - 512 * 1024**2:
        raise RuntimeError(f"Not enough space on the backup disk: need {p['bytes'] / 1024**3:.1f} GB, "
                           f"free {free / 1024**3:.1f} GB")

    t0 = time.time()
    bid = conn.execute("INSERT INTO backups(ts_start, target, status) VALUES (?,?,?)",
                       (t0, settings.backup_target, "running")).lastrowid
    conn.commit()
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    archive_root = os.path.join(settings.backup_target, ARCHIVE, stamp)
    copied = updated = archived = errors = 0
    done_bytes = 0
    error_list = []
    status = "ok"

    def archive(dst_root, src_name, rel):
        old = os.path.join(dst_root, rel)
        journal.safe_move(old, journal.unique_dest(os.path.join(archive_root, src_name, rel)))

    try:
        for s in p["sources"]:
            src_root, dst_root = s["src"], s["dst"]
            src_name = os.path.basename(dst_root)
            for rel in s["removed"]:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError
                try:
                    archive(dst_root, src_name, rel)
                    archived += 1
                except OSError as e:
                    errors += 1
                    error_list.append(f"{rel}: {e}")
            journal.remove_empty_dirs([os.path.join(dst_root, r) for r in s["removed"]], stop_at=[dst_root])

            changed = set(s["changed"])
            for rel in s["new"] + s["changed"]:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError
                src = os.path.join(src_root, rel)
                dst = os.path.join(dst_root, rel)
                tmp = dst + ".pvtmp"
                try:
                    st = os.stat(src)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    h = hashing.copy_with_hash(src, tmp, cancel)
                    if hashing.full_hash(tmp, cancel) != h:
                        raise OSError("verification failed (copy differs from original)")
                    os.utime(tmp, (st.st_atime, st.st_mtime))
                    if rel in changed and os.path.exists(dst):
                        archive(dst_root, src_name, rel)
                        updated += 1
                    else:
                        copied += 1
                    os.replace(tmp, dst)
                    done_bytes += st.st_size
                except InterruptedError:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    raise
                except OSError as e:
                    errors += 1
                    error_list.append(f"{rel}: {e}")
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                n = copied + updated
                if n % 20 == 0 or done_bytes == p["bytes"]:
                    el = time.time() - t0
                    speed = done_bytes / el if el > 0 else 0
                    eta = (p["bytes"] - done_bytes) / speed if speed > 0 else 0
                    progress(done_bytes // 1024**2, max(1, p["bytes"] // 1024**2),
                             f"Backing up {n:,}/{p['new'] + p['changed']:,} files - "
                             f"{speed / 1024**2:.0f} MB/s - {_fmt_eta(eta)} left")
        _copy_database(conn, settings)
    except InterruptedError:
        status = "cancelled"
    except Exception as e:
        status = f"failed: {e}"
        error_list.append(str(e))
        errors += 1
    if status == "ok" and errors:
        status = "done with errors"
    conn.execute("UPDATE backups SET ts_end=?, copied=?, updated=?, archived=?, bytes=?, errors=?, status=? "
                 "WHERE id=?", (time.time(), copied, updated, archived, done_bytes, errors, status, bid))
    if status in ("ok", "done with errors"):
        db.set_meta(conn, "last_backup", time.time())
    db.new_op(conn, "backup", f"Backup to {settings.backup_target}: {copied:,} new, {updated:,} updated, "
                              f"{archived:,} archived, {errors:,} errors ({status})", False)
    conn.commit()
    return {"copied": copied, "updated": updated, "archived": archived, "errors": errors,
            "error_list": error_list[:200], "bytes": done_bytes, "status": status,
            "seconds": round(time.time() - t0)}


def _fmt_eta(sec):
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec}s"


def _copy_database(conn, settings):
    d = os.path.join(settings.backup_target, DB_DIR)
    os.makedirs(d, exist_ok=True)
    conn.commit()
    out = sqlite3.connect(os.path.join(d, "library.db"))
    with out:
        conn.backup(out)
    out.close()
    if config.SETTINGS_FILE.exists():
        shutil.copy2(config.SETTINGS_FILE, os.path.join(d, "settings.json"))


def verify(settings, full=False, progress=_noop, cancel=None) -> dict:
    """quick: compare names/sizes/dates. full: also compare file contents (slow)."""
    ok, msg = target_ready(settings)
    if not ok:
        raise RuntimeError(msg)
    missing, different, extra = [], [], []
    checked = 0
    for src in settings.backup_sources:
        if not os.path.isdir(src):
            continue
        dst = dest_for(settings, src)
        progress(0, 0, f"Listing {src}...")
        s = _listing(src, cancel)
        t = _listing(dst, cancel)
        extra += [os.path.join(dst, r) for r in t if r not in s]
        items = list(s.items())
        for i, (rel, (size, mtime)) in enumerate(items, 1):
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            if rel not in t:
                missing.append(os.path.join(src, rel))
                continue
            if t[rel][0] != size:
                different.append(os.path.join(src, rel))
            elif full:
                try:
                    if hashing.full_hash(os.path.join(src, rel), cancel) != hashing.full_hash(os.path.join(dst, rel), cancel):
                        different.append(os.path.join(src, rel))
                except OSError:
                    different.append(os.path.join(src, rel))
            checked += 1
            if full and i % 50 == 0:
                progress(i, len(items), f"Checking file contents {i:,}/{len(items):,}")
    return {"checked": checked, "missing": missing, "different": different, "extra": extra, "full": full}


def restore(settings, backup_path: str) -> str:
    """Copy one file or folder from the backup back to the laptop. Never overwrites: if the
    original location is taken, it goes to <Pictures>\\_Restored instead. Returns where it went."""
    bp = os.path.abspath(backup_path)
    original = None
    for src in settings.backup_sources:
        dst_root = os.path.abspath(dest_for(settings, src))
        if os.path.normcase(bp) == os.path.normcase(dst_root) or os.path.normcase(bp).startswith(os.path.normcase(dst_root) + os.sep):
            original = os.path.join(src, os.path.relpath(bp, dst_root))
            break
    if original is None or os.path.exists(original):
        original = os.path.join(RESTORED, time.strftime("%Y-%m-%d_%H%M%S"), os.path.basename(bp))
    if os.path.isdir(bp):
        shutil.copytree(bp, original, dirs_exist_ok=False)
    else:
        os.makedirs(os.path.dirname(original), exist_ok=True)
        shutil.copy2(bp, original)
    return original


def history(conn, limit=50):
    return conn.execute("SELECT * FROM backups ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
