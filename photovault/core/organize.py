"""Build a plan that sorts the library into a clean date-based structure, then apply it (undoable)."""
import collections
import os
import re

from . import db, journal

JUNK_DIRS = {".thumbnails", ".globaltrash", ".trash", ".trashed", ".cache", ".tmp", "cache"}
JUNK_FILES = {".nomedia"}
JUNK_EXT = {".lnk", ".tmp", ".ini"}

CHAT_APPS = [
    ("WhatsApp", re.compile(r"whatsapp|-wa\d{4}", re.I)),
    ("Snapchat", re.compile(r"snapchat", re.I)),
    ("Instagram", re.compile(r"instagram", re.I)),
    ("Messenger", re.compile(r"messenger", re.I)),
    ("Facebook", re.compile(r"facebook|fb_img", re.I)),
    ("Telegram", re.compile(r"telegram", re.I)),
    ("Viber", re.compile(r"viber", re.I)),
    ("TikTok", re.compile(r"tiktok", re.I)),
]
SCREEN = re.compile(r"screenshot|screen[ _-]?record|screen[ _-]?capture|capture d", re.I)

LIFE = "Life"
NO_DATE = "_No date"
EVENT_YEAR_SHARE = 0.8  # a hand-picked folder is an event of year X if >= 80% of its photos are from X


def _noop(*a):
    pass


def is_junk(rel: str) -> bool:
    parts = rel.lower().split(os.sep)
    name = parts[-1]
    return any(p in JUNK_DIRS for p in parts[:-1]) or name in JUNK_FILES or os.path.splitext(name)[1] in JUNK_EXT


def _inner(rel: str) -> str:
    """Path inside the top-level folder."""
    parts = rel.split(os.sep)
    return os.sep.join(parts[1:]) if len(parts) > 1 else parts[0]


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .") or "Unnamed"


def _year_month(row):
    d = row["date_taken"] or ""
    return d[:4], d[:7]


def _dated(row) -> bool:
    return row["date_src"] not in (None, "mtime")


def _raw_destination(row, top) -> str:
    rel = row["rel"]
    year, ym = _year_month(row)
    dated = _dated(row)
    low = rel.lower()
    kind = row["kind"]
    if kind == "other":
        return os.path.join("Other", _safe_name(top), _inner(rel))
    for app, rx in CHAT_APPS:
        if rx.search(low):
            return os.path.join("Chat Media", app, year if dated else NO_DATE, row["name"])
    if SCREEN.search(low):
        return os.path.join("Screenshots & Recordings", year if dated else NO_DATE, row["name"])
    if kind == "audio":
        return os.path.join("Audio Recordings", year if dated else NO_DATE, row["name"])
    if not dated:
        return os.path.join(LIFE, NO_DATE, _safe_name(top), _inner(rel))
    return os.path.join(LIFE, year, ym, row["name"])


