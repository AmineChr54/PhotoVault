"""Startup notification when the last backup is older than the reminder setting."""
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core import db, stats

SHOW_FOR_MS = 120_000  # the tray icon is only there so the toast can be shown; remove it afterwards


class BackupReminder:
    """One toast per app start (QSystemTrayIcon.showMessage, no extra dependency).

    Clicking the toast, or the tray menu's "Open Backup", jumps to the Backup page;
    "Do not remind me again" switches `backup_notify` off (re-enable it in Settings).
    """

    def __init__(self, window):
        self.window = window
        self.tray = None

    def check(self) -> bool:
        """Show the toast if a reminder is due and the system tray is available. Returns True if shown."""
        ctx = self.window.ctx
        text = stats.reminder_text(ctx.settings, _last_backup(ctx.conn))
        if text is None or not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        tray = QSystemTrayIcon(self.window.windowIcon(), self.window)
        menu = QMenu()
        open_act = QAction("Open Backup", menu)
        open_act.triggered.connect(self.window.open_backup)
        mute_act = QAction("Do not remind me again", menu)
        mute_act.triggered.connect(self.mute)
        menu.addAction(open_act)
        menu.addAction(mute_act)
        tray.setContextMenu(menu)
        tray.messageClicked.connect(self.window.open_backup)
        tray.setToolTip("Photo Vault")
        self._menu, self.tray = menu, tray
        tray.show()
        tray.showMessage("Time for a backup", text + " Click to open Backup.", QSystemTrayIcon.Information, 15000)
        QTimer.singleShot(SHOW_FOR_MS, self.hide)
        return True

    def mute(self):
        self.window.ctx.settings.backup_notify = False
        self.window.ctx.settings.save()
        self.hide()

    def hide(self):
        if self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None


def _last_backup(conn):
    v = db.get_meta(conn, "last_backup")
    return float(v) if v else None
