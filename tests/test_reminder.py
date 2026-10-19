import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from photovault.core import config, db  # noqa: E402
from photovault.ui import reminder  # noqa: E402


class FakeWindow:
    def __init__(self, settings, conn):
        self.ctx = type("C", (), {"settings": settings, "conn": conn})()
        self.opened = 0

    def open_backup(self):
        self.opened += 1


def test_check_skips_when_not_due_or_tray_missing(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    conn = db.connect(tmp_path / "t.db")
    s = config.Settings(backup_target=r"F:\Backup")
    db.set_meta(conn, "last_backup", time.time())
    w = FakeWindow(s, conn)
    r = reminder.BackupReminder(w)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    assert r.check() is False  # backed up just now
    db.set_meta(conn, "last_backup", time.time() - 90 * 86400)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    assert r.check() is False  # overdue but no tray


def test_mute_turns_notifications_off_and_persists(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    s = config.Settings(backup_target=r"F:\Backup")
    r = reminder.BackupReminder(FakeWindow(s, None))
    r.mute()
    assert s.backup_notify is False
    assert config.Settings.load().backup_notify is False
