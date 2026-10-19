"""Health, Browse, Quarantine, Activity log and Settings pages."""
import html
import os
import re
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
                               QSpinBox, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core import config, health, journal, quarantine, scanner
from .common import (FIRST_RUN_TEXT, BAD, button, confirm, fill_table, header, hrow, human, info, make_table,
                     open_file, panel, px, section_title, show_in_explorer, square_pixmap)
from .pages_a import Page, scroll_wrap


def selected_data(table):
    rows = sorted({i.row() for i in table.selectionModel().selectedRows()})
    return [table.item(r, 0).data(Qt.UserRole) for r in rows]


# =================================================================== Health
class HealthPage(Page):
    TABS = [("corrupt", "Damaged photos"), ("zero", "Empty files (0 bytes)"), ("junk", "Junk / cache files"),
            ("tiny", "Tiny images"), ("no_date", "No date"), ("empty_dirs", "Empty folders")]
    HELP = {
        "corrupt": "These photos can't be fully opened (often from damaged memory cards). Check them - some may still "
                   "show partially. Quarantine the ones that are broken.",
        "zero": "Files with no content at all. Safe to quarantine.",
        "junk": "Thumbnail caches, .nomedia markers, shortcuts and other leftovers from phones. Safe to quarantine.",
        "tiny": "Very small images (under 160×160) - usually thumbnails, stickers or icons. Check before removing.",
        "no_date": "Photos/videos where no real date was found (no camera info, no date in the name). Organize puts "
                   "them in Life\\_No date. You can set a date for selected files here.",
        "empty_dirs": "Folders with nothing inside. Safe to remove.",
    }

    def __init__(self, ctx):
        super().__init__(ctx)
        self.res = None
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Health check", "Finds damaged photos, empty files, junk, tiny thumbnails, photos without "
                                             "a date and empty folders. Fixes go to quarantine - nothing is deleted directly."))
        self.run_btn = button("Run health check", primary=True)
        self.run_btn.clicked.connect(self.check)
        self.deep = QCheckBox("Fully open every photo to find damaged ones (slower the first time)")
        self.deep.setChecked(True)
        self.actions += [self.run_btn]
        lay.addLayout(hrow(self.run_btn, self.deep))
        self.tabs = QTabWidget()
        self.tables = {}
        for key, title in self.TABS:
            w = QWidget()
            wl = QVBoxLayout(w)
            h = QLabel(self.HELP[key])
            h.setWordWrap(True)
            h.setObjectName("pageSub")
            wl.addWidget(h)
            if key == "empty_dirs":
                t = make_table(["Folder"], stretch_col=0)
            else:
                t = make_table(["File", "Folder", "Size", "Date", "Details"], stretch_col=1)
                t.doubleClicked.connect(lambda idx, t=t: self._open(t, idx.row()))
            wl.addWidget(t)
            self.tables[key] = t
            self.tabs.addTab(w, title)
        lay.addWidget(self.tabs, 1)
        self.sel_all = button("Select all")
        self.sel_all.clicked.connect(lambda: self.current_table().selectAll())
        self.show_btn = button("Show in folder")
        self.show_btn.clicked.connect(self.show_selected)
        self.q_btn = button("Quarantine selected", primary=True)
        self.q_btn.clicked.connect(self.quarantine_selected)
        self.rm_btn = button("Remove selected empty folders", primary=True)
        self.rm_btn.clicked.connect(self.remove_dirs)
        self.date_btn = button("Set date for selected…")
        self.date_btn.clicked.connect(self.set_date)
        lay.addLayout(hrow(self.sel_all, self.show_btn, None, self.date_btn, self.rm_btn, self.q_btn))
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(0)

    def current_key(self):
        return self.TABS[self.tabs.currentIndex()][0]

    def current_table(self):
        return self.tables[self.current_key()]

    def _tab_changed(self, _i):
        k = self.current_key()
        self.q_btn.setVisible(k not in ("empty_dirs", "no_date"))
        self.rm_btn.setVisible(k == "empty_dirs")
        self.date_btn.setVisible(k == "no_date")

    def _open(self, t, row):
        d = t.item(row, 0).data(Qt.UserRole)
        if d:
            open_file(d["path"])

    def check(self):
        deep = self.deep.isChecked()
        self.run("Health check", lambda conn, prog, cancel: health.scan(conn, self.ctx.settings, deep, prog, cancel), self.loaded)

    def loaded(self, res):
        self.res = res
        for i, (key, title) in enumerate(self.TABS):
            items = res[key]
            t = self.tables[key]
            if key == "empty_dirs":
                fill_table(t, [(p,) for p in items], data_key=lambda j, items=items: {"path": items[j]})
            else:
                rows = [dict(r) for r in items]
                fill_table(t, [(r["name"], os.path.dirname(r["rel"]) or r["top"], (human(r["size"]), r["size"]),
                                (r["date_taken"] or "")[:16], (r["health"] or "")[9:] if key == "corrupt" else
                                (f"{r['width']}×{r['height']}" if r["width"] else "")) for r in rows],
                           data_key=lambda j, rows=rows: rows[j])
            self.tabs.setTabText(i, f"{title} ({len(items):,})")

    def show_selected(self):
        sel = selected_data(self.current_table())
        if sel:
            show_in_explorer(sel[0]["path"])

    def quarantine_selected(self):
        sel = selected_data(self.current_table())
        if not sel:
            return
        key = self.current_key()
        reason = {"corrupt": "damaged", "zero": "empty file", "junk": "junk", "tiny": "tiny image"}[key]
        if not confirm(self, "Quarantine", f"Move {len(sel):,} files to quarantine?"):
            return
        items = [(r["path"], None) for r in sel]
        self.run("Moving to quarantine",
                 lambda conn, prog, cancel: quarantine.quarantine_files(conn, self.ctx.settings, items, reason,
                                                                        f"Health check ({reason})", prog, cancel),
                 lambda r: self._drop(key, {r2["path"] for r2 in sel}))

    def _drop(self, key, paths):
        if self.res:
            self.res[key] = [r for r in self.res[key] if (r if isinstance(r, str) else r["path"]) not in paths]
            self.loaded(self.res)

    def remove_dirs(self):
        sel = [d["path"] for d in selected_data(self.tables["empty_dirs"])]
        if sel:
            n = health.remove_empty_dirs(sel)
            info(self, "Empty folders", f"Removed {n:,} empty folders.")
            self._drop("empty_dirs", set(sel))

    def set_date(self):
        sel = selected_data(self.tables["no_date"])
        if not sel:
            return
        text, ok = QInputDialog.getText(self, "Set date", f"Date for {len(sel):,} selected files (YYYY-MM-DD):",
                                        text=(sel[0]["date_taken"] or "")[:10])
        if not ok:
            return
        if not re.fullmatch(r"(19|20)\d\d-\d\d-\d\d", text.strip()):
            info(self, "Set date", "Please use the format YYYY-MM-DD, e.g. 2019-08-15.")
            return
        health.set_manual_date(self.ctx.conn, [r["id"] for r in sel], text.strip())
        self._drop("no_date", {r["path"] for r in sel})


