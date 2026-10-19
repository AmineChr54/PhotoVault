"""Auto events: group photos into events by time gaps (and optionally GPS jumps).
Events are `tag` rows of kind 'event'; membership is photo_tag with source='auto'.
Photos the user placed in an event by hand (source='manual') and renamed events are preserved."""
import math
from datetime import datetime

from . import store

GAP_HOURS = 7.0        # a gap longer than this starts a new event
SPLIT_KM = 50.0        # a GPS jump longer than this starts a new event (when both photos have GPS)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def split_events(rows, gap_hours=GAP_HOURS, split_km=SPLIT_KM) -> list:
    """rows: (hash, taken_ts, lat, lon). Returns lists of rows (sorted by time); rows without taken_ts are skipped."""
    rows = sorted((r for r in rows if r[1] is not None), key=lambda r: (r[1], r[0]))
    groups, cur, last_geo = [], [], None
    for r in rows:
        if cur:
            split = r[1] - cur[-1][1] > gap_hours * 3600
            if not split and split_km and last_geo and r[2] is not None and r[3] is not None:
                split = haversine_km(last_geo[0], last_geo[1], r[2], r[3]) > split_km
            if split:
                groups.append(cur)
                cur, last_geo = [], None
        cur.append(r)
        if r[2] is not None and r[3] is not None:
            last_geo = (r[2], r[3])
    if cur:
        groups.append(cur)
    return groups


def _date_range(group) -> str:
    a = datetime.fromtimestamp(group[0][1]).date()
    b = datetime.fromtimestamp(group[-1][1]).date()
    return a.isoformat() if a == b else f"{a.isoformat()} to {b.isoformat()}"


def _place(group):
    pts = [(r[2], r[3]) for r in group if r[2] is not None and r[3] is not None]
    if not pts:
        return None
    lat = sum(p[0] for p in pts) / len(pts)
    lon = sum(p[1] for p in pts) / len(pts)
    return f"{abs(lat):.1f}{'N' if lat >= 0 else 'S'} {abs(lon):.1f}{'E' if lon >= 0 else 'W'}"


def _top_tag(conn, group):
    marks = ",".join("?" * len(group))
    row = conn.execute(
        f"SELECT t.name FROM photo_tag pt JOIN tag t ON t.id=pt.tag_id WHERE t.kind='thing' AND pt.rejected=0 "
        f"AND pt.hash IN ({marks}) GROUP BY t.id ORDER BY COUNT(*) DESC, t.name LIMIT 1",
        [r[0] for r in group]).fetchone()
    return row["name"] if row else None


def event_name(conn, group) -> str:
    label = _top_tag(conn, group) or _place(group)
    base = _date_range(group)
    return f"{base} - {label}" if label else base


def rename_event(conn, tag_id, new_name):
    """User renames an event: the name sticks and all its photos become manual, so recompute keeps them."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("empty event name")
    conn.execute("UPDATE tag SET name=? WHERE id=? AND kind='event'", (new_name, tag_id))
    conn.execute("UPDATE photo_tag SET source='manual' WHERE tag_id=? AND rejected=0", (tag_id,))
    conn.commit()


def recompute(conn, gap_hours=GAP_HOURS, split_km=SPLIT_KM) -> int:
    """Rebuild auto events. Returns the number of auto events written."""
    # drop old auto memberships, then event tags nobody references any more
    conn.execute("DELETE FROM photo_tag WHERE source='auto' AND tag_id IN (SELECT id FROM tag WHERE kind='event')")
    conn.execute("DELETE FROM tag WHERE kind='event' AND id NOT IN (SELECT tag_id FROM photo_tag)")
    conn.commit()
    manual = {r["hash"] for r in conn.execute(
        "SELECT pt.hash FROM photo_tag pt JOIN tag t ON t.id=pt.tag_id "
        "WHERE t.kind='event' AND pt.source='manual' AND pt.rejected=0")}
    rows = [(r["hash"], r["taken_ts"], r["lat"], r["lon"]) for r in conn.execute(
        "SELECT hash, taken_ts, lat, lon FROM photo WHERE status='ok' AND taken_ts IS NOT NULL")
        if r["hash"] not in manual]
    n = 0
    for group in split_events(rows, gap_hours, split_km):
        name = event_name(conn, group)
        final, i = name, 2
        while conn.execute("SELECT 1 FROM tag WHERE kind='event' AND name=?", (final,)).fetchone():
            final, i = f"{name} ({i})", i + 1
        tid = store.get_or_create_tag(conn, final, "event")
        for r in group:
            store.add_photo_tag(conn, r[0], tid, "auto")
        n += 1
    return n
