import datetime as dt
import os
import struct

import pytest
from PIL import Image

from photovault.core import (backup, config, db, dates, dupes, health, importer, journal, organize,
                             quarantine, scanner, stats)


def make_jpg(path, color=(200, 30, 30), size=(400, 300), exif_date=None, pattern=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size, color)
    if pattern:
        for x in range(size[0]):
            for y in range(0, size[1], 7):
                img.putpixel((x, y), ((x * 3) % 255, (y * 5) % 255, 90))
        for x in range(0, size[0] // 2):
            for y in range(size[1] // 2):
                img.putpixel((x, y), (20, 20, 20))
    exif = Image.Exif()
    if exif_date:
        exif.get_ifd(0x8769)[36867] = exif_date
        exif[272] = "Redmi 10"
    img.save(path, "JPEG", quality=90, exif=exif)


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture
def env(tmp_path):
    lib = tmp_path / "Library"
    org = tmp_path / "Photos"
    s = config.Settings(library_roots=[str(lib), str(org)], organized_root=str(org),
                        quarantine_dir=str(tmp_path / "_Q"), backup_sources=[str(org)],
                        backup_target=str(tmp_path / "Backup"))
    conn = db.connect(tmp_path / "lib.db")
    return s, conn, lib, org, tmp_path


# ---------- dates ----------
@pytest.mark.parametrize("name,expected", [
    ("IMG_20210812_153012.jpg", dt.datetime(2021, 8, 12, 15, 30, 12)),
    ("PXL_20240501_101500123.jpg", dt.datetime(2024, 5, 1, 10, 15, 0)),
    ("IMG-20190703-WA0012.jpg", dt.datetime(2019, 7, 3)),
    ("Screenshot_2022-01-05-21-03-44-123_com.app.jpg", dt.datetime(2022, 1, 5, 21, 3, 44)),
    ("VID_20200101_000001.mp4", dt.datetime(2020, 1, 1, 0, 0, 1)),
    ("DSC00123.JPG", None),
])
def test_filename_dates(name, expected):
    assert dates.from_filename(name) == expected


def test_mp4_creation_time(tmp_path):
    secs = int((dt.datetime(2023, 6, 15, 12, 0, 0) - dt.datetime(1904, 1, 1)).total_seconds())
    mvhd_body = bytes([0, 0, 0, 0]) + struct.pack(">II", secs, secs) + b"\x00" * 88
    mvhd = struct.pack(">I4s", 8 + len(mvhd_body), b"mvhd") + mvhd_body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x00\x00"
    p = tmp_path / "v.mp4"
    p.write_bytes(ftyp + moov)
    assert dates.mp4_created(str(p)) == dt.datetime(2023, 6, 15, 12, 0, 0)


def test_exif_date_and_camera(tmp_path):
    p = str(tmp_path / "a.jpg")
    make_jpg(p, exif_date="2020:07:14 18:22:01")
    when, cam, w, h = dates.image_info(p)
    assert when == dt.datetime(2020, 7, 14, 18, 22, 1)
    assert cam == "Redmi 10" and (w, h) == (400, 300)


# ---------- scan + duplicates + quarantine + undo ----------
def test_exact_duplicates_keep_curated_and_undo(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "tsawer lmama w baba" / "mama.jpg"), exif_date="2021:01:01 10:00:00")
    for d in ("Redmi 10 (12)", "Redmi 10 (13)"):
        os.makedirs(lib / d, exist_ok=True)
        with open(lib / "tsawer lmama w baba" / "mama.jpg", "rb") as f:
            write(str(lib / d / "IMG_1.jpg"), f.read())
    make_jpg(str(lib / "Redmi 10 (12)" / "other.jpg"), color=(1, 2, 3))
    res = scanner.scan(s, conn=conn)
    assert res["total"] == 4

    groups = dupes.exact_groups(conn, s)
    assert len(groups) == 1
    g = groups[0]
    assert "tsawer lmama w baba" in g["files"][g["keep"]]["path"]

    keep = g["files"][0]["path"]
    out = quarantine.quarantine_files(conn, s, [(f["path"], keep) for f in g["files"][1:]], "duplicate", "test")
    assert out["moved"] == 2
    assert os.path.exists(keep)
    assert not os.path.exists(lib / "Redmi 10 (13)" / "IMG_1.jpg")
    assert conn.execute("SELECT COUNT(*) FROM quarantine WHERE state='in'").fetchone()[0] == 2
    assert dupes.exact_groups(conn, s) == []

    u = journal.undo(conn, out["op"], s.library_roots, extra_dirs=[s.quarantine_dir])
    assert u["restored"] == 2
    assert os.path.exists(lib / "Redmi 10 (13)" / "IMG_1.jpg")


def test_quarantine_refuses_when_content_differs(env):
    s, conn, lib, org, tmp = env
    write(str(lib / "A" / "x.jpg"), b"aaaa")
    write(str(lib / "B" / "x.jpg"), b"bbbb")
    scanner.scan(s, conn=conn)
    out = quarantine.quarantine_files(conn, s, [(str(lib / "B" / "x.jpg"), str(lib / "A" / "x.jpg"))], "duplicate", "t")
    assert out["moved"] == 0 and os.path.exists(lib / "B" / "x.jpg")


def test_restore_and_empty(env, monkeypatch):
    s, conn, lib, org, tmp = env
    write(str(lib / "A" / "1.jpg"), b"1")
    write(str(lib / "A" / "2.jpg"), b"2")
    scanner.scan(s, conn=conn)
    quarantine.quarantine_files(conn, s, [(str(lib / "A" / "1.jpg"), None), (str(lib / "A" / "2.jpg"), None)], "junk", "t")
    ids = [r["id"] for r in conn.execute("SELECT id FROM quarantine ORDER BY id")]
    quarantine.restore(conn, s, [ids[0]])
    assert os.path.exists(lib / "A" / "1.jpg")
    trashed = []
    monkeypatch.setattr(quarantine, "send2trash", lambda p: [trashed.append(x) or os.remove(x) for x in (p if isinstance(p, list) else [p])])
    quarantine.empty(conn, s, [ids[1]])
    assert len(trashed) == 1
    assert conn.execute("SELECT state FROM quarantine WHERE id=?", (ids[1],)).fetchone()[0] == "emptied"


def test_similar_photos(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "Redmi 10" / "big.jpg"), size=(1200, 900), pattern=True, exif_date="2022:02:02 02:02:02")
    with Image.open(lib / "Redmi 10" / "big.jpg") as im:
        os.makedirs(lib / "Redmi Whatsapp", exist_ok=True)
        im.resize((400, 300)).save(lib / "Redmi Whatsapp" / "IMG-20220202-WA0001.jpg", quality=60)
    make_jpg(str(lib / "Redmi 10" / "different.jpg"), color=(10, 200, 10), size=(800, 600))
    scanner.scan(s, conn=conn)
    groups = dupes.similar_groups(conn, s)
    assert len(groups) == 1
    assert groups[0]["files"][0]["name"] == "big.jpg"  # highest resolution suggested as keeper


