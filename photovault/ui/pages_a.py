"""Dashboard and Duplicates pages."""
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QRadioButton, QScrollArea, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import dupes, quarantine, scanner, stats
from . import theme
from .common import (FIRST_RUN_TEXT, BAD, GOOD, WARN, BarChart, Card, button, confirm, header, hrow, human, info, open_file,
                     panel, px, section_title, show_in_explorer)


class Page(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.actions = []
        self._margin_layouts = []
        ctx.tasks.busyChanged.connect(lambda b: [a.setEnabled(not b) for a in self.actions])

    def on_show(self):
        pass

    def set_margins(self, lay):
        """Standard page margins, rescaled with the UI zoom."""
        self._margin_layouts.append(lay)
        lay.setContentsMargins(px(28), px(24), px(28), px(20))

    def on_zoom(self):
        """UI zoom or theme changed: re-apply any size set in code (the style sheet is already updated).
        Subclasses override this and call super().on_zoom()."""
        for lay in self._margin_layouts:
            lay.setContentsMargins(px(28), px(24), px(28), px(20))
        for f in self.findChildren(QFrame, "panel"):
            f.layout().setContentsMargins(px(16), px(14), px(16), px(14))
        for w in self.findChildren(Card) + self.findChildren(BarChart) + self.findChildren(FileCard):
            w.rescale()

    def run(self, title, fn, on_done=None):
        return self.ctx.tasks.run(title, fn, on_done, parent=self)


def scroll_wrap(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    return sa


# =================================================================== Dashboard
class DashboardPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        self.set_margins(lay)
        lay.setSpacing(px(14))
        lay.addWidget(header("Dashboard", "Overview of your whole photo library."))
        self.firstrun = QLabel(FIRST_RUN_TEXT)
        self.firstrun.setWordWrap(True)
        self.firstrun.setObjectName("bigResult")
        self.firstrun.linkActivated.connect(lambda _h: self.ctx.main.open_help())
        lay.addWidget(self.firstrun)

        self.scan_btn = button("Scan library", primary=True)
        self.scan_btn.clicked.connect(self.scan)
        self.export_btn = button("Export report…")
        self.export_btn.clicked.connect(self.export)
        self.last_scan = QLabel("")
        self.last_scan.setObjectName("pageSub")
        self.actions += [self.scan_btn]
        lay.addLayout(hrow(self.scan_btn, self.export_btn, self.last_scan))

        grid = QGridLayout()
        grid.setSpacing(px(12))
        self.cards = {}
        names = [("photos", "Photos"), ("videos", "Videos"), ("size", "Library size"), ("nodate", "Without a date"),
                 ("dupes", "Duplicates found"), ("quar", "In quarantine"), ("backup", "Last backup"), ("disk", "Free space")]
        for i, (k, t) in enumerate(names):
            c = Card(t)
            self.cards[k] = c
            grid.addWidget(c, i // 4, i % 4)
        lay.addLayout(grid)

        self.year_chart = BarChart(vertical=True)
        self.folder_chart = BarChart()
        self.camera_chart = BarChart()
        self.type_chart = BarChart()
        p, pl = panel()
        pl.addWidget(section_title("Photos & videos per year"))
        pl.addWidget(self.year_chart)
        lay.addWidget(p)
        row = QHBoxLayout()
        for title, ch in (("Size per folder", self.folder_chart), ("Phones & cameras", self.camera_chart)):
            p, pl = panel()
            pl.addWidget(section_title(title))
            pl.addWidget(ch)
            pl.addStretch(1)
            row.addWidget(p)
        lay.addLayout(row)
        p, pl = panel()
        pl.addWidget(section_title("File types"))
        pl.addWidget(self.type_chart)
        lay.addWidget(p)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_wrap(inner))
        ctx.tasks.finished.connect(lambda _t: self.refresh() if self.isVisible() else None)

    def on_show(self):
        self.refresh()

    def scan(self):
        self.run("Scanning library", lambda conn, prog, cancel: scanner.scan(self.ctx.settings, prog, cancel, conn=conn),
                 lambda r: self.ctx.tasks.label.setText(
                     f"Scan done: {r['total']:,} files ({r['new_or_changed']:,} new/changed, "
                     f"{r['removed']:,} gone) in {r['seconds']}s"))

    def refresh(self):
        self.firstrun.setVisible(not self.ctx.settings.setup_complete)
        s = stats.collect(self.ctx.conn, self.ctx.settings)
        self.stats = s
        c = self.cards
        c["photos"].set(f"{s['photos'][0]:,}", human(s["photos"][1]))
        c["videos"].set(f"{s['videos'][0]:,}", human(s["videos"][1]))
        c["size"].set(human(s["total_bytes"]), f"{s['total_files']:,} files in total")
        c["nodate"].set(f"{s['no_date']:,}", "Fix these in Health check", WARN if s["no_date"] else None)
        c["dupes"].set(f"{s['dup_count']:,}", f"{human(s['dup_wasted'])} wasted - see Duplicates",
                       WARN if s["dup_count"] else GOOD)
        c["quar"].set(f"{s['quarantine_n']:,}", human(s["quarantine_b"]))
        days = stats.days_since(s["last_backup"])
        limit = self.ctx.settings.backup_reminder_days
        if days is None:
            c["backup"].set("Never", "Back up to your external disk!", BAD)
        else:
            c["backup"].set("Today" if days == 0 else f"{days} days ago",
                            "Time for a backup!" if days >= limit else "Up to date", BAD if days >= limit else GOOD)
        lib, bk = s["disk_library"], s["disk_backup"]
        parts = []
        if lib:
            parts.append(f"{lib['drive']} {human(lib['free'])} free")
        if not bk:
            parts.append("Backup disk not connected")
        elif not lib or bk["drive"].lower() != lib["drive"].lower():
            parts.append(f"{bk['drive']} {human(bk['free'])} free")
        c["disk"].set(human(lib["free"]) if lib else "–", " · ".join(parts),
                      BAD if lib and lib["free"] < 15 * 1024**3 else None)
        self.year_chart.set_data([(y, n, f"{n:,}") for y, n, _ in s["by_year"]])
        self.folder_chart.set_data([(f, b or 0, human(b)) for f, _, b in s["by_folder"][:15]])
        self.camera_chart.set_data([(cam, n, f"{n:,}") for cam, n in s["by_camera"][:12]])
        self.type_chart.set_data([(e, n, f"{n:,} · {human(b)}") for e, n, b in s["by_ext"][:12]])
        ls = s["last_scan"]
        self.last_scan.setText("Never scanned" if not ls else "Last scan: " + time.strftime("%d %b %Y %H:%M", time.localtime(ls)))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save report", os.path.expanduser("~/Desktop/Photo Vault report.html"),
                                              "HTML (*.html)")
        if path:
            stats.export_html(stats.collect(self.ctx.conn, self.ctx.settings), path)
            open_file(path)


# =================================================================== Duplicates
class FileCard(QFrame):
    def __init__(self, row, thumbs, keep_group: QButtonGroup, idx: int, hint: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.setFixedWidth(px(250))
        self.path = row["path"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(12), px(12), px(12), px(12))
        self.img = QLabel()
        self.img.setFixedSize(px(220), px(200))
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setCursor(Qt.PointingHandCursor)
        self.img.mousePressEvent = lambda e: open_file(self.path)
        lay.addWidget(self.img)
        self.radio = QRadioButton("Keep this one")
        keep_group.addButton(self.radio, idx)
        lay.addWidget(self.radio)
        res = f"{row['width']}×{row['height']}" if row["width"] else ""
        date_src = {"exif": "camera date", "video": "video date", "filename": "from filename",
                    "mtime": "file date (unsure)", "manual": "set manually"}.get(row["date_src"], "")
        txt = QLabel(f"<b>{row['name']}</b><br><span style='color:{theme.tokens()['text2']}'>{os.path.dirname(row['rel']) or row['top']}</span>"
                     f"<br>{human(row['size'])} {('· ' + res) if res else ''}<br>{(row['date_taken'] or '')[:16]} "
                     f"<span style='color:{theme.tokens()['text2']}'>({date_src})</span>")
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.RichText)
        lay.addWidget(txt)
        if hint:
            note = QLabel(hint)
            note.setObjectName("pageSub")
            note.setWordWrap(True)
            lay.addWidget(note)
        b = button("Show in folder")
        b.clicked.connect(lambda: show_in_explorer(self.path))
        lay.addWidget(b)
        self.thumbs = thumbs
        self.update_thumb()

    def rescale(self):
        self.setFixedWidth(px(250))
        self.layout().setContentsMargins(px(12), px(12), px(12), px(12))
        self.img.setFixedSize(px(220), px(200))
        self.update_thumb()

    def update_thumb(self):
        pm = self.thumbs.get(self.path)
        if pm is not None:
            self.img.setPixmap(pm.scaled(px(220), px(200), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.img.setText("loading…")


class GroupView(QWidget):
    def __init__(self, page, similar: bool):
        super().__init__()
        self.page = page
        self.ctx = page.ctx
        self.similar = similar
        self.groups = []
        self.shown = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)

        self.find_btn = button("Find similar photos" if similar else "Find exact duplicates", primary=True)
        self.find_btn.clicked.connect(self.find)
        self.folder = QComboBox()
        self.folder.setMinimumWidth(220)
        self.folder.currentIndexChanged.connect(self.apply_filter)
        self.summary = QLabel("Click the button to search.")
        self.summary.setObjectName("pageSub")
        self.summary.setWordWrap(True)
        self.all_btn = button("Quarantine extras in ALL shown groups…", danger=True)
        self.all_btn.clicked.connect(self.quarantine_all)
        self.all_btn.setEnabled(False)
        page.actions += [self.find_btn]
        lay.addLayout(hrow(self.find_btn, QLabel("Folder:"), self.folder, self.summary, None, self.all_btn))

        split = QSplitter()
        self.list = QListWidget()
        self.list.setMinimumWidth(320)
        self.list.currentRowChanged.connect(self.show_group)
        split.addWidget(self.list)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.cards_box = QWidget()
        self.cards_lay = QHBoxLayout(self.cards_box)
        self.cards_lay.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(self.cards_box)
        rl.addWidget(sa, 1)
        self.q_btn = button("Move the other copies to quarantine", primary=True)
        self.q_btn.clicked.connect(self.quarantine_current)
        self.ignore_btn = button("Not duplicates - ignore this group")
        self.ignore_btn.clicked.connect(self.ignore_current)
        hint = QLabel("Exact copies are byte-for-byte identical - removing extras loses nothing." if not similar else
                      "Similar = same picture in another size/quality (e.g. WhatsApp copy) or a burst. The suggested keeper has the highest resolution, then sharpness. Check before removing.")
        hint.setObjectName("pageSub")
        hint.setWordWrap(True)
        rl.addLayout(hrow(self.q_btn, self.ignore_btn, hint))
        split.addWidget(right)
        split.setSizes([330, 900])
        lay.addWidget(split, 1)
        self.keep_group = None
        self.cards = []
        self.ctx.thumbs.ready.connect(self._thumb_ready)
        self._set_group_buttons(False)

    def _set_group_buttons(self, on):
        self.q_btn.setEnabled(on)
        self.ignore_btn.setEnabled(on)

    def find(self):
        fn = dupes.similar_groups if self.similar else dupes.exact_groups
        self.page.run("Finding similar photos" if self.similar else "Finding duplicates",
                      lambda conn, prog, cancel: fn(conn, self.ctx.settings, prog, cancel), self.loaded)

    def loaded(self, groups):
        self.groups = groups
        tops = sorted({f["top"] for g in groups for f in g["files"]})
        self.folder.blockSignals(True)
        self.folder.clear()
        self.folder.addItem("All folders", None)
        for t in tops:
            self.folder.addItem(t, t)
        self.folder.blockSignals(False)
        self.apply_filter()

    def apply_filter(self):
        t = self.folder.currentData()
        self.shown = [g for g in self.groups if t is None or any(f["top"] == t for f in g["files"])]
        self.list.clear()
        for g in self.shown:
            keep = g["files"][g["keep"]]
            it = QListWidgetItem(f"{len(g['files'])} {'photos' if self.similar else 'copies'} · {human(g['wasted'])} extra\n"
                                 f"keep: {keep['top']}\\{keep['name']}")
            self.list.addItem(it)
        wasted = sum(g["wasted"] for g in self.shown)
        extra = sum(len(g["files"]) - 1 for g in self.shown)
        self.summary.setText(f"{len(self.shown):,} groups · {extra:,} extra files · {human(wasted)}")
        self.all_btn.setEnabled(bool(self.shown) and not self.similar)
        if self.shown:
            self.list.setCurrentRow(0)
        else:
            self.show_group(-1)

    def show_group(self, idx):
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        if idx < 0 or idx >= len(self.shown):
            self._set_group_buttons(False)
            return
        g = self.shown[idx]
        self.keep_group = QButtonGroup(self)
        for i, f in enumerate(g["files"]):
            card = FileCard(f, self.ctx.thumbs, self.keep_group, i, self._quality_hint(g, i))
            self.cards_lay.addWidget(card)
            self.cards.append(card)
        self.keep_group.button(g["keep"]).setChecked(True)
        self.keep_group.idClicked.connect(lambda i: g.__setitem__("keep", i))
        self._set_group_buttons(True)

    def _quality_hint(self, g, i):
        sharp = g.get("sharpness")
        if not self.similar or not sharp or sharp[i] is None:
            return ""
        best = max(s or 0 for s in sharp)
        txt = f"Sharpness {sharp[i]:,.0f}" + (" (sharpest)" if sharp[i] == best and len(sharp) > 1 else "")
        return txt + (" · suggested keeper" if i == 0 else "")

    def _thumb_ready(self, path):
        for c in self.cards:
            if c.path == path:
                c.update_thumb()

    def _items_for(self, g):
        keep = g["files"][g["keep"]]["path"]
        return [(f["path"], None if self.similar else keep) for i, f in enumerate(g["files"]) if i != g["keep"]]

    def _remove_groups(self, gs):
        ids = {id(g) for g in gs}
        self.groups = [g for g in self.groups if id(g) not in ids]
        row = self.list.currentRow()
        self.apply_filter()
        if self.shown:
            self.list.setCurrentRow(min(row, len(self.shown) - 1))

    def quarantine_current(self):
        idx = self.list.currentRow()
        if idx < 0:
            return
        g = self.shown[idx]
        items = self._items_for(g)
        self.page.run("Moving to quarantine",
                      lambda conn, prog, cancel: quarantine.quarantine_files(
                          conn, self.ctx.settings, items, "similar" if self.similar else "duplicate",
                          "Similar photos" if self.similar else "Duplicate copies", prog, cancel),
                      lambda r: self._remove_groups([g]))

    def quarantine_all(self):
        gs = list(self.shown)
        items = [it for g in gs for it in self._items_for(g)]
        size = sum(g["wasted"] for g in gs)
        if not confirm(self, "Quarantine all", f"Move {len(items):,} extra copies ({human(size)}) to quarantine?\n\n"
                       "For each group the copy marked 'Keep' stays where it is. Every file is re-checked to be "
                       "byte-identical first. You can restore everything from the Quarantine page or undo it in "
                       "the Activity log."):
            return
        self.page.run("Moving to quarantine",
                      lambda conn, prog, cancel: quarantine.quarantine_files(
                          conn, self.ctx.settings, items, "duplicate", "Duplicate copies", prog, cancel),
                      lambda r: (self._remove_groups(gs),
                                 info(self, "Done", f"{r['moved']:,} files ({human(r['bytes'])}) moved to quarantine."
                                      + (f"\n{r['skipped']:,} skipped (changed or missing)." if r["skipped"] else ""))))

    def ignore_current(self):
        idx = self.list.currentRow()
        if idx < 0:
            return
        g = self.shown[idx]
        dupes.ignore_group(self.ctx.conn, g["key"])
        self._remove_groups([g])


class DuplicatesPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Duplicates", "Find copies of the same photo. Extras go to quarantine - never deleted "
                                           "directly. Hand-picked folders are preferred as the copy to keep."))
        tabs = QTabWidget()
        tabs.addTab(GroupView(self, similar=False), "Exact copies")
        tabs.addTab(GroupView(self, similar=True), "Similar photos")
        lay.addWidget(tabs, 1)
