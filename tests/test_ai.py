"""Tests for photovault/ai using synthetic embeddings and fakes: no models, network, GPU or real library."""
import importlib
import json
import sys
import threading
import types

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xxhash")
pytest.importorskip("PIL")

from photovault.ai import paths, people, search, store  # noqa: E402
from photovault.core import config  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "data" / "ai.db")
    yield c
    c.close()


def unit(*v):
    a = np.asarray(v, np.float32)
    return a / np.linalg.norm(a)


def add_photo(conn, h, vec, path=None, date=None):
    conn.execute("INSERT INTO photo(hash,path,date_taken) VALUES (?,?,?)", (h, path or f"/p/{h}.jpg", date))
    conn.execute("INSERT INTO embedding(hash,vec) VALUES (?,?)", (h, store.vec_to_blob(vec)))
    conn.commit()


def add_face(conn, h, emb, score=0.9, person_id=None, cluster=None, rejected=0):
    cur = conn.execute("INSERT INTO face(hash,score,emb,person_id,cluster,rejected) VALUES (?,?,?,?,?,?)",
                       (h, score, store.vec_to_blob(emb, np.float32), person_id, cluster, rejected))
    conn.commit()
    return cur.lastrowid


# ---------- paths ----------
def test_paths_layout():
    assert paths.AI_DB == paths.DATA / "ai.db"
    assert paths.THUMBS.parent == paths.FACE_CROPS.parent == paths.MODELS.parent == paths.DATA
    assert paths.SIGLIP.parent == paths.FACES.parent == paths.MODELS
    assert paths.VENV_PY.parts[-3:] == ("venv", "Scripts", "python.exe")


def test_paths_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOTOVAULT_AI_DATA", str(tmp_path / "x"))
    try:
        m = importlib.reload(paths)
        assert m.DATA == tmp_path / "x" and m.AI_DB == tmp_path / "x" / "ai.db"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


# ---------- store ----------
def test_connect_creates_schema_and_wal(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"photo", "seen", "embedding", "person", "face", "meta"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_default_path_uses_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "AI_DB", tmp_path / "sub" / "ai.db")
    store.connect().close()
    assert (tmp_path / "sub" / "ai.db").exists()


def test_vec_blob_roundtrip():
    v = np.array([0.5, -0.25, 1.0], np.float32)
    assert np.allclose(store.blob_to_vec(store.vec_to_blob(v)), v)  # float16 exact for these
    b = store.vec_to_blob(v, np.float32)
    assert len(b) == 12 and np.array_equal(store.blob_to_vec(b, np.float32), v)


def test_load_embeddings_empty_and_normalised(conn):
    hashes, m = store.load_embeddings(conn)
    assert hashes == [] and m.shape == (0, 768)
    add_photo(conn, "a", np.array([3.0, 4.0], np.float32))
    hashes, m = store.load_embeddings(conn)
    assert hashes == ["a"] and m.dtype == np.float32
    assert abs(np.linalg.norm(m[0]) - 1) < 1e-3


def test_stats(conn):
    add_photo(conn, "a", unit(1, 0))
    conn.execute("INSERT INTO photo(hash,path,status,error) VALUES ('err:x','x','error','boom')")
    pid = people.person_id(conn, "Mom")
    add_face(conn, "a", unit(1, 0, 0), person_id=pid, cluster=1)
    add_face(conn, "a", unit(0, 1, 0), cluster=-1)
    add_face(conn, "a", unit(0, 0, 1), rejected=1, cluster=1)
    conn.commit()
    assert store.stats(conn) == {"photos": 1, "errors": 1, "embedded": 1, "faces": 2, "faces_named": 1,
                                 "people": 1, "clusters": 1}


# ---------- search ranking ----------
def make_index(conn):
    add_photo(conn, "beach", unit(1, 0, 0), date="2023-07-01 10:00:00")
    add_photo(conn, "city", unit(0, 1, 0), date="2022-05-01 10:00:00")
    add_photo(conn, "mix", unit(1, 1, 0), date="2023-01-01 10:00:00")
    add_photo(conn, "nodate", unit(0, 0, 1))
    return search.Index(conn)