def build_plan(conn, settings, progress=_noop, cancel=None) -> dict:
    org = os.path.normcase(os.path.abspath(settings.organized_root)) + os.sep
    inbox = os.path.normcase(os.path.abspath(settings.inbox)) + os.sep
    rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()

    candidates = []
    skipped_junk = skipped_rule = 0
    for r in rows:
        norm = os.path.normcase(os.path.abspath(r["path"]))
        if norm.startswith(org) and not norm.startswith(inbox):
            continue  # already organized
        if is_junk(r["rel"]):
            skipped_junk += 1
            continue
        candidates.append(r)

    by_top = collections.defaultdict(list)
    for r in candidates:
        by_top[r["top"]].append(r)

    items = []
    taken = set()
    for top, members in sorted(by_top.items()):
        if cancel is not None and cancel.is_set():
            raise InterruptedError
        cat = settings.category_for(top)
        if cat == "skip":
            skipped_rule += len(members)
            continue
        base = None
        if cat == "curated":
            dated = [m["date_taken"] for m in members if _dated(m) and m["kind"] in ("image", "video")] \
                or [m["date_taken"] for m in members if m["date_taken"]]
            year_counts = collections.Counter(d[:4] for d in dated)
            name = _safe_name(top)
            main_year, n = year_counts.most_common(1)[0] if year_counts else (None, 0)
            if main_year and n >= EVENT_YEAR_SHARE * len(dated):
                in_year = sorted(d for d in dated if d.startswith(main_year))
                median = in_year[len(in_year) // 2]
                base = os.path.join(LIFE, main_year, f"{median[:7]} {name}")
            else:
                base = os.path.join("Collections", name)
        elif cat.startswith("gaming:"):
            base = os.path.join("Gaming", _safe_name(cat.split(":", 1)[1]))
        elif cat == "projects":
            base = os.path.join("Projects", _safe_name(top))
        elif cat == "documents":
            base = os.path.join("Documents & Scans", _safe_name(top))
        elif cat == "other":
            base = os.path.join("Other", _safe_name(top) if top != "(root files)" else "")

        for m in members:
            if base is not None:
                rel_dest = os.path.join(base, _inner(m["rel"]) if top != "(root files)" else m["name"])
            elif cat.startswith("chat:"):
                app = _safe_name(cat.split(":", 1)[1])
                year = m["date_taken"][:4] if _dated(m) else NO_DATE
                rel_dest = os.path.join("Chat Media", app, year, m["name"])
            elif cat == "screenshots":
                year = m["date_taken"][:4] if _dated(m) else NO_DATE
                rel_dest = os.path.join("Screenshots & Recordings", year, m["name"])
            else:
                rel_dest = _raw_destination(m, top)
            dst = os.path.join(settings.organized_root, rel_dest)
            if os.path.normcase(dst) == os.path.normcase(m["path"]):
                continue
            dst = journal.unique_dest(dst, taken)
            items.append({"src": m["path"], "dst": dst, "size": m["size"], "top": top, "category": cat,
                          "dest_group": _group_label(rel_dest)})

    summary = collections.Counter()
    for it in items:
        summary[it["dest_group"]] += 1
    return {"items": items, "summary": sorted(summary.items()), "skipped_junk": skipped_junk,
            "skipped_rule": skipped_rule, "total_bytes": sum(i["size"] for i in items)}


def _group_label(rel_dest: str) -> str:
    """Summary bucket for the preview, e.g. 'Life\\2021', 'Chat Media\\WhatsApp', 'Screenshots & Recordings'."""
    parts = rel_dest.split(os.sep)
    if parts[0] in ("Screenshots & Recordings", "Audio Recordings") or len(parts) < 3:
        return parts[0]
    return os.sep.join(parts[:2])


def apply(conn, settings, plan: dict, progress=_noop, cancel=None) -> dict:
    items = plan["items"]
    roots = settings.library_roots
    op = db.new_op(conn, "organize", "Organize", True)
    moved = skipped = 0
    srcs = []
    for i, it in enumerate(items, 1):
        if cancel is not None and cancel.is_set():
            break
        src, dst = it["src"], it["dst"]
        if not os.path.isfile(src):
            skipped += 1
            continue
        if os.path.exists(dst):
            dst = journal.unique_dest(dst)
        try:
            journal.safe_move(src, dst)
        except OSError:
            skipped += 1
            continue
        journal.record(conn, op, src, dst, it["size"], "organize")
        db.update_path(conn, src, dst, roots)
        srcs.append(src)
        moved += 1
        if i % 200 == 0:
            conn.commit()
            progress(i, len(items), f"Organizing {i:,}/{len(items):,}")
    removed_dirs = journal.remove_empty_dirs(srcs, stop_at=roots)
    db.set_op_summary(conn, op, f"Organized {moved:,} files into {settings.organized_root}"
                                f"{' (cancelled)' if cancel is not None and cancel.is_set() else ''}")
    conn.commit()
    return {"moved": moved, "skipped": skipped, "removed_dirs": removed_dirs, "op": op}
