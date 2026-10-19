"""One pass per photo: read bytes once -> hash, SigLIP embedding, faces, thumbnail. Resumable, read-only
on the originals. Runs in the worker venv."""
import collections
import io
import itertools
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import xxhash
from PIL import Image, ImageOps

from ..core import config
from . import autotag, paths, store

DECODE_MAX = 1600
MIN_FACE = 28  # px on the decoded image; smaller faces are unreliable
CROP = 144
THUMB_MAX = 384
BATCH = 16
# Decoded images (~6 MB each at 1600 px) in flight at once = WINDOW (queued reads) + BATCH (being processed).
WINDOW = BATCH * 2
FALLBACK_DET = 640  # smaller SCRFD input used to retry a photo whose full-size detection failed (e.g. out of memory)
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def emit(**kw):
    import json
    print(json.dumps(kw), flush=True)


def list_photos(settings=None, extra_roots=None):
    """Photos in the library roots (+ any paths.EXTRA_ROOTS), skipping screenshots/gaming folders and junk caches."""
    settings = settings or config.Settings.load()
    roots = list(settings.library_roots) + list(extra_roots if extra_roots is not None else paths.EXTRA_ROOTS)
    skip_dirs = {os.path.normcase(os.path.abspath(settings.quarantine_dir))}
    out, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d.lower() != ".thumbnails" and not d.startswith(".")
                      and os.path.normcase(os.path.abspath(os.path.join(dp, d))) not in skip_dirs]
            rel = os.path.relpath(dp, root)
            top = rel.split(os.sep)[0] if rel != "." else ""
            cat = settings.category_for(top) if top else "raw"
            if cat == "screenshots" or cat.startswith("gaming"):
                dns[:] = []
                continue
            for n in fns:
                if os.path.splitext(n)[1].lower() not in IMG_EXT:
                    continue
                p = os.path.join(dp, n)
                key = os.path.normcase(p)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((p, st.st_size, st.st_mtime))
    return out


def _exif_date(img):
    try:
        ex = img.getexif()
        v = ex.get(36867) or (ex.get_ifd(0x8769).get(36867) if hasattr(ex, "get_ifd") else None) or ex.get(306)
        if v:
            return str(v).replace(":", "-", 2)[:19]
    except Exception:
        pass
    return None