def test_search_ranks_by_cosine(conn):
    ix = make_index(conn)
    res = ix.search(unit(1, 0, 0))
    assert [r[0] for r in res][:2] == ["beach", "mix"]
    assert res[0][2] == pytest.approx(1.0, abs=1e-3)
    assert [r[2] for r in res] == sorted((r[2] for r in res), reverse=True)
    assert len(ix.search(unit(1, 0, 0), limit=2)) == 2


def test_search_no_text_newest_first(conn):
    ix = make_index(conn)
    assert [r[0] for r in ix.search(None)] == ["beach", "mix", "city", "nodate"]


def test_search_year_filter(conn):
    ix = make_index(conn)
    assert {r[0] for r in ix.search(unit(1, 0, 0), year=2023)} == {"beach", "mix"}
    assert ix.search(unit(1, 0, 0), year=1999) == []


def test_search_people_filter_all_must_match_case_insensitive(conn):
    make_index(conn)
    mom, nils = people.person_id(conn, "Mom"), people.person_id(conn, "Nils")
    add_face(conn, "beach", unit(1, 0, 0), person_id=mom)
    add_face(conn, "beach", unit(0, 1, 0), person_id=nils)
    add_face(conn, "city", unit(1, 0, 0), person_id=mom)
    add_face(conn, "mix", unit(0, 0, 1), person_id=nils, rejected=1)  # rejected faces do not count
    ix = search.Index(conn)
    assert {r[0] for r in ix.search(unit(1, 0, 0), people=["mom"])} == {"beach", "city"}
    assert [r[0] for r in ix.search(unit(1, 0, 0), people=["Mom", "NILS"])] == ["beach"]
    assert ix.search(None, people=["Ghost"]) == []


def test_search_empty_index(conn):
    assert search.Index(conn).search(unit(1, 0)) == []


def test_index_reload_picks_up_new_photos(conn):
    ix = make_index(conn)
    add_photo(conn, "new", unit(0, 0, 1), date="2024-01-01 00:00:00")
    assert "new" not in ix.pos
    ix.reload()
    assert "new" in ix.pos


# ---------- people: SQL helpers ----------
def test_person_id_case_insensitive_and_trimmed(conn):
    a = people.person_id(conn, " Mom ")
    assert people.person_id(conn, "mom") == a
    assert conn.execute("SELECT name FROM person").fetchone()[0] == "Mom"


def test_name_cluster_only_unnamed_unrejected(conn):
    add_photo(conn, "a", unit(1, 0))
    other = people.person_id(conn, "Other")
    f1 = add_face(conn, "a", unit(1, 0, 0), cluster=5)
    f2 = add_face(conn, "a", unit(1, 0, 0), cluster=5, person_id=other)
    f3 = add_face(conn, "a", unit(1, 0, 0), cluster=5, rejected=1)
    assert people.name_cluster(conn, 5, "Anna") == 1

    def row(i):
        return conn.execute("SELECT person_id, manual FROM face WHERE id=?", (i,)).fetchone()
    assert row(f1)["manual"] == 1 and row(f1)["person_id"] != other
    assert row(f2)["person_id"] == other
    assert row(f3)["person_id"] is None


def test_reject_and_assign_face(conn):
    add_photo(conn, "a", unit(1, 0))
    f = add_face(conn, "a", unit(1, 0, 0))
    people.assign_face(conn, f, "Bob")
    assert conn.execute("SELECT person_id FROM face WHERE id=?", (f,)).fetchone()[0] is not None
    people.reject_face(conn, f)
    r = conn.execute("SELECT person_id, rejected FROM face WHERE id=?", (f,)).fetchone()
    assert r["person_id"] is None and r["rejected"] == 1
    people.assign_face(conn, f, "Bob")  # re-assigning un-rejects
    assert conn.execute("SELECT rejected FROM face WHERE id=?", (f,)).fetchone()[0] == 0