# ---------- organize ----------
def test_organize_plan_apply_undo(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "Redmi 10 (3)" / "DCIM" / "Camera" / "IMG_20210812_101010.jpg"))
    make_jpg(str(lib / "Redmi 10 (3)" / "WhatsApp" / "Media" / "IMG-20200101-WA0001.jpg"), color=(1, 1, 1))
    make_jpg(str(lib / "Redmi 10 (3)" / "Pictures" / "Screenshots" / "Screenshot_2022-03-04-05-06-07.jpg"), color=(2, 2, 2))
    make_jpg(str(lib / "sif 2020" / "beach" / "a.jpg"), exif_date="2020:07:10 10:00:00", color=(3, 3, 3))
    make_jpg(str(lib / "sif 2020" / "b.jpg"), exif_date="2020:08:10 10:00:00", color=(4, 4, 4))
    make_jpg(str(lib / "2016-2019 zebda" / "c.jpg"), exif_date="2016:01:01 10:00:00", color=(5, 5, 5))
    make_jpg(str(lib / "2016-2019 zebda" / "d.jpg"), exif_date="2019:01:01 10:00:00", color=(6, 6, 6))
    make_jpg(str(lib / "ScreenshotsWoW" / "WoWScrnShot_1.jpg"), color=(7, 7, 7))
    write(str(lib / "Redmi 10 (3)" / ".thumbnails" / "t.jpg"), b"junk")
    write(str(lib / "Redmi 10 (3)" / "DCIM" / "IMG_A.jpg"), b"no date here")
    os.utime(lib / "Redmi 10 (3)" / "DCIM" / "IMG_A.jpg", (1600000000, 1600000000))
    make_jpg(str(lib / "Redmi 10 (4)" / "IMG_20210812_101010.jpg"), color=(9, 9, 9))  # name clash, different photo
    scanner.scan(s, conn=conn)

    plan = organize.build_plan(conn, s)
    dst = {os.path.relpath(i["src"], lib): os.path.relpath(i["dst"], org) for i in plan["items"]}
    J = os.path.join
    assert dst[J("Redmi 10 (3)", "DCIM", "Camera", "IMG_20210812_101010.jpg")] == J("Life", "2021", "2021-08", "IMG_20210812_101010.jpg")
    assert dst[J("Redmi 10 (4)", "IMG_20210812_101010.jpg")] == J("Life", "2021", "2021-08", "IMG_20210812_101010_1.jpg")
    assert dst[J("Redmi 10 (3)", "WhatsApp", "Media", "IMG-20200101-WA0001.jpg")] == J("Chat Media", "WhatsApp", "2020", "IMG-20200101-WA0001.jpg")
    assert dst[J("Redmi 10 (3)", "Pictures", "Screenshots", "Screenshot_2022-03-04-05-06-07.jpg")] == J("Screenshots & Recordings", "2022", "Screenshot_2022-03-04-05-06-07.jpg")
    assert dst[J("sif 2020", "beach", "a.jpg")] == J("Life", "2020", "2020-08 sif 2020", "beach", "a.jpg")
    assert dst[J("2016-2019 zebda", "c.jpg")] == J("Collections", "2016-2019 zebda", "c.jpg")
    assert dst[J("ScreenshotsWoW", "WoWScrnShot_1.jpg")] == J("Gaming", "World of Warcraft", "WoWScrnShot_1.jpg")
    assert dst[J("Redmi 10 (3)", "DCIM", "IMG_A.jpg")] == J("Life", "_No date", "Redmi 10 (3)", "DCIM", "IMG_A.jpg")
    assert J("Redmi 10 (3)", ".thumbnails", "t.jpg") not in dst
    assert plan["skipped_junk"] == 1

    out = organize.apply(conn, s, plan)
    assert out["moved"] == len(plan["items"])
    assert os.path.exists(org / "Life" / "2021" / "2021-08" / "IMG_20210812_101010.jpg")
    assert not os.path.exists(lib / "sif 2020")  # emptied folders removed
    assert conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (str(org) + "%",)).fetchone()[0] == len(plan["items"])
    assert organize.build_plan(conn, s)["items"] == []  # idempotent

    journal.undo(conn, out["op"], s.library_roots)
    assert os.path.exists(lib / "sif 2020" / "beach" / "a.jpg")
    assert not os.path.exists(org / "Life" / "2021")


