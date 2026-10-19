"""Regression: AiIndexPage crashed on open when the AI environment was already set up
(EnableAiPanel fired its on_ready callback before the page's `info` label existed)."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from photovault.ui import pages_ai  # noqa: E402


class FakeTasks(QObject):
    busyChanged = Signal(bool)


class FakeController(QObject):
    changed = Signal()
    progress = Signal(str, float, str)
    running = False
    error = ""

    def start(self):
        pass

    def cancel(self):
        pass


def test_ai_index_page_opens_when_ai_is_ready(monkeypatch):
    app = QApplication.instance() or QApplication([])
    ctx = SimpleNamespace(ai=SimpleNamespace(available=True, last_progress=lambda: None),
                          ai_setup=FakeController(), tasks=FakeTasks())
    monkeypatch.setattr(pages_ai.setup_env, "models_ready", lambda: True)
    monkeypatch.setattr(pages_ai, "ai_conn", lambda c: None)
    monkeypatch.setattr(pages_ai.store, "stats", lambda conn: dict(
        embedded=1, faces=2, faces_named=0, people=0, errors=0))
    page = pages_ai.AiIndexPage(ctx)  # used to raise AttributeError: 'AiIndexPage' has no attribute 'info'
    assert "1" in page.info.text() and "ready" in page.info.text()
    page.timer.stop()
    assert app is not None
