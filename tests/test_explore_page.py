"""Timeline & Places page: offscreen Qt, generated ai.db, fake ctx (no AI worker, models or real library)."""
import os
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from photovault.ai import events, store  # noqa: E402
from photovault.ui import pages_explore  # noqa: E402
from photovault.ui.pages_c import BrowsePage  # noqa: E402


class FakeTasks(QObject):
    busyChanged = Signal(bool)


class FakeThumbs(QObject):
    ready = Signal(str)

    def get(self, path):
        return None

    def clear_queue(self):
        pass


class FakeMain:
    def __init__(self):
        self.opened = None

    def open_browse_paths(self, paths, title=""):
        self.opened = (list(paths), title)


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    conn = store.connect(tmp_path / "ai.db")
    ctx = SimpleNamespace(tasks=FakeTasks(), thumbs=FakeThumbs(), main=FakeMain())
    monkeypatch.setattr(pages_explore, "ai_conn", lambda c: conn)
    yield app, ctx, conn
    conn.close()


def add(conn, h, y, m, d, lat=None, lon=None):
    conn.execute("INSERT INTO photo(hash,path) VALUES (?,?)", (h, f"/lib/{h}.jpg"))
    store.set_photo_geo_date(conn, h, lat, lon, datetime(y, m, d, 12).timestamp())


def test_empty_index_shows_hint(env):
    _, ctx, _ = env
    page = pages_explore.ExplorePage(ctx)
    page.on_show()
    assert "AI Index" in page.status.text()
    assert page.timeline_tree.topLevelItemCount() == 0 and page.places_tree.topLevelItemCount() == 0


def test_timeline_and_places_open_in_browse(env):
    _, ctx, conn = env
    add(conn, "a", 2023, 7, 1, 48.13, 11.58)
    add(conn, "b", 2023, 7, 1, 48.14, 11.59)
    add(conn, "c", 2023, 8, 9, 50.1, 8.7)
    page = pages_explore.ExplorePage(ctx)
    page.on_show()                                   # no events yet: months only, with a hint
    assert "Compute events" in page.status.text()
    tree = page.timeline_tree
    assert tree.topLevelItemCount() == 1
    year = tree.topLevelItem(0)
    assert year.text(0) == "2023" and year.text(1) == "3" and year.childCount() == 2
    assert not page.t_open.isEnabled()
    tree.setCurrentItem(year.child(1))               # July (August is newest, listed first)
    assert page.t_open.isEnabled()
    page.open_selected(tree)
    assert ctx.main.opened[1] == "July 2023" and sorted(ctx.main.opened[0]) == ["/lib/a.jpg", "/lib/b.jpg"]

    events.recompute(conn)
    page.refresh()
    assert page.status.text() == ""
    jul = tree.topLevelItem(0).child(1)
    assert jul.childCount() == 1 and jul.child(0).text(1) == "2"
    tree.setCurrentItem(jul.child(0))
    page.open_selected(tree)
    assert ctx.main.opened[1] == "Event" and len(ctx.main.opened[0]) == 2

    places = page.places_tree
    assert places.topLevelItemCount() == 2
    assert places.topLevelItem(0).text(1) == "2"
    places.setCurrentItem(places.topLevelItem(0))
    page.open_selected(places)
    assert ctx.main.opened[1] == "Place" and sorted(ctx.main.opened[0]) == ["/lib/a.jpg", "/lib/b.jpg"]
    page.cell.setCurrentIndex(page.cell.findData(1.0))
    assert page.places_tree.topLevelItemCount() == 2
    assert page.places_tree.topLevelItem(0).data(0, Qt.UserRole)[0] == "cell"


def test_browse_show_paths(env):
    """BrowsePage.show_paths lists known library files and falls back to the file name for unknown ones."""
    _, ctx, _ = env
    lib = sqlite3.connect(":memory:")
    lib.row_factory = sqlite3.Row
    lib.execute("CREATE TABLE files (path TEXT, name TEXT, date_taken TEXT, kind TEXT, top TEXT, date_src TEXT)")
    lib.execute("INSERT INTO files VALUES ('/lib/a.jpg','a.jpg','2023-07-01','image','lib','exif')")
    ctx.conn = lib
    page = BrowsePage(ctx)
    page.show_paths(["/lib/a.jpg", "/elsewhere/z.jpg"], "July 2023")
    assert page.grid.count() == 2
    assert page.grid.item(0).text().startswith("a.jpg")
    assert page.grid.item(1).text().startswith("z.jpg")
    assert "July 2023" in page.count.text()