# ---------- backup ----------
def test_backup_mirror_archive_verify_restore(env):
    s, conn, lib, org, tmp = env
    write(str(org / "Life" / "2021" / "a.jpg"), b"A" * 1000)
    write(str(org / "Life" / "2021" / "b.jpg"), b"B" * 1000)
    r = backup.run(conn, s)
    assert r["copied"] == 2 and r["errors"] == 0, r
    bk = tmp / "Backup" / "Photos"
    assert (bk / "Life" / "2021" / "a.jpg").read_bytes() == b"A" * 1000
    assert (tmp / "Backup" / backup.DB_DIR / "library.db").exists()

    os.remove(org / "Life" / "2021" / "b.jpg")
    write(str(org / "Life" / "2021" / "a.jpg"), b"A2" * 600)
    write(str(org / "Life" / "2022" / "c.jpg"), b"C")
    r = backup.run(conn, s)
    assert (r["copied"], r["updated"], r["archived"]) == (1, 1, 1), r
    assert not (bk / "Life" / "2021" / "b.jpg").exists()
    archived = list((tmp / "Backup" / backup.ARCHIVE).rglob("*.jpg"))
    assert {p.name for p in archived} == {"a.jpg", "b.jpg"}  # old a.jpg version + deleted b.jpg kept
    assert (bk / "Life" / "2021" / "a.jpg").read_bytes() == b"A2" * 600

    v = backup.verify(s, full=True)
    assert v["missing"] == [] and v["different"] == [] and v["checked"] == 2

    assert backup.run(conn, s)["copied"] == 0  # nothing to do
    os.remove(org / "Life" / "2022" / "c.jpg")
    out = backup.restore(s, str(bk / "Life" / "2022" / "c.jpg"))
    assert out == str(org / "Life" / "2022" / "c.jpg") and os.path.exists(out)


