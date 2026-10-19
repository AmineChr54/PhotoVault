"""Auto events tests: synthetic timestamps only, no models, network or real library."""
import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xxhash")
pytest.importorskip("PIL")

from photovault.ai import events, store  # noqa: E402

H = 3600.0
T0 = 1_700_000_000.0


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "data" / "ai.db")
    yield c
    c.close()


def _add_photo(conn, h, ts, lat=None, lon=None):
    conn.execute("INSERT INTO photo(hash,path) VALUES (?,?)", (h, f"/x/{h}.jpg"))
    store.set_photo_geo_date(conn, h, lat, lon, ts)


def _members(conn):
    return {t["name"]: set(store.photos_for_tag(conn, t["id"]))
            for t in conn.execute("SELECT id,name FROM tag WHERE kind='event'").fetchall()}


def test_split_on_gap_and_skip_undated(conn):
    for h, ts in (("a", T0), ("b", T0 + H), ("c", T0 + 3 * H), ("d", T0 + 20 * H), ("e", T0 + 21 * H)):
        _add_photo(conn, h, ts)
    _add_photo(conn, "u", None)
    assert events.recompute(conn) == 2
    assert sorted(_members(conn).values(), key=sorted) == [{"a", "b", "c"}, {"d", "e"}]
    assert store.tags_for_photo(conn, "u") == []


def test_split_on_gps_distance(conn):
    _add_photo(conn, "a", T0, 48.1, 11.6)         # Munich
    _add_photo(conn, "b", T0 + H, 48.2, 11.7)
    _add_photo(conn, "c", T0 + 2 * H, 50.1, 8.7)  # Frankfurt, ~300 km
    assert events.haversine_km(48.1, 11.6, 50.1, 8.7) > 250
    assert events.recompute(conn) == 2
    assert events.recompute(conn, split_km=0) == 1


def test_name_uses_top_thing_tag_else_place(conn):
    _add_photo(conn, "a", T0, 48.1, 11.6)
    _add_photo(conn, "b", T0 + 100 * H)
    store.add_photo_tag(conn, "b", store.get_or_create_tag(conn, "beach", "thing"))
    events.recompute(conn)
    names = list(_members(conn))
    assert any(n.endswith("48.1N 11.6E") for n in names)
    assert any(n.endswith("- beach") for n in names)


def test_recompute_keeps_manual_and_is_idempotent(conn):
    for h, ts in (("a", T0), ("b", T0 + H), ("c", T0 + 50 * H)):
        _add_photo(conn, h, ts)
    events.recompute(conn)
    tid = next(t["id"] for t in conn.execute("SELECT id FROM tag WHERE kind='event'").fetchall()
               if "a" in store.photos_for_tag(conn, t["id"]))
    events.rename_event(conn, tid, "Italy trip")
    assert events.recompute(conn) == 1  # only the auto event for "c" is rebuilt
    members = _members(conn)
    assert members["Italy trip"] == {"a", "b"}
    assert sum(len(v) for v in members.values()) == 3
    assert events.recompute(conn) == 1
    assert len(_members(conn)) == 2
