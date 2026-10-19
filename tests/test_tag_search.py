"""Tag filter / tag helper tests on synthetic ai.db data (no models, network or real library)."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xxhash")
pytest.importorskip("PIL")

from photovault.ai import search, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "data" / "ai.db")
    for i, h in enumerate("abcd"):
        c.execute("INSERT INTO photo(hash,path,date_taken,taken_ts) VALUES (?,?,?,?)",
                  (h, f"/x/{h}.jpg", f"2024-0{i + 1}-01", 1_700_000_000.0 + i * 86400))
        c.execute("INSERT INTO embedding(hash,vec) VALUES (?,?)", (h, store.vec_to_blob(np.eye(4)[i])))
    c.commit()
    beach = store.get_or_create_tag(c, "Beach", "thing")
    night = store.get_or_create_tag(c, "night", "thing")
    for h in "abc":
        store.add_photo_tag(c, h, beach, "auto", 0.4)
    for h in "bd":
        store.add_photo_tag(c, h, night, "manual")
    store.reject_photo_tag(c, "c", beach)
    yield c
    c.close()


def hashes(res):
    return {r[0] for r in res}


def test_tag_filter_and_semantics(conn):
    ix = search.Index(conn)
    assert hashes(ix.search(tags=["beach"])) == {"a", "b"}          # case-insensitive, rejection excluded
    assert hashes(ix.search(tags=["beach", "night"])) == {"b"}      # AND
    assert ix.search(tags=["nonexistent"]) == []
    assert hashes(ix.search()) == set("abcd")                       # no tags -> no filter
    vec = np.eye(4)[1].astype(np.float32)
    res = ix.search(vec, tags=["night"])
    assert [r[0] for r in res][0] == "b" and hashes(res) == {"b", "d"}


def test_tag_filter_reload_and_year(conn):
    ix = search.Index(conn)
    store.add_photo_tag(conn, "d", store.get_or_create_tag(conn, "beach", "thing"), "manual")
    assert "d" not in hashes(ix.search(tags=["beach"]))             # stale until reload
    ix.reload()
    assert hashes(ix.search(tags=["beach"], year=2024)) == {"a", "b", "d"}
    assert ix.search(tags=["beach"], year=1999) == []


def test_match_tag_names(conn):
    found, rest = store.match_tag_names(conn, "sunset BEACH at night")
    assert sorted(found) == ["Beach", "night"] and rest == "sunset at"
    assert store.match_tag_names(conn, "beachball") == ([], "beachball")
    assert store.match_tag_names(conn, "dogs") == ([], "dogs")


def test_accept_and_counts(conn):
    beach = conn.execute("SELECT id FROM tag WHERE name='Beach'").fetchone()["id"]
    rows = {r["name"]: r for r in store.tag_counts(conn)}
    assert rows["Beach"]["n"] == 2 and rows["Beach"]["n_auto"] == 2
    assert store.accept_all_above(conn, beach, 0.5) == 0
    assert store.accept_all_above(conn, beach, 0.3) == 2
    assert {r["name"]: r for r in store.tag_counts(conn)}["Beach"]["n_auto"] == 0
    assert [r["source"] for r in store.tag_photos(conn, beach)] == ["manual", "manual"]
    store.accept_photo_tag(conn, "c", beach)                        # accepting revives a rejected tag
    assert len(store.tag_photos(conn, beach)) == 3


def test_list_events(conn):
    from photovault.ai import events
    assert events.recompute(conn) >= 1
    ev = store.list_events(conn)
    assert sum(e["n"] for e in ev) == 4 and ev[0]["first_ts"] <= ev[0]["last_ts"]