def test_backup_refuses_missing_drive(env):
    s, conn, *_ = env
    s.backup_target = r"Q:\NoSuchDisk"
    if os.path.exists("Q:\\"):
        pytest.skip("Q: exists")
    with pytest.raises(RuntimeError):
        backup.run(conn, s)


# ---------- import ----------
def test_import_skips_existing_and_verifies(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "Redmi 10" / "old.jpg"))
    scanner.scan(s, conn=conn)
    phone = tmp / "phone"
    with open(lib / "Redmi 10" / "old.jpg", "rb") as f:
        write(str(phone / "DCIM" / "old_copy.jpg"), f.read())
    make_jpg(str(phone / "DCIM" / "PXL_20251001_101010.jpg"), color=(9, 99, 9))
    make_jpg(str(phone / "DCIM" / "PXL_20251002_101010.jpg"), color=(99, 9, 9))
    r = importer.import_folder(conn, s, str(phone), "Pixel 9")
    assert (r["total"], r["copied"], r["already"]) == (3, 2, 1)
    assert r["safe_to_delete"]
    assert os.path.exists(os.path.join(r["dest"], "DCIM", "PXL_20251001_101010.jpg"))
    assert conn.execute("SELECT COUNT(*) FROM files WHERE top='00_Inbox'").fetchone()[0] == 2
    r2 = importer.import_folder(conn, s, str(phone), "Pixel 9")
    assert r2["copied"] == 0 and r2["already"] == 3


# ---------- health + stats ----------
def test_health_finds_problems(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "A" / "good.jpg"), exif_date="2020:01:01 00:00:00")
    make_jpg(str(lib / "A" / "bad.jpg"), size=(800, 600), pattern=True)
    with open(lib / "A" / "bad.jpg", "r+b") as f:
        f.truncate(os.path.getsize(lib / "A" / "bad.jpg") // 3)
    write(str(lib / "A" / "zero.jpg"), b"")
    write(str(lib / "A" / ".nomedia"), b"")
    os.makedirs(lib / "A" / "empty")
    make_jpg(str(lib / "A" / "tiny.jpg"), size=(64, 64), color=(0, 0, 255))
    scanner.scan(s, conn=conn)
    r = health.scan(conn, s)
    names = lambda k: {os.path.basename(x["path"]) for x in r[k]}
    assert names("corrupt") == {"bad.jpg"}
    assert names("zero") == {"zero.jpg", ".nomedia"}
    assert names("tiny") == {"tiny.jpg"}
    assert any(p.endswith("empty") for p in r["empty_dirs"])
    assert health.remove_empty_dirs(r["empty_dirs"]) == 1


def test_stats_and_report(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "A" / "a.jpg"), exif_date="2020:01:01 00:00:00")
    scanner.scan(s, conn=conn)
    st = stats.collect(conn, s)
    assert st["total_files"] == 1 and st["by_year"][0][0] == "2020"
    stats.export_html(st, str(tmp / "r.html"))
    assert "Photo Vault report" in (tmp / "r.html").read_text(encoding="utf-8")


