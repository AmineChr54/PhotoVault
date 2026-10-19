"""Photo Vault - main window."""
import os
import sys
import threading

from PySide6.QtCore import QObject, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QProgressBar,
                               QPushButton, QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from . import __version__
from .core import config, db, quarantine, scanner, updates
from .ui import theme
from .ui.common import Tasks, ThumbCache, apply_theme
from .ui.nav import ICON_ROLE, NavDelegate
from .ui.pages_a import DashboardPage, DuplicatesPage
from .ui.pages_ai import AiIndexPage, PeoplePage, SearchPage, TagsPage
from .ui.pages_b import BackupPage, ImportPage, OrganizePage
from .ui.pages_c import BrowsePage, HealthPage, LogPage, QuarantinePage, SettingsPage
from .ui.pages_help import AboutDialog, HelpPage

PAGES = [
    ("Dashboard", DashboardPage),
    ("Duplicates", DuplicatesPage),
    ("Organize", OrganizePage),
    ("Import", ImportPage),
    ("Backup", BackupPage),
    ("Health check", HealthPage),
    ("Browse", BrowsePage),
    ("Search", SearchPage),
    ("People", PeoplePage),
    ("Tags", TagsPage),
    ("AI Index", AiIndexPage),
    ("Quarantine", QuarantinePage),
    ("Activity log", LogPage),
    ("Settings", SettingsPage),
    ("Help", HelpPage),
]
PAGE_ICONS = {
    DashboardPage: "dashboard", DuplicatesPage: "duplicates", OrganizePage: "organize", ImportPage: "import",
    BackupPage: "backup", HealthPage: "health", BrowsePage: "browse", SearchPage: "search", PeoplePage: "people",
    TagsPage: "tags", AiIndexPage: "ai", QuarantinePage: "quarantine", LogPage: "log", SettingsPage: "settings",
    HelpPage: "help",
}


class Ctx:
    def __init__(self, settings, conn, tasks, thumbs):
        self.settings, self.conn, self.tasks, self.thumbs = settings, conn, tasks, thumbs


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)


class _UpdateSignals(QObject):
    done = Signal(object)  # latest release tag, or None when the check failed


