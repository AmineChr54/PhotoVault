"""Exact duplicates (same bytes) and similar photos (perceptual hash)."""
import collections
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import hashing

COPY_MARK = re.compile(r"(\(\d+\)|copy|kopie|copie|_\d{1,2})$", re.I)
MAX_SIMILAR_GROUP = 40


def _noop(*a):
    pass


def _folder_counts(conn):
    return {r["top"]: r["n"] for r in conn.execute("SELECT top, COUNT(*) n FROM files GROUP BY top")}


def _folder_tier(row, settings) -> int:
    """0 = organized/hand-picked folder, 1 = neutral, 2 = raw phone dump. Lower is better."""
    path = row["path"]
    org = os.path.normcase(settings.organized_root) + os.sep
    inbox = os.path.normcase(settings.inbox) + os.sep
    norm = os.path.normcase(path)
    if norm.startswith(org) and not norm.startswith(inbox):
        return 0
    cat = settings.category_for(row["top"])
    return 0 if cat == "curated" else 2 if cat == "raw" else 1


def keep_rank(row, settings, folder_counts):
    """Lower is better. Organized/hand-picked folders win over raw phone dumps; then the
    bigger (more complete) dump; then a clean filename; then a shorter path."""
    path = row["path"]
    tier = _folder_tier(row, settings)
    stem = os.path.splitext(os.path.basename(path))[0].strip()
    return (tier, 1 if COPY_MARK.search(stem) else 0, -folder_counts.get(row["top"], 0), len(path), path.lower())


def _ignored(conn):
    return {r["key"] for r in conn.execute("SELECT key FROM ignored_groups")}


def exact_groups(conn, settings, progress=_noop, cancel=None) -> list:
    rows = conn.execute(
        "SELECT * FROM files WHERE size>0 AND size IN "
        "(SELECT size FROM files WHERE size>0 GROUP BY size HAVING COUNT(*)>1)").fetchall()
    by_size = collections.defaultdict(list)
    for r in rows:
        by_size[r["size"]].append(r)
    hashes = {r["id"]: r["hash"] for r in rows}

    sizes = list(by_size)
    for gi, size in enumerate(sizes, 1):
        if cancel is not None and cancel.is_set():
            raise InterruptedError
        members = by_size[size]
        missing = [m for m in members if not hashes[m["id"]]]
        if missing:
            if len(missing) == len(members):
                partial = collections.defaultdict(list)
                for m in members:
                    try:
                        partial[hashing.partial_hash(m["path"], size)].append(m)
                    except OSError:
                        pass
                candidates = [m for g in partial.values() if len(g) > 1 for m in g]
            else:
                candidates = missing
            for m in candidates:
                try:
                    h = hashing.full_hash(m["path"], cancel)
                except OSError:
                    continue
                hashes[m["id"]] = h
                conn.execute("UPDATE files SET hash=? WHERE id=?", (h, m["id"]))
        if gi % 100 == 0:
            conn.commit()
            progress(gi, len(sizes), f"Comparing files {gi:,}/{len(sizes):,}")
    conn.commit()

    ignored = _ignored(conn)
    counts = _folder_counts(conn)
    by_hash = collections.defaultdict(list)
    for r in rows:
        h = hashes[r["id"]]
        if h:
            by_hash[h].append(r)
    groups = []
    for h, members in by_hash.items():
        if len(members) < 2 or h in ignored:
            continue
        members.sort(key=lambda r: keep_rank(r, settings, counts))
        groups.append({"key": h, "size": members[0]["size"], "files": members, "keep": 0,
                       "wasted": members[0]["size"] * (len(members) - 1)})
    groups.sort(key=lambda g: -g["wasted"])
    return groups


def _dhash_job(row):
    return row["id"], hashing.dhash(row["path"]) or ""


