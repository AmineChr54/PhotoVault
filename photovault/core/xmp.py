"""Optional XMP sidecar export: write `<photo>.<ext>.xmp` next to a photo with its accepted tags and person names so
other apps (digiKam, Immich, Lightroom...) can read them. The photo file itself is never opened for writing.

Safety: an existing sidecar is never overwritten or edited (such photos are skipped); each sidecar is written to a
temp name and renamed into place; every created file is journaled; undo moves a still-unchanged sidecar into the
quarantine folder (nothing is deleted)."""
import os
import re
import time
from xml.sax.saxutils import escape

from . import db, journal

OP_KIND = "xmp_export"
CREATOR = "PhotoVault"
REASON = "xmp_sidecar"
KIND_TITLES = {"person": "People", "event": "Events", "thing": "Things"}
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _noop(*a):
    pass


def sidecar_path(photo: str) -> str:
    """IMG_1.jpg -> IMG_1.jpg.xmp (keeps IMG_1.jpg and IMG_1.dng from sharing a sidecar)."""
    return photo + ".xmp"


def _clean(text: str) -> str:
    return " ".join(_BAD_XML.sub("", text).split())


def build(tags) -> str:
    """XMP packet for `tags`, a list of (kind, name). kind is person / event / thing."""
    names, hier = [], []
    for kind, name in tags:
        name = _clean(name)
        if not name:
            continue
        if name not in names:
            names.append(name)
        h = f"{KIND_TITLES.get(kind, 'Things')}|{name.replace('|', '/')}"
        if h not in hier:
            hier.append(h)

    def bag(items):
        return "<rdf:Bag>" + "".join(f"<rdf:li>{escape(i)}</rdf:li>" for i in items) + "</rdf:Bag>"

    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"\n'
        '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        f'    xmp:CreatorTool="{CREATOR}">\n'
        f'   <dc:subject>{bag(names)}</dc:subject>\n'
        f'   <lr:hierarchicalSubject>{bag(hier)}</lr:hierarchicalSubject>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>\n')


def plan(entries) -> dict:
    """Dry run. entries: {photo_path: [(kind, name), ...]}. Nothing is written."""
    create, existing, missing = [], 0, 0
    for photo, tags in sorted(entries.items()):
        if not tags or photo.lower().endswith(".xmp"):
            continue
        if not os.path.isfile(photo):
            missing += 1
        elif os.path.exists(sidecar_path(photo)):
            existing += 1
        else:
            create.append(photo)
    return {"create": len(create), "existing": existing, "missing": missing, "photos": create}


def export(conn, entries, progress=_noop, cancel=None) -> dict:
    """Create the missing sidecars. Journals every created file under one undoable op."""
    todo = [(p, entries[p]) for p in plan(entries)["photos"]]
    op = db.new_op(conn, OP_KIND, "XMP sidecar export", True)
    conn.commit()
    created = existing = failed = 0
    for i, (photo, tags) in enumerate(todo, 1):
        if cancel is not None and cancel.is_set():
            break
        side = sidecar_path(photo)
        tmp = side + ".pvtmp"
        data = build(tags).encode("utf-8")
        try:
            if os.path.exists(side) or os.path.exists(tmp):   # re-check at write time; never overwrite
                existing += 1
                continue
            with open(tmp, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, side)         # fails (Windows) instead of replacing if a sidecar appeared meanwhile
        except OSError:
            failed += 1
            try:
                if os.path.isfile(tmp) and not os.path.exists(side):
                    os.remove(tmp)       # only our own half-written temp file, never a photo or sidecar
            except OSError:
                pass
            continue
        journal.record(conn, op, side, side, len(data), REASON)
        created += 1
        if i % 100 == 0:
            conn.commit()
            progress(i, len(todo), f"Writing sidecars {i:,}/{len(todo):,}")
    db.set_op_summary(conn, op, f"XMP sidecar export: {created:,} sidecar files created next to photos")
    if not created:
        conn.execute("UPDATE ops SET undoable=0 WHERE id=?", (op,))
    conn.commit()
    return {"created": created, "existing": existing, "failed": failed, "op": op}


def undo(conn, settings, op_id: int, progress=_noop, cancel=None) -> dict:
    """Move the sidecars an export created into quarantine. A sidecar that was edited since (size differs or it no
    longer carries our creator mark) or is already gone is left alone."""
    op = conn.execute("SELECT * FROM ops WHERE id=?", (op_id,)).fetchone()
    if not op or op["kind"] != OP_KIND or not op["undoable"] or op["undone"]:
        return {"restored": 0, "skipped": 0, "error": "This operation can't be undone"}
    roots = settings.existing_roots()
    moves = conn.execute("SELECT * FROM moves WHERE op_id=? ORDER BY id DESC", (op_id,)).fetchall()
    removed = skipped = 0
    for i, m in enumerate(moves, 1):
        if cancel is not None and cancel.is_set():
            break
        side = m["src"]
        try:
            if not os.path.isfile(side) or os.path.getsize(side) != m["size"]:
                skipped += 1
                continue
            with open(side, "rb") as f:
                if CREATOR.encode() not in f.read(4096):
                    skipped += 1
                    continue
            root, rel, _ = db.split_path(side, roots)
            dst = journal.unique_dest(os.path.join(settings.quarantine_dir,
                                                   os.path.basename(root.rstrip("\\/")), rel))
            journal.safe_move(side, dst)
        except OSError:
            skipped += 1
            continue
        conn.execute("INSERT OR REPLACE INTO quarantine(qpath, original, kept, reason, size, ts, op_id) "
                     "VALUES (?,?,?,?,?,?,?)", (dst, side, None, REASON, m["size"], time.time(), op_id))
        removed += 1
        if i % 200 == 0:
            conn.commit()
            progress(i, len(moves), f"Undoing {i:,}/{len(moves):,}")
    if not (cancel is not None and cancel.is_set()):
        conn.execute("UPDATE ops SET undone=1 WHERE id=?", (op_id,))
    conn.commit()
    return {"restored": removed, "skipped": skipped}