def test_reject_cluster_spares_named(conn):
    add_photo(conn, "a", unit(1, 0))
    pid = people.person_id(conn, "X")
    a = add_face(conn, "a", unit(1, 0, 0), cluster=3)
    b = add_face(conn, "a", unit(1, 0, 0), cluster=3, person_id=pid)
    people.reject_cluster(conn, 3)
    rej = {r["id"]: r["rejected"] for r in conn.execute("SELECT id, rejected FROM face")}
    assert rej == {a: 1, b: 0}


def test_merge_and_rename_persons(conn):
    add_photo(conn, "a", unit(1, 0))
    p1, p2 = people.person_id(conn, "A"), people.person_id(conn, "B")
    add_face(conn, "a", unit(1, 0, 0), person_id=p1)
    people.merge_persons(conn, p1, p2)
    assert [r["name"] for r in conn.execute("SELECT name FROM person")] == ["B"]
    assert conn.execute("SELECT person_id FROM face").fetchone()[0] == p2
    people.rename_person(conn, p2, "  Carl ")
    assert conn.execute("SELECT name FROM person").fetchone()[0] == "Carl"


def test_clusters_and_persons_listing(conn):
    add_photo(conn, "a", unit(1, 0))
    add_photo(conn, "b", unit(0, 1))
    for i in range(3):
        add_face(conn, "a", unit(1, 0, 0), score=0.1 * i, cluster=1)
    add_face(conn, "b", unit(1, 0, 0), cluster=2)       # below min_size
    add_face(conn, "b", unit(1, 0, 0), cluster=-1)      # noise
    cl = people.clusters(conn)
    assert len(cl) == 1 and cl[0]["cluster"] == 1 and cl[0]["size"] == 3 and len(cl[0]["faces"]) == 3
    best = conn.execute("SELECT id FROM face WHERE cluster=1 ORDER BY score DESC").fetchone()[0]
    assert cl[0]["faces"][0] == best
    pid = people.person_id(conn, "Z")
    add_face(conn, "a", unit(1, 0, 0), person_id=pid)
    add_face(conn, "b", unit(1, 0, 0), person_id=pid)
    ps = people.persons(conn)
    assert ps[0]["name"] == "Z" and ps[0]["faces"] == 2 and ps[0]["photos"] == 2
    assert len(people.person_faces(conn, pid)) == 2


def test_parse_query(conn):
    people.person_id(conn, "Mom")
    people.person_id(conn, "Nils")
    people.person_id(conn, "Mom Smith")
    found, rest = people.parse_query(conn, "Mom and Nils at the beach")
    assert sorted(found) == ["Mom", "Nils"] and rest == "at the beach"
    assert people.parse_query(conn, "mom smith") == (["Mom Smith"], "")   # longest name wins
    assert people.parse_query(conn, "Momentum") == ([], "Momentum")      # word boundary
    assert people.parse_query(conn, "sunset") == ([], "sunset")


# ---------- people: clustering ----------
def blob_faces(conn, centers, per=6, noise=0.02, seed=0):
    rng = np.random.default_rng(seed)
    add_photo(conn, f"p{seed}", unit(1, 0))
    ids = []
    for c in centers:
        for _ in range(per):
            v = np.asarray(c, np.float32) + rng.normal(0, noise, len(c)).astype(np.float32)
            ids.append(add_face(conn, f"p{seed}", v / np.linalg.norm(v)))
    return ids


def test_recluster_separates_identities(conn):
    ids = blob_faces(conn, [unit(*([1] + [0] * 15)), unit(*([0, 1] + [0] * 14))])
    assert people.recluster(conn, min_cluster_size=4) == 2
    lab = {r["id"]: r["cluster"] for r in conn.execute("SELECT id, cluster FROM face")}
    a, b = {lab[i] for i in ids[:6]}, {lab[i] for i in ids[6:]}
    assert len(a) == len(b) == 1 and a != b and -1 not in a | b


def test_recluster_too_few_faces_and_keeps_named(conn):
    blob_faces(conn, [unit(*([1] + [0] * 7))], per=3)
    assert people.recluster(conn, min_cluster_size=4) == 0
    ids = blob_faces(conn, [unit(*([1] + [0] * 7))], per=6, seed=1)
    pid = people.person_id(conn, "Keep")
    conn.execute("UPDATE face SET person_id=?, cluster=99 WHERE id=?", (pid, ids[0]))
    conn.commit()
    people.recluster(conn, min_cluster_size=4)
    r = conn.execute("SELECT person_id, cluster FROM face WHERE id=?", (ids[0],)).fetchone()
    assert r["person_id"] == pid and r["cluster"] == 99