def similar_groups(conn, settings, progress=_noop, cancel=None) -> list:
    threshold = max(1, min(7, int(settings.similar_threshold)))  # the 8-byte bucketing below needs <= 7
    rows = conn.execute("SELECT * FROM files WHERE kind='image' AND size>0").fetchall()
    todo = [r for r in rows if r["dhash"] is None]
    dh = {r["id"]: r["dhash"] for r in rows}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i in range(0, len(todo), 300):
            if cancel is not None and cancel.is_set():
                conn.commit()
                raise InterruptedError
            chunk = todo[i:i + 300]
            res = list(pool.map(_dhash_job, chunk))
            conn.executemany("UPDATE files SET dhash=? WHERE id=?", [(h, fid) for fid, h in res])
            dh.update(dict(res))
            conn.commit()
            progress(min(i + 300, len(todo)), len(todo), f"Analysing photos {min(i + 300, len(todo)):,}/{len(todo):,}")

    by_id = {r["id"]: r for r in rows}
    vals = {}
    for fid, h in dh.items():
        if h:
            v = int(h, 16)
            if 6 <= v.bit_count() <= 58:  # skip near-blank images (black/white frames)
                vals[fid] = v

    # Pigeonhole: with <= 7 differing bits out of 64, at least one of 8 bytes is identical.
    buckets = collections.defaultdict(list)
    for fid, v in vals.items():
        for b in range(8):
            buckets[(b, (v >> (8 * b)) & 0xFF)].append(fid)

    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    seen_pairs = set()
    nb = len(buckets)
    for bi, ids in enumerate(buckets.values(), 1):
        if cancel is not None and cancel.is_set():
            raise InterruptedError
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            a = ids[i]
            va = vals[a]
            for j in range(i + 1, len(ids)):
                b = ids[j]
                if (va ^ vals[b]).bit_count() <= threshold:
                    key = (a, b) if a < b else (b, a)
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        union(a, b)
        if bi % 200 == 0:
            progress(bi, nb, "Grouping similar photos")

    comps = collections.defaultdict(list)
    for fid in {x for pair in seen_pairs for x in pair}:
        comps[find(fid)].append(by_id[fid])

    ignored = _ignored(conn)
    groups = []
    for members in comps.values():
        if len(members) < 2 or len(members) > MAX_SIMILAR_GROUP:
            continue
        if len({m["hash"] for m in members if m["hash"]}) == 1 and all(m["hash"] for m in members):
            continue  # pure exact duplicates - shown in the Exact tab
        key = "sim:" + ",".join(sorted(dh[m["id"]] + str(m["size"]) for m in members))
        if key in ignored:
            continue
        members.sort(key=lambda r: (-(r["width"] or 0) * (r["height"] or 0),
                                    0 if r["date_src"] == "exif" else 1, -r["size"], len(r["path"])))
        groups.append({"key": key, "files": members, "keep": 0,
                       "wasted": sum(m["size"] for m in members[1:])})
    groups.sort(key=lambda g: -g["wasted"])
    return groups


# ---- "keep best" auto-pick (pure ranking; never moves anything) ----
AUTO_PICK_DEFAULT = ("folder", "resolution", "size", "date")
AUTO_PICK_LABELS = {"folder": "hand-picked folder", "resolution": "larger resolution",
                    "size": "larger file size", "date": "earlier date taken"}


def describe_auto_pick(order=AUTO_PICK_DEFAULT) -> str:
    """The rule as one human-readable line, e.g. 'hand-picked folder > larger resolution > ...'."""
    return " > ".join(AUTO_PICK_LABELS[c] for c in order)


def pick_best(files, settings, order=AUTO_PICK_DEFAULT) -> int:
    """Index in `files` of the copy to keep. Criteria are applied in `order` (each in
    AUTO_PICK_LABELS); remaining ties keep the group's existing order (first wins)."""
    unknown = [c for c in order if c not in AUTO_PICK_LABELS]
    if unknown:
        raise ValueError(f"unknown auto-pick criteria: {unknown}")
    if not files:
        raise ValueError("empty group")

    def part(row, crit):
        if crit == "folder":
            return _folder_tier(row, settings)
        if crit == "resolution":
            return -((row["width"] or 0) * (row["height"] or 0))
        if crit == "size":
            return -(row["size"] or 0)
        d = row["date_taken"] or ""  # ISO text; undated copies lose to dated ones
        return (1, "") if not d else (0, d)

    return min(range(len(files)), key=lambda i: tuple(part(files[i], c) for c in order) + (i,))


def auto_pick(groups, settings, order=AUTO_PICK_DEFAULT) -> list:
    """Preview: [(group, best_index)] for groups whose current pick differs from the best."""
    out = []
    for g in groups:
        best = pick_best(g["files"], settings, order)
        if best != g["keep"]:
            out.append((g, best))
    return out


def ignore_group(conn, key: str) -> None:
    conn.execute("INSERT OR IGNORE INTO ignored_groups(key) VALUES (?)", (key,))
    conn.commit()
