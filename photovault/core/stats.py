"""Numbers for the dashboard and the exported report."""
import html
import os
import shutil
import time

from . import db


def _disk(path):
    drive = os.path.splitdrive(path)[0]
    if not drive or not os.path.exists(drive + "\\"):
        return None
    u = shutil.disk_usage(drive + "\\")
    return {"drive": drive, "total": u.total, "used": u.used, "free": u.free}


def collect(conn, settings) -> dict:
    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    kinds = {r["kind"]: (r["n"], r["b"] or 0) for r in q("SELECT kind, COUNT(*) n, SUM(size) b FROM files GROUP BY kind")}
    total_n = sum(v[0] for v in kinds.values())
    total_b = sum(v[1] for v in kinds.values())
    by_year = q("SELECT substr(date_taken,1,4) y, COUNT(*) n, SUM(size) b FROM files "
                "WHERE kind IN ('image','video') AND date_src<>'mtime' GROUP BY y ORDER BY y")
    no_date = q("SELECT COUNT(*) n FROM files WHERE kind IN ('image','video') AND date_src='mtime'")[0]["n"]
    by_folder = q("SELECT top, COUNT(*) n, SUM(size) b FROM files GROUP BY top ORDER BY b DESC")
    by_camera = q("SELECT camera, COUNT(*) n FROM files WHERE camera IS NOT NULL AND camera<>'' "
                  "GROUP BY camera ORDER BY n DESC LIMIT 15")
    by_ext = q("SELECT ext, COUNT(*) n, SUM(size) b FROM files GROUP BY ext ORDER BY n DESC LIMIT 15")
    dup = q("SELECT COALESCE(SUM(size * (n - 1)), 0) w, COALESCE(SUM(n - 1), 0) c FROM "
            "(SELECT hash, MIN(size) size, COUNT(*) n FROM files WHERE hash IS NOT NULL GROUP BY hash HAVING n > 1)")[0]
    quar = q("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM quarantine WHERE state='in'")[0]
    last_backup = db.get_meta(conn, "last_backup")
    last_scan = db.get_meta(conn, "last_scan")
    roots = settings.existing_roots()
    return {
        "total_files": total_n, "total_bytes": total_b,
        "photos": kinds.get("image", (0, 0)), "videos": kinds.get("video", (0, 0)),
        "audio": kinds.get("audio", (0, 0)), "other": kinds.get("other", (0, 0)),
        "by_year": [(r["y"], r["n"], r["b"]) for r in by_year], "no_date": no_date,
        "by_folder": [(r["top"], r["n"], r["b"]) for r in by_folder],
        "by_camera": [(r["camera"], r["n"]) for r in by_camera],
        "by_ext": [(r["ext"] or "(none)", r["n"], r["b"]) for r in by_ext],
        "dup_wasted": dup["w"], "dup_count": dup["c"],
        "quarantine_n": quar["n"], "quarantine_b": quar["b"],
        "last_backup": float(last_backup) if last_backup else None,
        "last_scan": float(last_scan) if last_scan else None,
        "disk_library": _disk(roots[0]) if roots else None,
        "disk_backup": _disk(settings.backup_target),
    }


def gb(b) -> str:
    b = b or 0
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} GB"
    return f"{b / 1024**2:.0f} MB"


def days_since(ts):
    return None if ts is None else int((time.time() - ts) // 86400)


def reminder_text(settings, last_backup, now=None):
    """Text for the "backup overdue" startup notification, or None when no reminder is due.

    Silent when the user switched reminders off, has not finished setup, or has no backup folder
    chosen (a fresh install should not nag about a backup it cannot make yet).
    """
    if not settings.backup_notify or not settings.setup_complete or not settings.backup_target:
        return None
    if last_backup is None:
        return "No backup has been made yet."
    days = int(((now if now is not None else time.time()) - last_backup) // 86400)
    if days < settings.backup_reminder_days:
        return None
    return f"Last backup was {days} day{'s' if days != 1 else ''} ago."


def export_html(s: dict, path: str) -> None:
    def table(title, headers, rows):
        h = "".join(f"<th>{html.escape(x)}</th>" for x in headers)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
        return f"<h2>{html.escape(title)}</h2><table><tr>{h}</tr>{body}</table>"

    lb = days_since(s["last_backup"])
    parts = [
        f"<h1>Photo Vault report</h1><p>{time.strftime('%Y-%m-%d %H:%M')}</p>",
        "<ul>",
        f"<li><b>{s['total_files']:,}</b> files, <b>{gb(s['total_bytes'])}</b></li>",
        f"<li>{s['photos'][0]:,} photos ({gb(s['photos'][1])}), {s['videos'][0]:,} videos ({gb(s['videos'][1])})</li>",
        f"<li>Photos/videos without a date: {s['no_date']:,}</li>",
        f"<li>Known duplicates: {s['dup_count']:,} ({gb(s['dup_wasted'])})</li>",
        f"<li>In quarantine: {s['quarantine_n']:,} ({gb(s['quarantine_b'])})</li>",
        f"<li>Last backup: {'never' if lb is None else f'{lb} days ago'}</li>",
        "</ul>",
        table("By year", ["Year", "Files", "Size"], [(y, f"{n:,}", gb(b)) for y, n, b in s["by_year"]]),
        table("By folder", ["Folder", "Files", "Size"], [(f, f"{n:,}", gb(b)) for f, n, b in s["by_folder"]]),
        table("By camera / phone", ["Camera", "Photos"], [(c, f"{n:,}") for c, n in s["by_camera"]]),
        table("By file type", ["Type", "Files", "Size"], [(e, f"{n:,}", gb(b)) for e, n, b in s["by_ext"]]),
    ]
    css = ("body{font-family:Segoe UI,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#222}"
           "table{border-collapse:collapse;width:100%;margin-bottom:16px}td,th{border-bottom:1px solid #ddd;"
           "padding:4px 8px;text-align:left}th{background:#f3f3f3}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset='utf-8'><title>Photo Vault report</title><style>{css}</style>"
                + "".join(parts))