def test_curated_event_tolerates_a_few_other_years(env):
    s, conn, lib, org, tmp = env
    for i in range(5):
        make_jpg(str(lib / "sif 2021" / f"{i}.jpg"), exif_date=f"2021:08:0{i + 1} 10:00:00", color=(i, 9, 9))
    make_jpg(str(lib / "sif 2021" / "old.jpg"), exif_date="2019:01:01 10:00:00", color=(50, 9, 9))
    scanner.scan(s, conn=conn)
    dsts = {os.path.relpath(i["dst"], org).split(os.sep)[2] for i in organize.build_plan(conn, s)["items"]}
    assert dsts == {"2021-08 sif 2021"}


def test_future_dates_rejected():
    assert dates.parse_exif_date("2099:01:01 00:00:00") is None
    assert dates.from_filename("IMG_20990101_101010.jpg") is None


def test_broken_mtime_does_not_crash():
    assert dates.from_mtime(-2_000_000_000).year < 1970
    assert dates.best_date("x.bin", "other", -5, None)[1] == "mtime"


def test_incremental_scan(env):
    s, conn, lib, org, tmp = env
    make_jpg(str(lib / "A" / "a.jpg"))
    assert scanner.scan(s, conn=conn)["new_or_changed"] == 1
    assert scanner.scan(s, conn=conn)["new_or_changed"] == 0
    os.remove(lib / "A" / "a.jpg")
    assert scanner.scan(s, conn=conn)["removed"] == 1


def test_remove_empty_dirs_never_leaves_root(tmp_path):
    root = tmp_path / "root"
    os.makedirs(root / "a" / "b")
    journal.remove_empty_dirs([str(root / "a" / "b" / "x.jpg")], stop_at=[str(root)])
    assert root.exists() and not (root / "a").exists()


# --------------------------------------------------------------- first-run defaults
@pytest.fixture
def fresh_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "home")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "home" / "settings.json")
    return tmp_path / "home"


def test_fresh_install_uses_per_user_defaults(fresh_home):
    s = config.Settings.load()
    pictures = os.path.join(os.path.expanduser("~"), "Pictures")
    assert s.library_roots == [pictures]
    assert s.organized_root.startswith(pictures)
    assert s.quarantine_dir.startswith(pictures)
    assert s.backup_target == ""
    assert s.setup_complete is False
    blob = " ".join([*s.library_roots, s.organized_root, s.quarantine_dir, *s.backup_sources])
    assert "Library" not in blob and "F:" + chr(92) not in blob
    assert (fresh_home / "settings.json").exists()


def test_existing_settings_json_untouched(fresh_home):
    import json
    fresh_home.mkdir(parents=True)
    legacy = {"library_roots": [r"D:\Pictures\Library"], "organized_root": r"D:\Pictures\Photos",
              "quarantine_dir": r"D:\Q", "backup_sources": [r"D:\Pictures\Photos"],
              "backup_target": r"F:\PhotoVault Backup", "theme": "light"}
    f = fresh_home / "settings.json"
    f.write_text(json.dumps(legacy), encoding="utf-8")
    before = f.read_bytes()
    s = config.Settings.load()
    assert f.read_bytes() == before  # load never rewrites an existing file
    assert s.library_roots == legacy["library_roots"]
    assert s.backup_target == r"F:\PhotoVault Backup"
    assert s.setup_complete is True  # no first-run notice for current users


def test_first_run_notice_clears_after_save(fresh_home):
    s = config.Settings.load()
    assert not s.setup_complete
    s.setup_complete = True
    s.save()
    assert config.Settings.load().setup_complete is True


def test_backup_without_target_is_refused(tmp_path):
    s = config.Settings(backup_sources=[str(tmp_path)], backup_target="")
    ok, msg = backup.target_ready(s)
    assert not ok and "backup folder" in msg
    with pytest.raises(RuntimeError):
        backup.run(db.connect(str(tmp_path / "x.db")), s)