class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.setWindowTitle("Photo Vault")
        self._restore_geometry()
        conn = db.connect()
        quarantine.import_legacy_log(conn, settings)

        status = QStatusBar()
        self.setStatusBar(status)
        label = QLabel()
        bar = QProgressBar()
        self._bar = bar
        cancel = QPushButton("Cancel")
        status.addWidget(label, 1)
        status.addPermanentWidget(bar)
        status.addPermanentWidget(cancel)
        self.ctx = Ctx(settings, conn, Tasks(label, bar, cancel), ThumbCache(size=240))
        self.ctx.main = self

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        side = QWidget()
        side.setObjectName("sidebarBox")
        self._side = side
        sl = QVBoxLayout(side)
        self._sl = sl
        brand = QHBoxLayout()
        self._brand = brand
        self.brand_icon = QLabel()
        self.brand_icon.setAlignment(Qt.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("Photo Vault")
        title.setObjectName("brandName")
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("brandVer")
        names.addWidget(title)
        names.addWidget(ver)
        brand.addWidget(self.brand_icon)
        brand.addLayout(names, 1)
        sl.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("sidebar")
        self.nav.setItemDelegate(NavDelegate(self.nav))
        self.nav.setMouseTracking(True)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for name, cls in PAGES:
            it = QListWidgetItem(name)
            it.setData(ICON_ROLE, PAGE_ICONS.get(cls))
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        self.about_btn = QPushButton("About Photo Vault")
        self.about_btn.setObjectName("flat")
        self.about_btn.setCursor(Qt.PointingHandCursor)
        self.about_btn.clicked.connect(self.show_about)
        sl.addWidget(self.about_btn)
        lay.addWidget(side)
        self.stack = QStackedWidget()
        self.pages = [cls(self.ctx) for _, cls in PAGES]
        for p in self.pages:
            self.stack.addWidget(p)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.go)
        self.nav.setCurrentRow(0)

        for keys in ("Ctrl+=", "Ctrl++", QKeySequence.ZoomIn):
            self._shortcut(keys, lambda: self.set_zoom(config.step_zoom(self.ctx.settings.ui_zoom, 1)))
        for keys in ("Ctrl+-", QKeySequence.ZoomOut):
            self._shortcut(keys, lambda: self.set_zoom(config.step_zoom(self.ctx.settings.ui_zoom, -1)))
        self._shortcut("Ctrl+0", lambda: self.set_zoom(1.0))
        self._shortcut("F1", self.open_help)
        self.relayout()

        # Keep the library index fresh: quick incremental scan on every start.
        self.ctx.tasks.run("Scanning library",
                           lambda c, prog, cancel: scanner.scan(settings, prog, cancel, conn=c),
                           lambda r: label.setText(f"Library up to date: {r['total']:,} files "
                                                   f"({r['new_or_changed']:,} new/changed, {r['removed']:,} gone)"),
                           parent=self)
        self.refresh_updates()
        self._maybe_check_updates()

    def refresh_updates(self):
        """Show or hide the 'new version' notice on the Dashboard (settings changed or a check finished)."""
        for p in self.pages:
            if isinstance(p, DashboardPage):
                p.refresh_update()

    def _maybe_check_updates(self):
        """At most once a day, ask GitHub for the latest release in a daemon thread. Silent on any failure."""
        s = self.ctx.settings
        if os.environ.get("PHOTOVAULT_NO_UPDATE_CHECK") or not updates.check_due(s):
            return
        self._upd_signals = _UpdateSignals()  # kept on self so the queued signal reaches the GUI thread

        def done(tag):
            if tag:  # a failed (offline) check changes nothing and is retried next start
                updates.record_result(s, tag)
                s.save()
                self.refresh_updates()

        self._upd_signals.done.connect(done)
        sig = self._upd_signals
        threading.Thread(target=lambda: sig.done.emit(updates.fetch_latest()), daemon=True).start()

    def _restore_geometry(self):
        """Reopen at the last size/position, else fit ~90% of the screen (never larger than it)."""
        screen = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        w, h = min(1400, int(screen.width() * 0.9)), min(900, int(screen.height() * 0.9))
        self.resize(w, h)
        self.move(screen.center() - self.rect().center())
        saved = QSettings("PhotoVault", "PhotoVault").value("window/geometry")
        if saved:
            self.restoreGeometry(saved)  # Qt re-fits it if the monitor layout changed
            if not screen.intersects(self.frameGeometry()) or self.width() > screen.width() or self.height() > screen.height():
                self.resize(w, h)
                self.move(screen.center() - self.rect().center())

    def _shortcut(self, keys, fn):
        sc = QShortcut(QKeySequence(keys), self)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(fn)

    # ----- theme / zoom
    def relayout(self):
        """Re-apply every size that is not driven by the style sheet (after a theme or zoom change)."""
        px = theme.px
        self._side.setFixedWidth(px(236))
        self._sl.setContentsMargins(0, px(18), 0, px(10))
        self._sl.setSpacing(px(14))
        self._brand.setContentsMargins(px(20), 0, px(16), 0)
        self._brand.setSpacing(px(12))
        size = px(36)
        icon = QIcon(resource("icon-256.png") if os.path.exists(resource("icon-256.png")) else resource("icon.ico"))
        self.brand_icon.setPixmap(icon.pixmap(QSize(size, size)))
        self.brand_icon.setFixedSize(size, size)
        self._bar.setFixedWidth(px(320))
        self.nav.setSpacing(0)
        self.nav.doItemsLayout()
        self.nav.viewport().update()
        for p in self.pages:
            p.on_zoom()

    def set_zoom(self, z):
        z = config.clamp_zoom(z)
        self.ctx.settings.ui_zoom = z
        self.ctx.settings.save()
        self.refresh_theme()
        self.statusBar().showMessage(f"Zoom {round(z * 100)}%", 1500)

    def refresh_theme(self):
        s = self.ctx.settings
        apply_theme(QApplication.instance(), s.theme, s.ui_zoom)
        self.relayout()

    def open_help(self):
        """Jump to the Help page (F1, first-run notice)."""
        for i, (_, cls) in enumerate(PAGES):
            if cls is HelpPage:
                self.nav.setCurrentRow(i)
                return

    def show_about(self):
        AboutDialog(self, self.ctx.settings).exec()

    def open_search(self, text):
        """Jump to the Search page and run a query (used by People)."""
        for i, (_, cls) in enumerate(PAGES):
            if cls is SearchPage:
                self.nav.setCurrentRow(i)
                self.pages[i].set_query(text)
                return

    def open_search_tags(self, tags, text=""):
        """Jump to Search with tag chips (used by the Tags page)."""
        for i, (_, cls) in enumerate(PAGES):
            if cls is SearchPage:
                self.nav.setCurrentRow(i)
                self.pages[i].set_tag_filters(tags)
                self.pages[i].set_query(text)
                return

    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.pages[i].on_show()

    def closeEvent(self, ev):
        QSettings("PhotoVault", "PhotoVault").setValue("window/geometry", self.saveGeometry())
        if self.ctx.tasks.busy:
            self.ctx.tasks.cancel()
            self.ctx.tasks.pool.waitForDone(10000)
        if hasattr(self.ctx, "ai"):
            self.ctx.ai.stop()
        self.ctx.thumbs.clear_queue()
        self.ctx.thumbs.pool.waitForDone(3000)
        super().closeEvent(ev)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PhotoVault.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Vault")
    icon = resource("icon.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    settings = config.Settings.load()
    apply_theme(app, settings.theme, settings.ui_zoom)
    w = MainWindow(settings)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