def test_assign_known_threshold_and_vote(conn):
    add_photo(conn, "p", unit(1, 0))
    pid_a, pid_b = people.person_id(conn, "A"), people.person_id(conn, "B")
    add_face(conn, "p", unit(1, 0, 0), person_id=pid_a)
    add_face(conn, "p", unit(0, 1, 0), person_id=pid_b)
    near_a = add_face(conn, "p", unit(1, 0.1, 0))
    near_b = add_face(conn, "p", unit(0.1, 1, 0))
    far = add_face(conn, "p", unit(0, 0, 1))
    assert people.assign_known(conn) == 2
    own = {r["id"]: (r["person_id"], r["manual"]) for r in conn.execute("SELECT id, person_id, manual FROM face")}
    assert own[near_a] == (pid_a, 0) and own[near_b] == (pid_b, 0) and own[far][0] is None


def test_assign_known_without_named_faces(conn):
    blob_faces(conn, [unit(1, 0, 0)], per=2)
    assert people.assign_known(conn) == 0


# ---------- indexer ----------
indexer = pytest.importorskip("photovault.ai.indexer")
from PIL import Image  # noqa: E402


def make_img(path, color=(200, 30, 30), size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def test_list_photos_filters(tmp_path):
    root, quar = tmp_path / "lib", tmp_path / "quar"
    make_img(root / "Holiday" / "a.jpg")
    make_img(root / "Holiday" / "b.PNG")
    (root / "Holiday" / "note.txt").write_text("x")
    make_img(root / "Screens" / "s.jpg")
    make_img(root / ".hidden" / "h.jpg")
    make_img(root / "Holiday" / ".thumbnails" / "t.jpg")
    make_img(root / "top.jpg")
    make_img(quar / "q.jpg")
    make_img(root / "quar_inside" / "q2.jpg")
    s = config.Settings(library_roots=[str(root), str(root), str(tmp_path / "missing")],
                        quarantine_dir=str(root / "quar_inside"),
                        folder_rules=[["Screens*", "screenshots"], ["Holiday", "raw"]])
    got = sorted(p.replace(str(root), "").lstrip("\\/").replace("\\", "/")
                 for p, _, _ in indexer.list_photos(s, extra_roots=[]))
    assert got == ["Holiday/a.jpg", "Holiday/b.PNG", "top.jpg"]
    extra = indexer.list_photos(s, extra_roots=[str(quar)])
    assert any(p.endswith("q.jpg") for p, _, _ in extra)


def test_load_one_ok_and_error(tmp_path):
    p = make_img(tmp_path / "a.jpg", size=(200, 100))
    st = p.stat()
    r = indexer.load_one((str(p), st.st_size, st.st_mtime))
    assert "error" not in r and (r["w"], r["h"]) == (200, 100) and len(r["hash"]) == 32
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    r = indexer.load_one((str(bad), 12, 0.0))
    assert "error" in r and "img" not in r


def test_save_thumb_and_face_crop(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "THUMBS", tmp_path / "thumbs")
    monkeypatch.setattr(paths, "FACE_CROPS", tmp_path / "crops")
    img = Image.new("RGB", (1000, 800), (1, 2, 3))
    indexer.save_thumb(img, "abc")
    assert max(Image.open(tmp_path / "thumbs" / "abc.jpg").size) <= indexer.THUMB_MAX
    indexer.save_face_crop(img, (900, 700, 1200, 1000), 7)   # box exceeding the image is clamped
    assert max(Image.open(tmp_path / "crops" / "7.jpg").size) <= indexer.CROP


class FakeSigLIP:
    def __init__(self, prefer_gpu=True):
        pass

    def prep(self, img):
        return np.asarray(img.resize((8, 8)), np.float32).mean(axis=(0, 1))

    def encode_images(self, x):
        return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)