# =================================================================== Browse
class BrowsePage(Page):
    PAGE = 400

    def __init__(self, ctx):
        super().__init__(ctx)
        self.rows = []
        self.shown = 0
        self.items_by_path = {}
        self._make_blank()
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Browse", "Look through your library by year, month, folder or type. Double-click to open."))
        self.year = QComboBox()
        self.month = QComboBox()
        self.month.addItem("All months", None)
        for m in range(1, 13):
            self.month.addItem(time.strftime("%B", time.strptime(str(m), "%m")), f"{m:02d}")
        self.folder = QComboBox()
        self.folder.setMinimumWidth(200)
        self.kind = QComboBox()
        for t, v in (("Photos & videos", None), ("Photos", "image"), ("Videos", "video")):
            self.kind.addItem(t, v)
        self.search = QLineEdit()
        self.search.setPlaceholderText("File name contains…")
        self.search.returnPressed.connect(self.load)
        go = button("Show", primary=True)
        go.clicked.connect(self.load)
        lay.addLayout(hrow(QLabel("Year:"), self.year, self.month, QLabel("Folder:"), self.folder, self.kind,
                           self.search, go))
        self.count = QLabel("")
        self.count.setObjectName("pageSub")
        lay.addWidget(self.count)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self._grid_sizes()
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.itemDoubleClicked.connect(lambda it: open_file(it.data(Qt.UserRole)))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.menu)
        lay.addWidget(self.grid, 1)
        self.more = button("Load more")
        self.more.clicked.connect(self.load_more)
        lay.addLayout(hrow(self.more))
        ctx.thumbs.ready.connect(self._thumb_ready)

    def on_show(self):
        cur_y, cur_f = self.year.currentData(), self.folder.currentData()
        self.year.clear()
        self.year.addItem("All years", None)
        for r in self.ctx.conn.execute("SELECT DISTINCT substr(date_taken,1,4) y FROM files WHERE kind IN ('image','video') "
                                       "AND date_src<>'mtime' ORDER BY y DESC"):
            self.year.addItem(r["y"], r["y"])
        self.year.addItem("No date", "nodate")
        self.folder.clear()
        self.folder.addItem("All folders", None)
        for r in self.ctx.conn.execute("SELECT DISTINCT top FROM files ORDER BY top"):
            self.folder.addItem(r["top"], r["top"])
        for combo, val in ((self.year, cur_y), (self.folder, cur_f)):
            i = combo.findData(val)
            if i >= 0:
                combo.setCurrentIndex(i)
        if not self.rows:
            self.load()

    def load(self):
        where, args = ["kind IN ('image','video')"], []
        y, m, f, k, s = (self.year.currentData(), self.month.currentData(), self.folder.currentData(),
                         self.kind.currentData(), self.search.text().strip())
        if y == "nodate":
            where.append("date_src='mtime'")
        elif y:
            where.append("date_taken LIKE ? AND date_src<>'mtime'")
            args.append(f"{y}-{m or '__'}-%")
        elif m:
            where.append("substr(date_taken,6,2)=? AND date_src<>'mtime'")
            args.append(m)
        if f:
            where.append("top=?")
            args.append(f)
        if k:
            where.append("kind=?")
            args.append(k)
        if s:
            where.append("name LIKE ?")
            args.append(f"%{s}%")
        self.rows = self.ctx.conn.execute(f"SELECT path, name, date_taken, kind FROM files WHERE {' AND '.join(where)} "
                                          "ORDER BY date_taken", args).fetchall()
        self.ctx.thumbs.clear_queue()
        self.grid.clear()
        self.items_by_path = {}
        self.shown = 0
        self.count.setText(f"{len(self.rows):,} photos/videos")
        self.load_more()

    def show_paths(self, paths, title=""):
        """Show exactly these photos (used by Timeline & Places). Paths the library index does not know are
        still listed by file name. Use Show to go back to the normal filters."""
        known = {}
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            for r in self.ctx.conn.execute(
                    f"SELECT path, name, date_taken, kind FROM files WHERE path IN ({','.join('?' * len(chunk))})", chunk):
                known[r["path"]] = r
        self.rows = [known.get(p) or {"path": p, "name": os.path.basename(p), "date_taken": "", "kind": "image"}
                     for p in paths]
        self.ctx.thumbs.clear_queue()
        self.grid.clear()
        self.items_by_path = {}
        self.shown = 0
        self.count.setText(f"{title + ': ' if title else ''}{len(self.rows):,} photos/videos "
                           "(click Show to return to the normal filters)")
        self.load_more()

    def _make_blank(self):
        self.blank = QPixmap(px(170), px(170))
        self.blank.fill(Qt.transparent)

    def _grid_sizes(self):
        self.grid.setIconSize(QSize(px(170), px(170)))
        self.grid.setGridSize(QSize(px(190), px(215)))
        self.grid.setSpacing(px(6))

    def on_zoom(self):
        super().on_zoom()
        self._grid_sizes()
        self._make_blank()
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            it.setSizeHint(self.grid.gridSize() - QSize(px(6), 0))
            self._thumb_ready(it.data(Qt.UserRole))

    def load_more(self):
        chunk = self.rows[self.shown:self.shown + self.PAGE]
        for r in chunk:
            it = QListWidgetItem(f"{r['name']}\n{(r['date_taken'] or '')[:10]}")
            it.setData(Qt.UserRole, r["path"])
            it.setToolTip(r["path"])
            pm = self.ctx.thumbs.get(r["path"])
            it.setIcon(QIcon(square_pixmap(pm, self.grid.iconSize().width()) if pm is not None else self.blank))
            it.setSizeHint(self.grid.gridSize() - QSize(px(6), 0))
            self.grid.addItem(it)
            self.items_by_path[r["path"]] = it
        self.shown += len(chunk)
        self.more.setVisible(self.shown < len(self.rows))
        self.more.setText(f"Load more ({len(self.rows) - self.shown:,} left)")

    def _thumb_ready(self, path):
        it = self.items_by_path.get(path)
        if it is not None:
            pm = self.ctx.thumbs.get(path)
            if pm is not None:
                it.setIcon(QIcon(square_pixmap(pm, self.grid.iconSize().width())))

    def menu(self, pos):
        it = self.grid.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        m.addAction("Open", lambda: open_file(it.data(Qt.UserRole)))
        m.addAction("Show in folder", lambda: show_in_explorer(it.data(Qt.UserRole)))
        m.addAction("Copy path", lambda: QApplication.clipboard().setText(it.data(Qt.UserRole)))
        m.exec(self.grid.mapToGlobal(pos))