def test_default_layout_scan_skips_quarantine(tmp_path):
    pics = tmp_path / "Pictures"
    make_jpg(str(pics / "a.jpg"))
    write(str(pics / "_Duplicates_To_Review" / "b.jpg"))
    s = config.Settings(library_roots=[str(pics)], organized_root=str(pics / "Photos"),
                        quarantine_dir=str(pics / "_Duplicates_To_Review"))
    assert [os.path.basename(p) for p, _, _ in scanner.walk_roots(s)] == ["a.jpg"]


# ---------- XMP sidecar export ----------
def test_xmp_build_escapes_and_nests():
    import xml.etree.ElementTree as ET
    from photovault.core import xmp
    root = ET.fromstring(xmp.build([("person", "Anna & <Bob>"), ("thing", "a|b"), ("thing", "a|b"), ("event", "x\x01y")]))
    ns = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "dc": "http://purl.org/dc/elements/1.1/",
          "lr": "http://ns.adobe.com/lightroom/1.0/"}
    flat = [li.text for li in root.findall(".//dc:subject//rdf:li", ns)]
    hier = [li.text for li in root.findall(".//lr:hierarchicalSubject//rdf:li", ns)]
    assert flat == ["Anna & <Bob>", "a|b", "xy"]
    assert hier == ["People|Anna & <Bob>", "Things|a/b", "Events|xy"]


def test_xmp_export_skips_existing_and_undo(env):
    from photovault.core import xmp
    s, conn, lib, org, tmp = env
    a, b, c = (str(lib / n) for n in ("a.jpg", "b.jpg", "c.jpg"))
    make_jpg(a)
    make_jpg(b)
    before = {p: open(p, "rb").read() for p in (a, b)}
    write(b + ".xmp", b"someone elses sidecar")
    entries = {a: [("thing", "beach")], b: [("thing", "dog")], c: [("thing", "gone")], str(lib / "d.jpg"): []}
    p = xmp.plan(entries)
    assert (p["create"], p["existing"], p["missing"]) == (1, 1, 1)
    assert not os.path.exists(a + ".xmp")                        # dry run writes nothing

    r = xmp.export(conn, entries)
    assert (r["created"], r["existing"], r["failed"]) == (1, 0, 0)
    assert b"beach" in open(a + ".xmp", "rb").read()
    assert open(b + ".xmp", "rb").read() == b"someone elses sidecar"
    assert not os.path.exists(c + ".xmp")
    assert not [f for f in os.listdir(lib) if f.endswith(".pvtmp")]
    assert {p: open(p, "rb").read() for p in (a, b)} == before   # photos untouched
    assert xmp.export(conn, entries)["created"] == 0              # second run is a no-op

    assert journal.undo(conn, r["op"], s.library_roots).get("error")   # generic undo must not touch it
    res = xmp.undo(conn, s, r["op"])
    assert (res["restored"], res["skipped"]) == (1, 0)
    assert not os.path.exists(a + ".xmp")
    assert open(b + ".xmp", "rb").read() == b"someone elses sidecar"
    q = conn.execute("SELECT * FROM quarantine WHERE reason='xmp_sidecar'").fetchone()
    assert os.path.isfile(q["qpath"]) and q["original"] == a + ".xmp"   # moved, not deleted
    assert xmp.undo(conn, s, r["op"]).get("error")               # can't undo twice


def test_xmp_undo_leaves_edited_sidecar(env):
    from photovault.core import xmp
    s, conn, lib, org, tmp = env
    a = str(lib / "a.jpg")
    make_jpg(a)
    r = xmp.export(conn, {a: [("thing", "beach")]})
    with open(a + ".xmp", "ab") as f:
        f.write(b"<!-- edited by another app -->")
    res = xmp.undo(conn, s, r["op"])
    assert (res["restored"], res["skipped"]) == (0, 1)
    assert os.path.isfile(a + ".xmp")


def test_xmp_nothing_created_is_not_undoable(env):
    from photovault.core import xmp
    s, conn, lib, org, tmp = env
    r = xmp.export(conn, {str(lib / "nope.jpg"): [("thing", "x")]})
    assert r["created"] == 0
    assert conn.execute("SELECT undoable FROM ops WHERE id=?", (r["op"],)).fetchone()[0] == 0
