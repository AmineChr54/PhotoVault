"""Regression (#44): the Enable AI features button passed clicked(bool) as the setup factory and did nothing."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from photovault.ui import pages_ai  # noqa: E402


class FakeSetup:
    def __init__(self, progress=None):
        self.progress = progress

    def run(self):
        pass

    def cancel(self):
        pass


def test_enable_button_starts_setup(monkeypatch):
    QApplication.instance() or QApplication([])
    started = []
    monkeypatch.setattr(pages_ai.setup_env, "AiSetup", lambda progress=None: started.append(1) or FakeSetup(progress))
    monkeypatch.setattr(pages_ai.setup_env, "models_ready", lambda: False)
    ctx = SimpleNamespace(ai=SimpleNamespace(available=False))
    panel = pages_ai.EnableAiPanel(ctx)
    panel.go_btn.click()
    assert started and panel.ctl.setup is not None


def test_start_failure_is_reported_not_stuck():
    ctl = pages_ai.SetupController()

    def boom(progress=None):
        raise RuntimeError("nope")
    ctl.start(boom)
    assert not ctl.running and "nope" in ctl.error