# =================================================================== Quarantine
class QuarantinePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Quarantine", "Files removed by the app wait here. Restore anything you want back, or send "
                                           "them to the Windows Recycle Bin when you're sure (best after a backup)."))
        self.summary = QLabel("")
        self.summary.setObjectName("pageSub")
        self.reason = QComboBox()
        self.reason.currentIndexChanged.connect(self.refresh)
        lay.addLayout(hrow(QLabel("Show:"), self.reason, self.summary))
        self.table = make_table(["Reason", "File", "Original location", "Size", "Moved on", "Kept copy"], stretch_col=2)
        self.table.doubleClicked.connect(lambda idx: open_file(self.table.item(idx.row(), 0).data(Qt.UserRole)["qpath"]))
        lay.addWidget(self.table, 1)
        self.restore_btn = button("Restore selected")
        self.restore_btn.clicked.connect(self.restore)
        self.empty_btn = button("Send selected to Recycle Bin…")
        self.empty_btn.clicked.connect(lambda: self.empty(False))
        self.empty_all_btn = button("Send ALL shown to Recycle Bin…", danger=True)
        self.empty_all_btn.clicked.connect(lambda: self.empty(True))
        open_btn = button("Open quarantine folder")
        open_btn.clicked.connect(lambda: os.path.isdir(self.ctx.settings.quarantine_dir) and os.startfile(self.ctx.settings.quarantine_dir))
        self.actions += [self.restore_btn, self.empty_btn, self.empty_all_btn]
        lay.addLayout(hrow(self.restore_btn, open_btn, None, self.empty_btn, self.empty_all_btn))

    def on_show(self):
        cur = self.reason.currentData()
        self.reason.blockSignals(True)
        self.reason.clear()
        self.reason.addItem("All reasons", None)
        for r in self.ctx.conn.execute("SELECT reason, COUNT(*) n FROM quarantine WHERE state='in' GROUP BY reason"):
            self.reason.addItem(f"{r['reason']} ({r['n']:,})", r["reason"])
        i = self.reason.findData(cur)
        self.reason.setCurrentIndex(max(0, i))
        self.reason.blockSignals(False)
        self.refresh()

    def refresh(self):
        r = self.reason.currentData()
        q = "SELECT * FROM quarantine WHERE state='in'" + (" AND reason=?" if r else "") + " ORDER BY ts DESC, id"
        rows = [dict(x) for x in self.ctx.conn.execute(q, (r,) if r else ())]
        self.rows = rows
        fill_table(self.table, [(x["reason"], os.path.basename(x["original"]), os.path.dirname(x["original"]),
                                 (human(x["size"]), x["size"] or 0),
                                 time.strftime("%Y-%m-%d %H:%M", time.localtime(x["ts"])), x["kept"] or "") for x in rows],
                   data_key=lambda j: rows[j])
        total = sum(x["size"] or 0 for x in rows)
        self.summary.setText(f"{len(rows):,} files · {human(total)}")

    def _ids(self, all_rows):
        return [x["id"] for x in (self.rows if all_rows else selected_data(self.table))]

    def restore(self):
        ids = self._ids(False)
        if not ids:
            return
        self.run("Restoring", lambda conn, prog, cancel: quarantine.restore(conn, self.ctx.settings, ids, prog, cancel),
                 lambda r: (self.on_show(), info(self, "Restored", f"{r['restored']:,} files restored to their original place."
                                                 + (f"\n{r['failed']:,} failed." if r["failed"] else ""))))

    def empty(self, all_rows):
        ids = self._ids(all_rows)
        if not ids:
            return
        size = sum(x["size"] or 0 for x in self.rows if x["id"] in set(ids))
        if not confirm(self, "Send to Recycle Bin",
                       f"Send {len(ids):,} files ({human(size)}) to the Windows Recycle Bin?\n\n"
                       "You can still get them back from the Recycle Bin until you empty it. (If a file is too big "
                       "for the Recycle Bin, Windows may delete it permanently.)\n\nTip: do this after a backup."):
            return
        self.run("Sending to Recycle Bin", lambda conn, prog, cancel: quarantine.empty(conn, self.ctx.settings, ids, prog, cancel),
                 lambda r: (self.on_show(), info(self, "Done", f"{r['emptied']:,} files ({human(r['bytes'])}) sent to the Recycle Bin.")))


