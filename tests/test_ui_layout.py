"""Regressions found by the real-display UI smoke (#12): clipped Tags header, zoom message overlapping the status label."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    from photovault.app import MainWindow
    from photovault.core import config
    from photovault.ui.common import apply_theme

    app = QApplication.instance() or QApplication([])
    s = config.Settings.load()
    apply_theme(app, s.theme, s.ui_zoom)
    w = MainWindow(s)
    w.resize(1400, 900)
    w.show()
    deadline = time.time() + 20
    while w.ctx.tasks.busy and time.time() < deadline:  # the startup scan must finish first
        app.processEvents()
        time.sleep(0.02)
    yield w
    w.close()


def test_zoom_message_uses_status_label_not_overlay(window):
    window.set_zoom(1.5)
    assert window.ctx.tasks.label.text() == "Zoom 150%"
    assert window.statusBar().currentMessage() == ""  # an overlay would collide with the label
    window.set_zoom(1.0)


def test_tags_tree_headers_not_clipped(window):
    from photovault.app import PAGES

    idx = next(i for i, (_, cls) in enumerate(PAGES) if cls.__name__ == "TagsPage")
    window.nav.setCurrentRow(idx)
    QApplication.processEvents()
    tree = window.pages[idx].tree
    for col in range(tree.columnCount()):
        text = tree.headerItem().text(col)
        assert tree.columnWidth(col) >= tree.fontMetrics().horizontalAdvance(text), text
