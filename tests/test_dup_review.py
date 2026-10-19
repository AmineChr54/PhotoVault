"""Duplicates page: keyboard review (shortcuts, counter) and auto-pick, on a headless GroupView."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from photovault.core import config  # noqa: E402
from photovault.ui import pages_a  # noqa: E402


class FakeTasks(QObject):
    busyChanged = Signal(bool)
    busy = False


class FakeThumbs(QObject):
    ready = Signal(str)

    def get(self, path):
        return None


def _file(tmp, top, name, size=100, w=800, h=600, date="2021-01-01 10:00:00"):
    return {"path": str(tmp / top / name), "top": top, "name": name, "rel": f"{top}/{name}", "size": size,
            "width": w, "height": h, "date_taken": date, "date_src": "exif"}


@pytest.fixture
def view(tmp_path):
    app = QApplication.instance() or QApplication([])
    s = config.Settings(library_roots=[str(tmp_path)], organized_root=str(tmp_path / "Photos"),
                        quarantine_dir=str(tmp_path / "_Q"))
    ctx = SimpleNamespace(settings=s, tasks=FakeTasks(), thumbs=FakeThumbs(), conn=None)
    runs = []
    page = SimpleNamespace(ctx=ctx, actions=[], run=lambda title, fn, done=None: runs.append(title))
    v = pages_a.GroupView(page, similar=True)
    groups = [
        {"key": f"g{i}", "wasted": 100 * (i + 1), "keep": 0,
         "files": [_file(tmp_path, "A", f"a{i}.jpg"), _file(tmp_path, "A", f"b{i}.jpg", w=1600, h=1200)]}
        for i in range(3)]
    v.loaded(groups)
    v._runs = runs
    v._app = app
    return v


def test_counter_and_group_navigation(view):
    assert "group 1 of 3" in view.summary.text() and "to reclaim" in view.summary.text()
    view.step_group(1)
    assert view.list.currentRow() == 1 and "group 2 of 3" in view.summary.text()
    view.step_group(5)  # clamped at the end
    view.step_group(1)
    assert view.list.currentRow() == 2
    view.step_group(-1)
    assert view.list.currentRow() == 1
    view.list.setCurrentRow(0)
    view.step_group(-1)
    assert view.list.currentRow() == 0


def test_number_keys_pick_the_copy_to_keep(view):
    view.set_keep(1)
    assert view.shown[0]["keep"] == 1
    assert view.keep_group.checkedId() == 1
    assert "b0.jpg" in view.list.item(0).text()
    view.set_keep(7)  # no such copy -> ignored
    assert view.shown[0]["keep"] == 1


def test_shortcuts_are_registered_with_the_documented_keys(view):
    keys = {sc.key().toString() for sc in view.findChildren(QShortcut)}
    assert {"Up", "Down", "Return", "Enter", "I", "O"} | {str(n) for n in range(1, 10)} <= keys
    assert all(sc.context() == Qt.WidgetWithChildrenShortcut for sc in view.findChildren(QShortcut))
    assert QKeySequence(Qt.Key_Up).toString() == "Up"


def test_enter_quarantines_only_when_idle(view):
    view.ctx.tasks.busy = True
    view.shortcut_quarantine()
    assert view._runs == []
    view.ctx.tasks.busy = False
    view.shortcut_quarantine()
    assert view._runs == ["Moving to quarantine"]


def test_auto_pick_current_and_all(view, monkeypatch):
    view.auto_pick_current()  # the 1600x1200 copy (index 1) is better
    assert view.shown[0]["keep"] == 1
    monkeypatch.setattr(pages_a, "confirm", lambda *a, **k: True)
    view.auto_pick_all()
    assert [g["keep"] for g in view.shown] == [1, 1, 1]
    assert all("b" in view.list.item(i).text() for i in range(3))
    seen = []
    monkeypatch.setattr(pages_a, "info", lambda *a, **k: seen.append(a[2]))
    view.auto_pick_all()  # nothing left to change
    assert seen and "already the best" in seen[0]
    assert view._runs == []  # picking never moves anything


def test_auto_pick_all_declined_changes_nothing(view, monkeypatch):
    monkeypatch.setattr(pages_a, "confirm", lambda *a, **k: False)
    view.auto_pick_all()
    assert [g["keep"] for g in view.shown] == [0, 0, 0]