# =================================================================== Activity log
class LogPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Activity log", "Everything the app changed. Moves (organize, quarantine) can be undone."))
        self.table = make_table(["Date", "Type", "What happened", "Undo"], stretch_col=2,
                                selection=QAbstractItemView.SingleSelection)
        lay.addWidget(self.table, 1)
        self.undo_btn = button("Undo selected…")
        self.undo_btn.clicked.connect(self.undo)
        self.actions += [self.undo_btn]
        lay.addLayout(hrow(self.undo_btn))

    def on_show(self):
        rows = [dict(r) for r in self.ctx.conn.execute("SELECT * FROM ops ORDER BY id DESC LIMIT 1000")]
        self.rows = rows
        fill_table(self.table, [(time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])), r["kind"], r["summary"],
                                 "undone" if r["undone"] else "can undo" if r["undoable"] else "")
                                for r in rows], data_key=lambda j: rows[j])

    def undo(self):
        sel = selected_data(self.table)
        if not sel:
            return
        op = sel[0]
        if not op["undoable"] or op["undone"]:
            info(self, "Undo", "This entry can't be undone.")
            return
        if not confirm(self, "Undo", f"Undo this?\n\n{op['summary']}\n\nFiles are moved back to where they were."):
            return
        s = self.ctx.settings

        def fn(conn, prog, cancel):
            r = journal.undo(conn, op["id"], s.library_roots, [s.quarantine_dir], prog, cancel)
            scanner.index_paths(r["paths"], s, conn)
            conn.commit()
            return r

        self.run("Undoing", fn, lambda r: (self.on_show(),
                                           info(self, "Undo finished", f"{r['restored']:,} files moved back, {r['skipped']:,} skipped.")))


