"""Read-only browse views over ai.db: a timeline (year > month > event) and places (rounded GPS cells).
No Qt, no numpy, no network: pure queries so it is testable on a generated ai.db. Nothing here writes."""
import math
from datetime import datetime

from . import store

# Place cell sizes in degrees (1 degree of latitude ~ 111 km). Labels are what the UI offers.
CELL_SIZES = (("Region (~100 km)", 1.0), ("Area (~10 km)", 0.1), ("Neighbourhood (~1 km)", 0.01))
DEFAULT_CELL = 0.1
MAX_PLACE_PHOTOS = 20000


def _month_bounds(year: int, month: int) -> tuple:
    a = datetime(year, month, 1).timestamp()
    b = datetime(year + 1, 1, 1).timestamp() if month == 12 else datetime(year, month + 1, 1).timestamp()
    return a, b


def has_data(conn) -> bool:
    """True once at least one indexed photo has a usable date or GPS fix."""
    return conn.execute("SELECT 1 FROM photo WHERE status='ok' AND (taken_ts IS NOT NULL OR lat IS NOT NULL) "
                        "LIMIT 1").fetchone() is not None


def timeline(conn) -> dict:
    """{'years': [{year, n, months: [{month, n, events: [{id, name, n, first_ts, last_ts, cover}]}]}], 'undated': n}.
    Newest first. Month counts are all dated photos taken that month (local time, same as the events);
    an event is listed under the month of its first photo and `cover` is the hash of that photo."""
    months = {}
    for r in conn.execute(
            "SELECT CAST(strftime('%Y', taken_ts, 'unixepoch', 'localtime') AS INTEGER) AS y, "
            "CAST(strftime('%m', taken_ts, 'unixepoch', 'localtime') AS INTEGER) AS m, COUNT(*) AS n "
            "FROM photo WHERE status='ok' AND taken_ts IS NOT NULL GROUP BY y, m"):
        months[(r["y"], r["m"])] = {"month": r["m"], "n": r["n"], "events": []}
    for e in store.list_events(conn):
        if e["first_ts"] is None:
            continue
        d = datetime.fromtimestamp(e["first_ts"])
        cover = conn.execute(
            "SELECT p.hash FROM photo_tag pt JOIN photo p ON p.hash=pt.hash AND p.status='ok' "
            "WHERE pt.tag_id=? AND pt.rejected=0 ORDER BY p.taken_ts, p.hash LIMIT 1", (e["id"],)).fetchone()
        slot = months.setdefault((d.year, d.month), {"month": d.month, "n": 0, "events": []})
        slot["events"].append({"id": e["id"], "name": e["name"], "n": e["n"], "first_ts": e["first_ts"],
                               "last_ts": e["last_ts"], "cover": cover["hash"] if cover else None})
    years = {}
    for (y, _m), slot in months.items():
        slot["events"].sort(key=lambda ev: ev["first_ts"], reverse=True)
        years.setdefault(y, []).append(slot)
    out = [{"year": y, "n": sum(s["n"] for s in sl), "months": sorted(sl, key=lambda s: -s["month"])}
           for y, sl in sorted(years.items(), reverse=True)]
    undated = conn.execute("SELECT COUNT(*) FROM photo WHERE status='ok' AND taken_ts IS NULL").fetchone()[0]
    return {"years": out, "undated": undated}


def month_photos(conn, year: int, month: int) -> list:
    """(hash, path) of dated photos taken in a month, oldest first."""
    a, b = _month_bounds(year, month)
    return [(r["hash"], r["path"]) for r in conn.execute(
        "SELECT hash, path FROM photo WHERE status='ok' AND taken_ts>=? AND taken_ts<? ORDER BY taken_ts, path", (a, b))]


def year_photos(conn, year: int) -> list:
    a, b = datetime(year, 1, 1).timestamp(), datetime(year + 1, 1, 1).timestamp()
    return [(r["hash"], r["path"]) for r in conn.execute(
        "SELECT hash, path FROM photo WHERE status='ok' AND taken_ts>=? AND taken_ts<? ORDER BY taken_ts, path", (a, b))]


def event_photos(conn, tag_id: int) -> list:
    return [(r["hash"], r["path"]) for r in conn.execute(
        "SELECT p.hash, p.path FROM photo_tag pt JOIN photo p ON p.hash=pt.hash AND p.status='ok' "
        "JOIN tag t ON t.id=pt.tag_id AND t.kind='event' WHERE pt.tag_id=? AND pt.rejected=0 "
        "ORDER BY p.taken_ts, p.path", (tag_id,))]


def cell_of(lat: float, lon: float, cell: float) -> tuple:
    """Integer grid cell (row, col) of a coordinate. Cells are `cell` degrees wide, aligned to 0/0."""
    return math.floor(lat / cell), math.floor(lon / cell)


def cell_label(i: int, j: int, cell: float) -> str:
    """Centre of the cell as '48.15N 11.65E' (decimals follow the cell size)."""
    dec = max(0, math.ceil(-math.log10(cell)) + 1)
    lat, lon = (i + 0.5) * cell, (j + 0.5) * cell
    return f"{abs(lat):.{dec}f}{'N' if lat >= 0 else 'S'} {abs(lon):.{dec}f}{'E' if lon >= 0 else 'W'}"


def places(conn, cell: float = DEFAULT_CELL) -> dict:
    """{'cells': [{i, j, label, lat, lon, n, cover, first_ts, last_ts}], 'no_gps': n}, biggest cell first.
    Photos without a GPS fix are only counted (`no_gps`); invalid coordinates are ignored."""
    if cell <= 0:
        raise ValueError("cell size must be positive")
    acc = {}
    for r in conn.execute("SELECT hash, lat, lon, taken_ts FROM photo "
                          "WHERE status='ok' AND lat IS NOT NULL AND lon IS NOT NULL ORDER BY taken_ts, hash"):
        lat, lon = r["lat"], r["lon"]
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        c = acc.setdefault(cell_of(lat, lon, cell), {"n": 0, "cover": r["hash"], "first_ts": None, "last_ts": None})
        c["n"] += 1
        ts = r["taken_ts"]
        if ts is not None:
            c["first_ts"] = ts if c["first_ts"] is None else min(c["first_ts"], ts)
            c["last_ts"] = ts if c["last_ts"] is None else max(c["last_ts"], ts)
    cells = [{"i": i, "j": j, "label": cell_label(i, j, cell), "lat": (i + 0.5) * cell, "lon": (j + 0.5) * cell, **c}
             for (i, j), c in acc.items()]
    cells.sort(key=lambda c: (-c["n"], c["label"]))
    no_gps = conn.execute("SELECT COUNT(*) FROM photo WHERE status='ok' AND (lat IS NULL OR lon IS NULL)").fetchone()[0]
    return {"cells": cells, "no_gps": no_gps}


def cell_photos(conn, i: int, j: int, cell: float = DEFAULT_CELL) -> list:
    """(hash, path) of photos in one grid cell, oldest first. Uses the same floor() rule as `places`."""
    lat0, lon0 = i * cell, j * cell
    pad = cell * 1e-6  # coarse SQL window, exact membership is decided by cell_of
    rows = conn.execute(
        "SELECT hash, path, lat, lon FROM photo WHERE status='ok' AND lat>=? AND lat<? AND lon>=? AND lon<? "
        "ORDER BY taken_ts, path", (lat0 - pad, lat0 + cell + pad, lon0 - pad, lon0 + cell + pad)).fetchall()
    return [(r["hash"], r["path"]) for r in rows if cell_of(r["lat"], r["lon"], cell) == (i, j)][:MAX_PLACE_PHOTOS]
