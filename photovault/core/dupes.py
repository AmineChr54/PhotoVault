"""Exact duplicates (same bytes) and similar photos (perceptual hash, optionally refined by AI embeddings)."""
import collections
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from . import embeddings, hashing

COPY_MARK = re.compile(r"(\(\d+\)|copy|kopie|copie|_\d{1,2})$", re.I)
MAX_SIMILAR_GROUP = 40


def _noop(*a):
    pass


def _folder_counts(conn):
    return {r["top"]: r["n"] for r in conn.execute("SELECT top, COUNT(*) n FROM files GROUP BY top")}


def keep_rank(row, settings, folder_counts):
    """Lower is better. Organized/hand-picked folders win over raw phone dumps; then the
    bigger (more complete) dump; then a clean filename; then a shorter path."""
    path = row["path"]
    org = os.path.normcase(settings.organized_root) + os.sep
    inbox = os.path.normcase(settings.inbox) + os.sep
    norm = os.path.normcase(path)
    if norm.startswith(org) and not norm.startswith(inbox):
        tier = 0
    else:
        cat = settings.category_for(row["top"])
        tier = 0 if cat == "curated" else 2 if cat == "raw" else 1
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


def similar_groups(conn, settings, progress=_noop, cancel=None, ai_db=None) -> list:
    """Groups of similar photos, best keeper first. dHash finds candidates; if an AI index (ai.db, read-only) has
    embeddings for them, dissimilar dHash matches are dropped and near-identical, nearby-in-time shots are added.
    ai_db: path of ai.db (default: the app's own). Without it everything works from hashes and sharpness alone."""
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
                    seen_pairs.add((a, b) if a < b else (b, a))
        if bi % 200 == 0:
            progress(bi, nb, "Grouping similar photos")

    ai = embeddings.open_ai_db(ai_db)
    if ai is not None:
        try:
            progress(0, 0, "Refining with AI embeddings")
            seen_pairs = _refine_with_embeddings(ai, seen_pairs, vals, by_id, cancel)
        finally:
            ai.close()

    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    for a, b in seen_pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    comps = collections.defaultdict(list)
    for fid in {x for pair in seen_pairs for x in pair}:
        comps[find(fid)].append(by_id[fid])

    ignored = _ignored(conn)
    cands = []
    for members in comps.values():
        if len(members) < 2 or len(members) > MAX_SIMILAR_GROUP:
            continue
        if len({m["hash"] for m in members if m["hash"]}) == 1 and all(m["hash"] for m in members):
            continue  # pure exact duplicates - shown in the Exact tab
        key = "sim:" + ",".join(sorted(dh[m["id"]] + str(m["size"]) for m in members))
        if key in ignored:
            continue
        cands.append((key, members))

    # Quality hints for the members of the groups that survived (not for the whole library).
    sharp = {}
    todo = [m for _, members in cands for m in members]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i in range(0, len(todo), 60):
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            chunk = todo[i:i + 60]
            sharp.update(zip((m["id"] for m in chunk), pool.map(lambda m: hashing.sharpness(m["path"]), chunk)))
            progress(min(i + 60, len(todo)), len(todo), "Measuring sharpness")

    groups = []
    for key, members in cands:
        members.sort(key=lambda r: (-(r["width"] or 0) * (r["height"] or 0), -(sharp[r["id"]] or 0),
                                    0 if r["date_src"] == "exif" else 1, -r["size"], len(r["path"])))
        groups.append({"key": key, "files": members, "keep": 0,
                       "sharpness": [sharp[m["id"]] for m in members],
                       "wasted": sum(m["size"] for m in members[1:])})
    groups.sort(key=lambda g: -g["wasted"])
    return groups


def _refine_with_embeddings(ai, pairs, vals, by_id, cancel):
    """Drop dHash pairs whose embeddings clearly differ; add pairs whose embeddings are near-identical.
    Pairs with no embedding on either side keep the plain dHash verdict."""
    try:
        path = {fid: by_id[fid]["path"] for fid in {x for p in pairs for x in p}}
        vecs = embeddings.vectors_for_paths(ai, path.values())
        kept = set()
        for a, b in pairs:
            va, vb = vecs.get(os.path.normcase(path[a])), vecs.get(os.path.normcase(path[b]))
            if va is None or vb is None or embeddings.cosine(va, vb) >= embeddings.SPLIT_MIN_COS:
                kept.add((a, b))
        by_path = {os.path.normcase(by_id[fid]["path"]): fid for fid in vals}
        eligible = {k: k for k in by_path}
        for pa, pb in embeddings.merge_pairs(ai, eligible, cancel):
            a, b = by_path[pa], by_path[pb]
            kept.add((a, b) if a < b else (b, a))
        return kept
    except sqlite3.Error:
        return pairs  # unreadable/incompatible ai.db: fall back to hashes only


def ignore_group(conn, key: str) -> None:
    conn.execute("INSERT OR IGNORE INTO ignored_groups(key) VALUES (?)", (key,))
    conn.commit()