class FakeFaces:
    def __init__(self, prefer_gpu=True):
        pass

    def detect(self, rgb):
        h, w = rgb.shape[:2]
        return [((2, 2, w - 2, h - 2), 0.95, None), ((0, 0, 5, 5), 0.9, None)]  # 2nd is below MIN_FACE

    def embed(self, rgb, kps):
        return np.ones(512, np.float32) / np.sqrt(512)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Fake models + a tiny synthetic library; all AI state under tmp_path."""
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA", data)
    monkeypatch.setattr(paths, "AI_DB", data / "ai.db")
    monkeypatch.setattr(paths, "THUMBS", data / "thumbs")
    monkeypatch.setattr(paths, "FACE_CROPS", data / "facecrops")
    fake = types.ModuleType("photovault.ai.models")
    fake.SigLIP, fake.Faces = FakeSigLIP, FakeFaces
    monkeypatch.setitem(sys.modules, "photovault.ai.models", fake)
    lib = tmp_path / "lib"
    make_img(lib / "A" / "red.jpg", (200, 10, 10))
    make_img(lib / "A" / "green.jpg", (10, 200, 10))
    (lib / "A" / "dup.jpg").write_bytes((lib / "A" / "red.jpg").read_bytes())   # identical content
    (lib / "A" / "broken.jpg").write_bytes(b"garbage")
    s = config.Settings(library_roots=[str(lib)], quarantine_dir=str(tmp_path / "q"))
    orig = indexer.list_photos
    monkeypatch.setattr(indexer, "list_photos", lambda: orig(s, extra_roots=[]))
    return lib


def run_index(capsys, **kw):
    capsys.readouterr()
    indexer.run(workers=2, **kw)
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_run_end_to_end_and_resume(sandbox, capsys):
    ev = run_index(capsys)
    assert ev[0]["event"] == "listing" and ev[-1]["event"] == "done"
    assert next(e for e in ev if e["event"] == "start")["total"] == 4
    c = store.connect()
    st = store.stats(c)
    assert st["photos"] == 2 and st["errors"] == 1 and st["embedded"] == 2 and st["faces"] == 2
    assert c.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 4          # dup + error remembered too
    assert len(list(paths.THUMBS.glob("*.jpg"))) == 2 and len(list(paths.FACE_CROPS.glob("*.jpg"))) == 2
    c.close()
    ev2 = run_index(capsys)                                                    # nothing changed: resumable
    assert [e["event"] for e in ev2] == ["listing", "start", "done"] and ev2[-1]["processed"] == 0


def test_run_limit_and_no_faces(sandbox, capsys):
    run_index(capsys, limit=1, use_faces=False)
    c = store.connect()
    st = store.stats(c)
    assert st["photos"] + st["errors"] == 1 and st["faces"] == 0
    c.close()


def test_run_cancel_stops_early(sandbox, capsys):
    cancel = threading.Event()
    cancel.set()
    ev = run_index(capsys, cancel=cancel)
    assert ev[-1]["event"] == "done"


def test_run_leaves_originals_untouched(sandbox, capsys):
    before = {p: p.read_bytes() for p in sandbox.rglob("*.jpg")}
    run_index(capsys)
    assert {p: p.read_bytes() for p in sandbox.rglob("*.jpg")} == before


# ---------- worker CLI / client ----------
def test_worker_cli_dispatch(monkeypatch):
    from photovault.ai import worker
    seen = {}
    monkeypatch.setattr(indexer, "run", lambda **kw: seen.update(kw))
    worker.main(["index", "--limit", "3", "--no-faces", "--cpu", "--workers", "2"])
    assert seen == dict(limit=3, use_faces=False, prefer_gpu=False, workers=2, sample_every=None)
    with pytest.raises(SystemExit):
        worker.main([])


def test_client_unavailable_without_venv(monkeypatch, tmp_path):
    from photovault.ai import client
    monkeypatch.setattr(paths, "VENV_PY", tmp_path / "nope" / "python.exe")
    c = client.AiClient()
    assert not c.available
    with pytest.raises(RuntimeError, match="AI environment not found"):
        c.start()
    assert c.last_progress() is None
    c.events += [{"event": "other"}, {"event": "progress", "done": 1}, {"event": "x"}]
    assert c.last_progress()["done"] == 1


# ---- tags / GPS / taken_ts (#14) ----

def test_old_db_is_migrated(tmp_path):
    import sqlite3
    p = tmp_path / "old" / "ai.db"
    p.parent.mkdir()
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE photo (hash TEXT PRIMARY KEY, path TEXT NOT NULL, size INTEGER, mtime REAL, "
                "date_taken TEXT, width INTEGER, height INTEGER, n_faces INTEGER DEFAULT 0, "
                "status TEXT NOT NULL DEFAULT 'ok', error TEXT)")
    raw.execute("INSERT INTO photo(hash,path) VALUES ('h1','/p/a.jpg')")
    raw.commit()
    raw.close()
    for _ in range(2):  # idempotent
        c = store.connect(p)
        cols = {r[1] for r in c.execute("PRAGMA table_info(photo)")}
        assert {"lat", "lon", "taken_ts"} <= cols
        assert c.execute("SELECT path, lat FROM photo").fetchone()["path"] == "/p/a.jpg"
        c.close()


def test_tag_helpers(conn):
    add_photo(conn, "a", unit(1, 0))
    t = store.get_or_create_tag(conn, "Paris 2023", "event")
    assert store.get_or_create_tag(conn, " Paris 2023 ", "event") == t
    assert store.get_or_create_tag(conn, "Paris 2023", "thing") != t
    with pytest.raises(ValueError):
        store.get_or_create_tag(conn, "x", "bogus")
    store.add_photo_tag(conn, "a", t, "auto", 0.4)
    assert [r["name"] for r in store.tags_for_photo(conn, "a")] == ["Paris 2023"]
    store.reject_photo_tag(conn, "a", t)
    assert store.tags_for_photo(conn, "a") == []
    store.add_photo_tag(conn, "a", t, "auto", 0.9)  # auto must not undo a manual rejection
    assert store.photos_for_tag(conn, t) == []
    store.add_photo_tag(conn, "a", t, "manual")
    assert store.photos_for_tag(conn, t) == ["a"]


def test_geo_date_and_load_one(tmp_path):
    import datetime as dt

    from PIL import Image

    from photovault.ai import indexer
    c = store.connect(tmp_path / "g.db")
    c.execute("INSERT INTO photo(hash,path) VALUES ('a','/a.jpg')")
    store.set_photo_geo_date(c, "a", 48.5, -2.25, 123.0)
    r = c.execute("SELECT lat,lon,taken_ts FROM photo").fetchone()
    assert (r["lat"], r["lon"], r["taken_ts"]) == (48.5, -2.25, 123.0)
    c.close()

    img = Image.new("RGB", (8, 8), "red")
    ex = img.getexif()
    ex[306] = "2020:05:06 07:08:09"
    g = ex.get_ifd(0x8825)
    g[1], g[2], g[3], g[4] = "N", (48.0, 30.0, 0.0), "W", (2.0, 15.0, 0.0)
    p = tmp_path / "with_gps.jpg"
    img.save(p, exif=ex)
    d = indexer.load_one((str(p), p.stat().st_size, p.stat().st_mtime))
    assert d["lat"] == pytest.approx(48.5) and d["lon"] == pytest.approx(-2.25)
    assert dt.datetime.fromtimestamp(d["taken_ts"]) == dt.datetime(2020, 5, 6, 7, 8, 9)

    q = tmp_path / "IMG_20190102_030405.jpg"  # no EXIF: date comes from the filename
    Image.new("RGB", (8, 8)).save(q)
    d = indexer.load_one((str(q), q.stat().st_size, q.stat().st_mtime))
    assert d["lat"] is None and d["date"] is None
    assert dt.datetime.fromtimestamp(d["taken_ts"]).year == 2019

# ---------------------------------------------------------------- first-run setup (fakes only)
def _setup_env(tmp_path, monkeypatch):
    from photovault.ai import setup_env
    data = tmp_path / "ai"
    for name, val in [("DATA", data), ("MODELS", data / "models"), ("VENV", data / "venv"),
                      ("VENV_PY", data / "venv" / "Scripts" / "python.exe"),
                      ("SETUP_MARKER", data / "setup_in_progress"), ("PROJECT", tmp_path / "proj")]:
        monkeypatch.setattr(paths, name, val)
    return setup_env


def test_ai_setup_full_run_and_available(tmp_path, monkeypatch):
    from photovault.ai.client import AiClient
    setup_env = _setup_env(tmp_path, monkeypatch)
    calls, events = [], []

    def stream(cmd, on_line):
        calls.append(cmd)
        if "venv" in cmd:
            paths.VENV_PY.parent.mkdir(parents=True)
            paths.VENV_PY.write_text("")
        on_line("Collecting numpy")
        return 0

    def dl(url, dest, progress, cancel):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x")
        progress(1, 1)

    s = setup_env.AiSetup(progress=lambda *a: events.append(a), stream=stream, downloader=dl,
                          find=lambda: ["py", "-3.13"])
    s.run()
    assert calls[0][:2] == ["py", "-3.13"] and "pip" in calls[1]
    assert setup_env.models_ready() and not paths.SETUP_MARKER.exists()
    assert AiClient().available and events[-1][0] == "done"


def test_ai_setup_not_available_while_interrupted(tmp_path, monkeypatch):
    from photovault.ai.client import AiClient
    _setup_env(tmp_path, monkeypatch)
    paths.VENV_PY.parent.mkdir(parents=True)
    paths.VENV_PY.write_text("")
    assert AiClient().available
    paths.SETUP_MARKER.write_text("running")
    assert not AiClient().available


def test_ai_setup_no_python_and_pip_failure(tmp_path, monkeypatch):
    setup_env = _setup_env(tmp_path, monkeypatch)
    with pytest.raises(setup_env.SetupError, match="3.13"):
        setup_env.AiSetup(stream=lambda c, f: 0, find=lambda: None).run()
    paths.VENV_PY.parent.mkdir(parents=True)
    paths.VENV_PY.write_text("")
    with pytest.raises(setup_env.SetupError, match="packages"):
        setup_env.AiSetup(stream=lambda c, f: 1, find=lambda: None).run()
    assert paths.SETUP_MARKER.exists()  # stays "not ready" until a retry succeeds


def test_ai_setup_cancel(tmp_path, monkeypatch):
    setup_env = _setup_env(tmp_path, monkeypatch)
    paths.VENV_PY.parent.mkdir(parents=True)
    paths.VENV_PY.write_text("")
    holder = {}

    def stream(cmd, on_line):
        holder["s"].cancel()
        return 0

    holder["s"] = setup_env.AiSetup(stream=stream, downloader=lambda *a: None)
    with pytest.raises(setup_env.SetupCancelled):
        holder["s"].run()


def test_find_python_version_check():
    from photovault.ai import setup_env

    def run_ok(ver):
        return lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=ver, stderr="")
    assert setup_env.find_python(run_ok("Python 3.13.1"), lambda n: n) == ["py", "-3.13"]
    assert setup_env.find_python(run_ok("Python 3.9.0"), lambda n: n) is None
    assert setup_env.find_python(run_ok("Python 3.13.1"), lambda n: None) is None


def test_download_resumes_with_range(tmp_path):
    from photovault.ai import setup_env

    class Resp:
        def __init__(self, data, status):
            self.data, self.status = data, status
            self.headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, n):
            c, self.data = self.data[:n], self.data[n:]
            return c

    seen = {}

    def opener(req, timeout):
        seen["range"] = req.headers.get("Range")
        return Resp(b"world", 206)

    dest = tmp_path / "m" / "f.onnx"
    dest.parent.mkdir()
    (tmp_path / "m" / "f.onnx.part").write_bytes(b"hello")
    setup_env.download("http://x/f", dest, opener=opener)
    assert seen["range"] == "bytes=5-" and dest.read_bytes() == b"helloworld"
    assert not (tmp_path / "m" / "f.onnx.part").exists()