# =================================================================== Settings
class SettingsPage(Page):
    CATEGORIES = ["raw", "curated", "screenshots", "projects", "documents", "other", "skip",
                  "chat:WhatsApp", "chat:Snapchat", "chat:Instagram", "chat:Messenger", "gaming:<name>"]

    def __init__(self, ctx):
        super().__init__(ctx)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        self.set_margins(lay)
        lay.addWidget(header("Settings"))
        self.firstrun = QLabel(FIRST_RUN_TEXT)
        self.firstrun.setWordWrap(True)
        self.firstrun.setObjectName("bigResult")
        self.firstrun.linkActivated.connect(lambda _h: self.ctx.main.open_help())
        lay.addWidget(self.firstrun)

        p, pl = panel()
        pl.addWidget(section_title("Folders"))
        form = QFormLayout()
        self.roots = QListWidget()
        self.roots.setMaximumHeight(px(80))
        add = button("Add…")
        add.clicked.connect(self.add_root)
        rem = button("Remove")
        rem.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        form.addRow("Library folders (scanned):", self.roots)
        form.addRow("", hrow(add, rem))
        self.org = QLineEdit()
        form.addRow("Organized photos go to:", self.org)
        self.quar = QLineEdit()
        form.addRow("Quarantine folder:", self.quar)
        pl.addLayout(form)
        lay.addWidget(p)

        p, pl = panel()
        pl.addWidget(section_title("Behaviour"))
        form = QFormLayout()
        self.sim = QSpinBox()
        self.sim.setRange(1, 7)
        form.addRow("Similar-photo sensitivity (1 strict – 7 loose):", self.sim)
        self.remind = QSpinBox()
        self.remind.setRange(1, 365)
        self.remind.setSuffix(" days")
        form.addRow("Remind me to back up after:", self.remind)
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        form.addRow("Theme:", self.theme)
        pl.addLayout(form)
        lay.addWidget(p)

        p, pl = panel()
        pl.addWidget(section_title("Folder rules (used by Organize and to choose which duplicate to keep)"))
        helptxt = "<br>".join(f"<b>{html.escape(k)}</b> - {html.escape(v)}" for k, v in config.CATEGORY_HELP.items())
        h = QLabel("Each top-level folder is matched against these patterns (first match wins, * = anything). "
                   "Unmatched folders count as <b>raw</b>.<br>" + helptxt)
        h.setWordWrap(True)
        h.setObjectName("pageSub")
        pl.addWidget(h)
        self.rules = make_table(["Folder name pattern", "Category"], stretch_col=0)
        self.rules.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.rules.setMinimumHeight(px(320))
        pl.addWidget(self.rules)
        addr = button("Add rule")
        addr.clicked.connect(lambda: self._add_rule("", "raw"))
        remr = button("Remove selected rule")
        remr.clicked.connect(lambda: [self.rules.removeRow(r.row()) for r in
                                      sorted(self.rules.selectionModel().selectedRows(), key=lambda x: -x.row())])
        up = button("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = button("Move down")
        down.clicked.connect(lambda: self._move(1))
        reset = button("Reset to defaults")
        reset.clicked.connect(self.reset_rules)
        pl.addLayout(hrow(addr, remr, up, down, None, reset))
        lay.addWidget(p)

        save = button("Save settings", primary=True)
        save.clicked.connect(self.save)
        self.saved = QLabel("")
        lay.addLayout(hrow(save, self.saved))
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(inner))

    def on_show(self):
        s = self.ctx.settings
        self.firstrun.setVisible(not s.setup_complete)
        self.roots.clear()
        self.roots.addItems(s.library_roots)
        self.org.setText(s.organized_root)
        self.quar.setText(s.quarantine_dir)
        self.sim.setValue(int(s.similar_threshold))
        self.remind.setValue(int(s.backup_reminder_days))
        self.theme.setCurrentText(s.theme)
        self.rules.setRowCount(0)
        for pat, cat in s.folder_rules:
            self._add_rule(pat, cat)
        self.saved.setText("")

    def _add_rule(self, pat, cat):
        r = self.rules.rowCount()
        self.rules.insertRow(r)
        self.rules.setItem(r, 0, QTableWidgetItem(pat))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(self.CATEGORIES)
        combo.setCurrentText(cat)
        self.rules.setCellWidget(r, 1, combo)

    def _rule_list(self):
        out = []
        for r in range(self.rules.rowCount()):
            pat = (self.rules.item(r, 0).text() if self.rules.item(r, 0) else "").strip()
            cat = self.rules.cellWidget(r, 1).currentText().strip()
            if pat and cat:
                out.append([pat, cat])
        return out

    def _move(self, d):
        rows = self.rules.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        rules = self._rule_list()
        if 0 <= r + d < len(rules):
            rules[r], rules[r + d] = rules[r + d], rules[r]
            self.rules.setRowCount(0)
            for pat, cat in rules:
                self._add_rule(pat, cat)
            self.rules.selectRow(r + d)

    def reset_rules(self):
        self.rules.setRowCount(0)
        for pat, cat in config.DEFAULT_RULES:
            self._add_rule(pat, cat)

    def add_root(self):
        d = QFileDialog.getExistingDirectory(self, "Library folder", config._pictures())
        if d:
            self.roots.addItem(os.path.normpath(d))

    def save(self):
        s = self.ctx.settings
        s.library_roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        s.organized_root = self.org.text().strip()
        s.quarantine_dir = self.quar.text().strip()
        s.similar_threshold = self.sim.value()
        s.backup_reminder_days = self.remind.value()
        theme_changed = s.theme != self.theme.currentText()
        s.theme = self.theme.currentText()
        s.folder_rules = self._rule_list()
        if os.path.normcase(s.organized_root) not in {os.path.normcase(r) for r in s.library_roots}:
            s.library_roots.append(s.organized_root)
            self.roots.addItem(s.organized_root)
        s.setup_complete = True
        s.save()
        self.firstrun.hide()
        if theme_changed:
            self.ctx.main.refresh_theme()
        self.saved.setText("Saved ✔ (run a library scan if you changed folders)")