def _gps_degrees(img):
    """(lat, lon) in decimal degrees from EXIF GPS, or (None, None)."""
    try:
        g = img.getexif().get_ifd(0x8825)
        if not g or 2 not in g or 4 not in g:
            return None, None

        def deg(v, ref):
            d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -d if str(ref).upper() in ("S", "W") else d
        lat, lon = deg(g[2], g.get(1, "N")), deg(g[4], g.get(3, "E"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            return None, None
        return lat, lon
    except Exception:
        return None, None


def _taken_ts(path, mtime, exif_date):
    """Epoch seconds of the best known date (EXIF, else filename, else mtime)."""
    import datetime as dt

    from ..core import dates
    when = None
    if exif_date:
        try:
            when = dt.datetime.strptime(exif_date[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            when = None
    when, _src = dates.best_date(path, "photo", mtime, when)
    try:
        return when.timestamp()
    except (OSError, OverflowError, ValueError):
        return None


def load_one(item):
    """Worker thread: read file once; return decoded small image, hash, metadata."""
    path, size, mtime = item
    try:
        data = open(path, "rb").read()
        h = xxhash.xxh3_128_hexdigest(data)
        img = Image.open(io.BytesIO(data))
        w0, h0 = img.size
        date = _exif_date(img)
        lat, lon = _gps_degrees(img)
        ts = _taken_ts(path, mtime, date)
        img.draft("RGB", (DECODE_MAX, DECODE_MAX))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((DECODE_MAX, DECODE_MAX), Image.Resampling.BILINEAR)
        return dict(path=path, size=size, mtime=mtime, hash=h, img=img, w=w0, h=h0, date=date,
                    lat=lat, lon=lon, taken_ts=ts)
    except Exception as e:  # damaged / unsupported file must never stop the run
        return dict(path=path, size=size, mtime=mtime, error=f"{type(e).__name__}: {e}"[:200])


def _atomic_save_jpeg(img, out, quality):
    """Write via a temp file + rename so a killed run never leaves a truncated jpg that later runs would trust."""
    tmp = out.with_name(out.name + ".tmp")
    try:
        img.save(tmp, "JPEG", quality=quality)
        os.replace(tmp, out)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def save_thumb(img, h):
    paths.THUMBS.mkdir(parents=True, exist_ok=True)
    out = paths.THUMBS / f"{h}.jpg"
    if not (out.exists() and out.stat().st_size > 0):
        t = img.copy()
        t.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.BILINEAR)
        _atomic_save_jpeg(t, out, 82)


def save_face_crop(img, box, face_id):
    paths.FACE_CROPS.mkdir(parents=True, exist_ok=True)
    x1, y1, x2, y2 = (float(v) for v in box)
    m = 0.25 * max(x2 - x1, y2 - y1)
    c = img.crop((max(0, x1 - m), max(0, y1 - m), min(img.width, x2 + m), min(img.height, y2 + m)))
    c.thumbnail((CROP, CROP), Image.Resampling.LANCZOS)
    _atomic_save_jpeg(c, paths.FACE_CROPS / f"{face_id}.jpg", 88)


def repair_after_interrupt(conn):
    """Clean up leftovers of a run that died mid-way. Index rows are committed one batch at a time, so a
    killed run normally leaves nothing half-written; this removes any face rows whose photo never got an
    embedding (so re-indexing that photo cannot duplicate its faces) and stale temp files. Returns #faces removed."""
    orphans = [r["id"] for r in conn.execute(
        "SELECT id FROM face WHERE hash NOT IN (SELECT hash FROM embedding)")]
    for fid in orphans:
        conn.execute("DELETE FROM face WHERE id=?", (fid,))
        try:
            os.remove(paths.FACE_CROPS / f"{fid}.jpg")
        except OSError:
            pass
    conn.commit()
    for d in (paths.THUMBS, paths.FACE_CROPS):
        if d.is_dir():
            for t in d.glob("*.tmp"):
                try:
                    t.unlink()
                except OSError:
                    pass
    return len(orphans)


def _detect_faces(faces, rgb):
    """([(box, score, embedding)], error_or_None) for faces large enough to keep. A failure (e.g. MemoryError
    in the detector) is retried once with a smaller detector input; if that fails too the photo just gets no
    faces. Never raises, so one bad photo cannot kill the run."""
    err = None
    for size in (None, FALLBACK_DET):
        try:
            found = []
            for box, score, kps in (faces.detect(rgb) if size is None else faces.detect(rgb, det_size=size)):
                if min(box[2] - box[0], box[3] - box[1]) < MIN_FACE:
                    continue
                found.append((box, score, faces.embed(rgb, kps)))
            return found, None
        except Exception as ex:
            err = ex
    return [], err


def _encode_all(sig, ok):
    """One embedding per photo (an Exception instance for a photo that could not be embedded). The batch is
    tried at once; if that fails the photos are retried one by one so a single bad image costs only itself."""
    try:
        return list(sig.encode_images(np.stack([sig.prep(b["img"]) for b in ok])))
    except Exception as ex:
        if len(ok) == 1:
            return [ex]
    out = []
    for b in ok:
        try:
            out.append(sig.encode_images(np.stack([sig.prep(b["img"])]))[0])
        except Exception as ex:
            out.append(ex)
    return out


def _record_error(conn, b, msg):
    conn.execute("INSERT OR REPLACE INTO seen(path,size,mtime,hash) VALUES (?,?,?,NULL)",
                 (b["path"], b["size"], b["mtime"]))
    conn.execute("INSERT OR REPLACE INTO photo(hash,path,size,mtime,status,error) VALUES (?,?,?,?,?,?)",
                 ("err:" + b["path"], b["path"], b["size"], b["mtime"], "error", msg))


def run(limit=None, use_faces=True, prefer_gpu=True, cancel=None, workers=4, sample_every=None):
    from .models import Faces, SigLIP
    conn = store.connect()
    try:
        _run(conn, SigLIP, Faces, limit, use_faces, prefer_gpu, cancel, workers, sample_every)
    finally:
        conn.close()  # an interrupted batch is rolled back, never half-committed


def _run(conn, SigLIP, Faces, limit, use_faces, prefer_gpu, cancel, workers, sample_every):
    t0 = time.time()
    emit(event="listing")
    removed = repair_after_interrupt(conn)
    if removed:
        emit(event="warning", message=f"removed {removed} leftover face rows from an interrupted run")
    items = list_photos()
    done = {r["path"]: (r["size"], r["mtime"]) for r in conn.execute("SELECT path,size,mtime FROM seen")}
    todo = [i for i in items if done.get(i[0]) != (i[1], i[2])]
    del done
    if sample_every:
        todo = todo[::sample_every]
    if limit:
        todo = todo[:limit]
    total = len(todo)
    emit(event="start", total=total, library=len(items))
    del items
    if not total:
        emit(event="done", processed=0, seconds=0)
        return
    sig = SigLIP(prefer_gpu)
    faces = Faces(prefer_gpu) if use_faces else None
    n = nfaces = face_failures = 0
    t_inf = 0.0
    batch = []

    def flush():
        nonlocal n, nfaces, t_inf, face_failures
        ok = []
        for b in batch:
            if "error" in b:
                continue
            if conn.execute("SELECT 1 FROM embedding WHERE hash=?", (b["hash"],)).fetchone() or \
                    any(o["hash"] == b["hash"] for o in ok):
                # identical content already indexed (duplicate copy): just remember this path
                conn.execute("INSERT OR REPLACE INTO seen(path,size,mtime,hash) VALUES (?,?,?,?)",
                             (b["path"], b["size"], b["mtime"], b["hash"]))
            else:
                ok.append(b)
        for b in batch:
            if "error" in b:
                _record_error(conn, b, b["error"])
        if ok:
            t1 = time.time()
            embs = _encode_all(sig, ok)
            tagged = []
            for b, e in zip(ok, embs):
                if isinstance(e, Exception):
                    _record_error(conn, b, f"embedding failed: {type(e).__name__}: {e}"[:200])
                    emit(event="warning", message=f"could not embed {b['path']}: {type(e).__name__}")
                    continue
                nf = 0
                if faces:
                    rgb = np.asarray(b["img"])
                    found, ferr = _detect_faces(faces, rgb)
                    if ferr is not None:  # the photo is still indexed for search, just without faces
                        face_failures += 1
                        emit(event="warning", message=f"face detection failed for {b['path']}: {type(ferr).__name__}")
                    hh, ww = rgb.shape[:2]
                    for box, score, fe in found:
                        cur = conn.execute("INSERT INTO face(hash,x1,y1,x2,y2,score,emb) VALUES (?,?,?,?,?,?,?)",
                                           (b["hash"], float(box[0] / ww), float(box[1] / hh), float(box[2] / ww),
                                            float(box[3] / hh), score, store.vec_to_blob(fe, np.float32)))
                        save_face_crop(b["img"], box, cur.lastrowid)
                        nf += 1
                conn.execute("INSERT OR REPLACE INTO embedding(hash,vec) VALUES (?,?)", (b["hash"], store.vec_to_blob(e)))
                conn.execute("INSERT OR REPLACE INTO photo(hash,path,size,mtime,date_taken,width,height,n_faces,status,lat,lon,taken_ts) "
                             "VALUES (?,?,?,?,?,?,?,?, 'ok',?,?,?)",
                             (b["hash"], b["path"], b["size"], b["mtime"], b["date"], b["w"], b["h"], nf,
                              b.get("lat"), b.get("lon"), b.get("taken_ts")))
                conn.execute("INSERT OR REPLACE INTO seen(path,size,mtime,hash) VALUES (?,?,?,?)",
                             (b["path"], b["size"], b["mtime"], b["hash"]))
                save_thumb(b["img"], b["hash"])
                nfaces += nf
                tagged.append((b["hash"], e))
            try:
                autotag.tag_embeddings(conn, tagged, sig.encode_text)
            except Exception as ex:  # tagging must never break indexing
                emit(event="warning", message=f"auto-tagging skipped: {ex}")
            t_inf += time.time() - t1
        conn.commit()
        n += len(batch)
        el = time.time() - t0
        emit(event="progress", done=n, total=total, faces=nfaces, per_sec=round(n / el, 2),
             infer_sec=round(t_inf, 1), eta=round((total - n) / max(n / el, 1e-9)))
        for b in batch:
            b.pop("img", None)
        batch.clear()

    # Bounded read-ahead: decoded 1600 px images are ~6 MB each, so at most WINDOW queued + BATCH being
    # processed are alive at once, however large the library is.
    window = max(WINDOW, workers)
    with ThreadPoolExecutor(workers) as pool:
        pending = collections.deque()
        it = iter(todo)
        for item in itertools.islice(it, window):
            pending.append(pool.submit(load_one, item))
        while pending:
            r = pending.popleft().result()
            nxt = next(it, None)
            if nxt is not None:
                pending.append(pool.submit(load_one, nxt))
            batch.append(r)
            if len(batch) >= BATCH:
                flush()
            if cancel is not None and cancel.is_set():
                break
        if batch:
            flush()
    emit(event="done", processed=n, faces=nfaces, face_failures=face_failures, seconds=round(time.time() - t0, 1))
