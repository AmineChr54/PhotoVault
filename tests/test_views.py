"""Timeline / Places queries over a generated ai.db: synthetic rows only, no models, network or real library."""
from datetime import datetime

import pytest

from photovault.ai import events, store, views


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "data" / "ai.db")
    yield c
    c.close()


def ts(y, m, d, h=12):
    return datetime(y, m, d, h).timestamp()


def add(conn, h, taken=None, lat=None, lon=None, status="ok"):
    conn.execute("INSERT INTO photo(hash,path,status) VALUES (?,?,?)", (h, f"/x/{h}.jpg", status))
    store.set_photo_geo_date(conn, h, lat, lon, taken)


def test_empty_db_has_no_data_and_empty_views(conn):
    assert not views.has_data(conn)
    assert views.timeline(conn) == {"years": [], "undated": 0}
    assert views.places(conn) == {"cells": [], "no_gps": 0}


def test_timeline_groups_year_month_event_newest_first(conn):
    add(conn, "a", ts(2023, 7, 1))
    add(conn, "b", ts(2023, 7, 1, 14))
    add(conn, "c", ts(2023, 7, 20))
    add(conn, "d", ts(2024, 1, 5))
    add(conn, "u", None)                                # undated: counted, not listed
    add(conn, "bad", ts(2024, 1, 6), status="error")    # not ok: ignored
    assert views.has_data(conn)
    assert events.recompute(conn) == 3
    t = views.timeline(conn)
    assert [y["year"] for y in t["years"]] == [2024, 2023]
    assert t["undated"] == 1
    y23 = t["years"][1]
    assert y23["n"] == 3 and [m["month"] for m in y23["months"]] == [7]
    jul = y23["months"][0]
    assert jul["n"] == 3
    assert [e["n"] for e in jul["events"]] == [1, 2]            # newest event first
    assert jul["events"][1]["cover"] == "a"                     # earliest photo of the event
    assert t["years"][0]["months"][0]["n"] == 1                 # error photo not counted


def test_timeline_without_events_still_lists_months(conn):
    add(conn, "a", ts(2022, 12, 31))
    t = views.timeline(conn)
    assert t["years"][0]["months"][0] == {"month": 12, "n": 1, "events": []}


def test_month_year_and_event_photos(conn):
    add(conn, "dec", ts(2022, 12, 31, 23))
    add(conn, "jan", ts(2023, 1, 1, 0))
    add(conn, "jan2", ts(2023, 1, 31, 23))
    add(conn, "feb", ts(2023, 2, 1, 0))
    assert [h for h, _ in views.month_photos(conn, 2022, 12)] == ["dec"]
    assert [h for h, _ in views.month_photos(conn, 2023, 1)] == ["jan", "jan2"]
    assert [h for h, _ in views.year_photos(conn, 2023)] == ["jan", "jan2", "feb"]
    events.recompute(conn)
    ev = views.timeline(conn)["years"][0]["months"][-1]["events"][0]
    assert views.event_photos(conn, ev["id"])
    assert views.event_photos(conn, 99999) == []


def test_places_group_by_cell_and_count_no_gps(conn):
    add(conn, "m1", ts(2023, 1, 1), 48.13, 11.58)    # Munich
    add(conn, "m2", ts(2023, 1, 3), 48.14, 11.59)
    add(conn, "f1", ts(2023, 2, 1), 50.11, 8.68)     # Frankfurt
    add(conn, "s1", ts(2023, 3, 1), -33.86, 151.21)  # Sydney (negative latitude)
    add(conn, "n1", ts(2023, 3, 2))
    add(conn, "inv", ts(2023, 3, 3), 95.0, 10.0)     # invalid latitude: ignored
    p = views.places(conn, 0.1)
    assert p["no_gps"] == 1
    assert [c["n"] for c in p["cells"]] == [2, 1, 1]
    top = p["cells"][0]
    assert top["cover"] == "m1" and top["first_ts"] == ts(2023, 1, 1) and top["last_ts"] == ts(2023, 1, 3)
    assert top["label"] == "48.15N 11.55E"
    assert any(c["label"].split()[0].endswith("S") for c in p["cells"])
    assert len(views.places(conn, 10.0)["cells"]) == 3  # 40-50N/10E, 50-60N/0E, 40S/150E


def test_cell_photos_matches_places_cells(conn):
    add(conn, "m1", ts(2023, 1, 1), 48.13, 11.58)
    add(conn, "m2", ts(2023, 1, 2), 48.14, 11.59)
    add(conn, "f1", ts(2023, 2, 1), 50.11, 8.68)
    add(conn, "s1", ts(2023, 3, 1), -33.86, 151.21)
    for cell in (1.0, 0.1, 0.01):
        total = 0
        for c in views.places(conn, cell)["cells"]:
            got = views.cell_photos(conn, c["i"], c["j"], cell)
            assert len(got) == c["n"]
            total += len(got)
        assert total == 4


def test_cell_edges_are_consistent(conn):
    # 48.3 / 0.1 is 482.99999999999994 in floating point: places() and cell_photos() must agree anyway
    add(conn, "e", ts(2023, 1, 1), 48.3, 11.0)
    c = views.places(conn, 0.1)["cells"][0]
    assert [h for h, _ in views.cell_photos(conn, c["i"], c["j"], 0.1)] == ["e"]


def test_bad_cell_size_rejected(conn):
    with pytest.raises(ValueError):
        views.places(conn, 0)


def test_views_never_write(conn):
    add(conn, "a", ts(2023, 1, 1), 48.1, 11.6)
    before = conn.total_changes
    views.timeline(conn)
    views.places(conn)
    views.cell_photos(conn, 481, 116)
    views.month_photos(conn, 2023, 1)
    assert conn.total_changes == before
