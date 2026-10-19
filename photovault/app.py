"""Photo Vault - main window."""
import os
import shutil
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMessageBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QProgressBar,
                               QPushButton, QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from . import __version__
from .core import config, db, quarantine, scanner, updater
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


class MainWindow(QMainWindow):
    updateFound = Signal(object)

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
        self.update_btn = QPushButton()
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.hide()
        self.update_btn.clicked.connect(self.start_update)
        sl.addWidget(self.update_btn)
        self.release = None
        self.updateFound.connect(self._show_update)
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
        self.check_for_update()

        # Keep the library index fresh: quick incremental scan on every start.
        self.ctx.tasks.run("Scanning library",
                           lambda c, prog, cancel: scanner.scan(settings, prog, cancel, conn=c),
                           lambda r: label.setText(f"Library up to date: {r['total']:,} files "
                                                   f"({r['new_or_changed']:,} new/changed, {r['removed']:,} gone)"),
                           parent=self)

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

    def check_for_update(self, force=False):
        """Look for a newer GitHub release in a background thread (at most daily unless forced; offline = silent)."""
        s = self.ctx.settings
        if not force and (not s.check_updates or time.time() - s.last_update_check < 86400):
            return

        threading.Thread(target=lambda: self.updateFound.emit(updater.check(__version__)), daemon=True).start()

    def _show_update(self, rel):
        s = self.ctx.settings  # runs on the UI thread, so settings are only ever saved from there
        s.last_update_check = time.time()
        s.save()
        self.release = rel
        if rel:
            self.update_btn.setText(f"Update to {rel.tag} available")
            self.update_btn.show()
        else:
            self.update_btn.hide()

    def start_update(self):
        rel = self.release
        if not rel:
            return
        notes = (rel.notes or "").strip()[:1500]
        box = QMessageBox(self)
        box.setWindowTitle("Update Photo Vault")
        box.setText(f"Update from v{__version__} to {rel.tag}?")
        box.setInformativeText(notes + "\n\nYour photos, settings and index are not touched. Photo Vault will restart.")
        go = box.addButton("Update now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return
        if not updater.install_dir():
            import webbrowser
            webbrowser.open(rel.page)  # running from source: nothing to swap
            return
        self.update_btn.setEnabled(False)
        inst, protected = updater.install_dir(), self._protected_folders()

        def work(c, prog, cancel):
            tmp = tempfile.mkdtemp(prefix="photovault_update_")
            try:
                zip_path = updater.download_and_verify(rel, tmp, lambda d, t: prog(d, t, "Downloading update"), cancel)
                prog(0, 0, "Preparing update")
                return updater.prepare(zip_path, inst, protected, rel.sha)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        def failed(msg):
            self.update_btn.setEnabled(True)
            if msg != "Cancelled":
                QMessageBox.warning(self, "Update failed", str(msg))

        self.ctx.tasks.run("Downloading update", work, lambda new: self._apply_update(new, inst), on_fail=failed,
                           parent=self)

    def _protected_folders(self):
        """Every folder holding user data; the updater refuses to replace an install folder that overlaps one."""
        from .ai import paths as ai_paths
        s = self.ctx.settings
        return [str(config.APP_DIR), str(ai_paths.DATA), *s.library_roots, s.organized_root, s.quarantine_dir,
                *s.backup_sources, s.backup_target]

    def _apply_update(self, new, inst):
        try:
            updater.launch_swap(new, inst)
        except Exception as e:
            self.update_btn.setEnabled(True)
            QMessageBox.warning(self, "Update failed", str(e))
            return
        QApplication.quit()

    def show_about(self):
        AboutDialog(self).exec()

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
