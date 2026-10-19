"""Organize, Import and Backup pages."""
import os
import shutil
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QSplitter, QTextEdit,
                               QVBoxLayout, QWidget, QAbstractItemView)

from ..core import backup, config, importer, journal, organize, scanner
from .common import (BAD, GOOD, WARN, button, confirm, fill_table, header, hrow, human, info, make_table,
                     panel, section_title)
from .pages_a import Page


def big_result(lbl: QLabel, text: str, color: str):
    lbl.setText(text)
    lbl.setStyleSheet(f"background:{color}22; border:1px solid {color}; color:{color};")
    lbl.show()


# =================================================================== Organize
class OrganizePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.plan = None
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header(
            "Organize",
            "Sorts everything into a clean structure under the Photos folder: Life\\Year\\Year-Month for camera "
            "photos, hand-picked folders kept intact as events or Collections, plus Chat Media, Screenshots, Gaming, "
            "Projects and Documents. Always shows a preview first, and can be undone."))
        self.plan_btn = button("1. Build plan (preview)", primary=True)
        self.plan_btn.clicked.connect(self.build)
        self.apply_btn = button("2. Apply plan…", danger=True)
        self.apply_btn.clicked.connect(self.apply)
        self.apply_btn.setEnabled(False)
        self.undo_btn = button("Undo last organize")
        self.undo_btn.clicked.connect(self.undo)
        self.actions += [self.plan_btn, self.undo_btn]
        lay.addLayout(hrow(self.plan_btn, self.apply_btn, None, self.undo_btn))
        self.summary = QLabel("Scan the library first (Dashboard), then build a plan. Nothing moves until you click Apply.")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("pageSub")
        lay.addWidget(self.summary)

        split = QSplitter()
        self.groups = make_table(["Destination", "Files"], stretch_col=0, selection=QAbstractItemView.SingleSelection)
        self.groups.itemSelectionChanged.connect(self.show_items)
        split.addWidget(self.groups)
        self.items = make_table(["From (current place)", "To (new place)"])
        split.addWidget(self.items)
        split.setSizes([320, 900])
        lay.addWidget(split, 1)

    def build(self):
        self.apply_btn.setEnabled(False)
        self.run("Building organize plan",
                 lambda conn, prog, cancel: organize.build_plan(conn, self.ctx.settings, prog, cancel), self.loaded)

    def loaded(self, plan):
        self.plan = plan
        n = len(plan["items"])
        self.summary.setText(
            f"<b>{n:,} files ({human(plan['total_bytes'])})</b> would be moved into <b>{self.ctx.settings.organized_root}</b>. "
            f"{plan['skipped_junk']:,} junk files (thumbnails caches, .nomedia, shortcuts) are left alone - "
            f"clean them in Health check. {plan['skipped_rule']:,} files skipped by your folder rules. "
            f"Folder rules can be changed in Settings.")
        self.summary.setTextFormat(Qt.RichText)
        fill_table(self.groups, [(g, (f"{c:,}", c)) for g, c in plan["summary"]])
        self.apply_btn.setEnabled(n > 0 and not self.ctx.tasks.busy)
        if plan["summary"]:
            self.groups.selectRow(0)

    def show_items(self):
        if not self.plan:
            return
        rows = self.groups.selectionModel().selectedRows()
        if not rows:
            return
        grp = self.groups.item(rows[0].row(), 0).text()
        org = self.ctx.settings.organized_root
        try:
            base = os.path.dirname(os.path.commonpath(self.ctx.settings.library_roots))
        except ValueError:
            base = ""

        def short(p):
            return os.path.relpath(p, base) if base and p.lower().startswith(base.lower()) else p

        items = [it for it in self.plan["items"] if it["dest_group"] == grp]
        fill_table(self.items, [(short(it["src"]), os.path.relpath(it["dst"], org)) for it in items], limit=5000)

    def apply(self):
        if not self.plan:
            return
        n = len(self.plan["items"])
        if not confirm(self, "Apply organize plan",
                       f"Move {n:,} files into the new structure?\n\n"
                       "• Files are moved (not copied) - instant on the same drive.\n"
                       "• Every move is logged; 'Undo last organize' puts everything back.\n"
                       "• Tip: have a safety copy on your external disk before big changes.\n\nContinue?"):
            return
        plan = self.plan
        self.apply_btn.setEnabled(False)

        def done(r):
            self.plan = None
            self.groups.setRowCount(0)
            self.items.setRowCount(0)
            self.summary.setText(f"Done: {r['moved']:,} files organized, {r['skipped']:,} skipped, "
                                 f"{r['removed_dirs']:,} empty folders removed.")
            info(self, "Organize finished", self.summary.text())

        self.run("Organizing", lambda conn, prog, cancel: organize.apply(conn, self.ctx.settings, plan, prog, cancel), done)

    def undo(self):
        row = self.ctx.conn.execute("SELECT * FROM ops WHERE kind='organize' AND undoable=1 AND undone=0 "
                                    "ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            info(self, "Undo", "There is no organize operation to undo.")
            return
        if not confirm(self, "Undo organize", f"Undo this?\n\n{row['summary']}\n"
                       f"({time.strftime('%d %b %Y %H:%M', time.localtime(row['ts']))})\n\nAll files go back to where they were."):
            return
        s = self.ctx.settings
        self.run("Undoing organize",
                 lambda conn, prog, cancel: journal.undo(conn, row["id"], s.library_roots, [s.quarantine_dir], prog, cancel),
                 lambda r: info(self, "Undo finished", f"{r['restored']:,} files moved back, {r['skipped']:,} skipped."))


# =================================================================== Import
class ImportPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header(
            "Import",
            "Bring new photos (phone, camera card, USB) into the Inbox. Photos you already have are skipped, every "
            "copy is verified, and you get a clear answer whether it's safe to delete them from the phone."))
        p, pl = panel()
        pl.addWidget(section_title("Source"))
        tip = QLabel("Phones: Windows doesn't give apps a normal folder for phone storage. Open the phone in File "
                     "Explorer, copy its DCIM folder to the laptop (e.g. C:\\Users\\you\\Pictures\\_PhoneDump), then choose that "
                     "folder here. A memory card or USB stick can be chosen directly.")
        tip.setWordWrap(True)
        tip.setObjectName("pageSub")
        pl.addWidget(tip)
        self.src = QLineEdit()
        self.src.setPlaceholderText(r"e.g. C:\Users\you\Pictures\_PhoneDump\DCIM")
        browse = button("Browse…")
        browse.clicked.connect(self.browse)
        pl.addLayout(hrow(QLabel("Folder:"), self.src, browse, stretch=False))
        self.label = QLineEdit()
        self.label.setPlaceholderText("Device name, e.g. Pixel 9")
        pl.addLayout(hrow(QLabel("Device name:"), self.label))
        self.count_btn = button("Count files")
        self.count_btn.clicked.connect(self.count)
        self.import_btn = button("Import to Inbox", primary=True)
        self.import_btn.clicked.connect(self.do_import)
        self.count_lbl = QLabel("")
        self.actions += [self.import_btn]
        pl.addLayout(hrow(self.count_btn, self.import_btn, self.count_lbl))
        lay.addWidget(p)

        self.result = QLabel()
        self.result.setObjectName("bigResult")
        self.result.setWordWrap(True)
        self.result.hide()
        lay.addWidget(self.result)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        lay.addWidget(self.details)
        self.open_btn = button("Open Inbox folder")
        self.open_btn.clicked.connect(lambda: os.path.isdir(self.ctx.settings.inbox) and os.startfile(self.ctx.settings.inbox))
        lay.addLayout(hrow(self.open_btn))
        lay.addWidget(section_title("Skipped because you already have them"))
        self.already = make_table(["Incoming file", "Already in library as"])
        lay.addWidget(self.already, 1)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder to import", self.src.text() or config._pictures())
        if d:
            self.src.setText(os.path.normpath(d))
            if not self.label.text():
                self.label.setText(os.path.basename(os.path.normpath(d)))

    def count(self):
        src = self.src.text().strip()
        if not os.path.isdir(src):
            info(self, "Import", "Choose an existing folder first.")
            return
        c = importer.count_check(src)
        self.count_lbl.setText(f"{c.get('image', 0):,} photos · {c.get('video', 0):,} videos · "
                               f"{c.get('audio', 0) + c.get('other', 0):,} other  - compare with the count on your phone")

    def do_import(self):
        src = self.src.text().strip()
        if not os.path.isdir(src):
            info(self, "Import", "Choose an existing folder first.")
            return
        label = self.label.text().strip() or os.path.basename(src)
        self.result.hide()

        def done(r):
            if r["safe_to_delete"]:
                big_result(self.result, f"✔ All {r['total']:,} files are safely in your library "
                                        f"({r['copied']:,} new + {r['already']:,} you already had). "
                                        "It is safe to delete them from the source - ideally after your next backup.", GOOD)
            elif not r["complete"]:
                big_result(self.result, "Import was cancelled - do NOT delete anything from the source yet.", WARN)
            else:
                big_result(self.result, f"✖ {len(r['failed']):,} files could not be imported - do NOT delete the "
                                        "source yet. See details below.", BAD)
            fails = "".join(f"<br>• {os.path.basename(p)}: {e}" for p, e in r["failed"][:30])
            self.details.setText(f"Copied to: {r['dest']}<br>New: {r['copied']:,} · Already had: {r['already']:,} · "
                                 f"Failed: {len(r['failed']):,}{fails}")
            fill_table(self.already, [(a, b) for a, b in r["already_list"]])

        self.run("Importing", lambda conn, prog, cancel: importer.import_folder(conn, self.ctx.settings, src, label, prog, cancel), done)


# =================================================================== Backup
class BackupPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header(
            "Backup",
            "Mirror + safety archive: the backup becomes an exact copy of your library, but nothing on the backup "
            "disk is ever deleted - files you removed or changed are moved to _Deleted_Archive\\<date> first. "
            "Every copied file is verified."))

        p, pl = panel()
        pl.addWidget(section_title("What to back up"))
        self.sources = QListWidget()
        self.sources.setMaximumHeight(90)
        pl.addWidget(self.sources)
        add = button("Add folder…")
        add.clicked.connect(self.add_source)
        rem = button("Remove selected")
        rem.clicked.connect(self.remove_source)
        pl.addLayout(hrow(add, rem))
        self.target = QLineEdit()
        tb = button("Browse…")
        tb.clicked.connect(self.browse_target)
        self.target.editingFinished.connect(self.save_target)
        pl.addLayout(hrow(QLabel("Backup to:"), self.target, tb, stretch=False))
        self.status = QLabel("")
        pl.addWidget(self.status)
        lay.addWidget(p)

        self.preview_btn = button("Preview")
        self.preview_btn.clicked.connect(self.preview)
        self.run_btn = button("Back up now", primary=True)
        self.run_btn.clicked.connect(self.backup_now)
        self.qv_btn = button("Quick verify")
        self.qv_btn.clicked.connect(lambda: self.verify(False))
        self.fv_btn = button("Full verify (slow)")
        self.fv_btn.clicked.connect(lambda: self.verify(True))
        self.rf_btn = button("Restore a file…")
        self.rf_btn.clicked.connect(lambda: self.restore(False))
        self.rd_btn = button("Restore a folder…")
        self.rd_btn.clicked.connect(lambda: self.restore(True))
        self.actions += [self.preview_btn, self.run_btn, self.qv_btn, self.fv_btn]
        lay.addLayout(hrow(self.preview_btn, self.run_btn, self.qv_btn, self.fv_btn, None, self.rf_btn, self.rd_btn))

        self.result = QLabel()
        self.result.setObjectName("bigResult")
        self.result.setWordWrap(True)
        self.result.hide()
        lay.addWidget(self.result)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(110)
        self.details.hide()
        lay.addWidget(self.details)
        lay.addWidget(section_title("History"))
        self.history = make_table(["Date", "Duration", "New", "Updated", "Archived", "Copied", "Errors", "Status"], stretch_col=7)
        lay.addWidget(self.history, 1)

    def on_show(self):
        s = self.ctx.settings
        self.sources.clear()
        self.sources.addItems(s.backup_sources)
        self.target.setText(s.backup_target)
        self.refresh_status()
        self.refresh_history()

    def refresh_status(self):
        s = self.ctx.settings
        ok, msg = backup.target_ready(s)
        missing = [x for x in s.backup_sources if not os.path.isdir(x)]
        parts = []
        if ok:
            drive = os.path.splitdrive(s.backup_target)[0] + "\\"
            u = shutil.disk_usage(drive)
            parts.append(f"<span style='color:{GOOD}'>● Backup disk connected</span> - {human(u.free)} free of {human(u.total)}")
        else:
            parts.append(f"<span style='color:{BAD}'>● {msg}</span> - plug in your external disk")
        if missing:
            parts.append(f"<span style='color:{WARN}'>Not found (skipped): {', '.join(missing)}</span>")
        self.status.setText("<br>".join(parts))

    def refresh_history(self):
        rows = []
        for r in backup.history(self.ctx.conn):
            dur = int((r["ts_end"] or r["ts_start"]) - r["ts_start"])
            rows.append((time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts_start"])), f"{dur // 60}m {dur % 60}s",
                         (f"{r['copied']:,}", r["copied"]), (f"{r['updated']:,}", r["updated"]),
                         (f"{r['archived']:,}", r["archived"]), (human(r["bytes"]), r["bytes"]),
                         (f"{r['errors']:,}", r["errors"]), r["status"] or ""))
        fill_table(self.history, rows)

    def _save(self):
        s = self.ctx.settings
        s.backup_sources = [self.sources.item(i).text() for i in range(self.sources.count())]
        s.backup_target = self.target.text().strip()
        s.save()
        self.refresh_status()

    def add_source(self):
        d = QFileDialog.getExistingDirectory(self, "Folder to back up", config._pictures())
        if d:
            self.sources.addItem(os.path.normpath(d))
            self._save()

    def remove_source(self):
        for it in self.sources.selectedItems():
            self.sources.takeItem(self.sources.row(it))
        self._save()

    def browse_target(self):
        d = QFileDialog.getExistingDirectory(self, "Backup destination", self.target.text() or "F:\\")
        if d:
            self.target.setText(os.path.normpath(d))
            self._save()

    def save_target(self):
        self._save()

    def preview(self):
        self._save()

        def done(p):
            per = "".join(f"<br>• {s['src']} → {s['dst']}: {len(s['new']):,} new, {len(s['changed']):,} changed, "
                          f"{len(s['removed']):,} to archive" for s in p["sources"])
            big_result(self.result, f"Next backup: {p['new']:,} new files, {p['changed']:,} changed, {p['removed']:,} "
                                    f"moved to the archive · {human(p['bytes'])} to copy.{per}",
                       WARN if p["new"] + p["changed"] + p["removed"] else GOOD)
            self.result.setTextFormat(Qt.RichText)

        ok, msg = backup.target_ready(self.ctx.settings)
        if not ok:
            info(self, "Backup", msg)
            return
        self.run("Comparing with backup", lambda conn, prog, cancel: backup.plan(self.ctx.settings, prog, cancel), done)

    def backup_now(self):
        self._save()
        ok, msg = backup.target_ready(self.ctx.settings)
        if not ok:
            info(self, "Backup", msg)
            return

        def done(r):
            color = GOOD if r["status"] == "ok" else WARN if r["status"] in ("done with errors", "cancelled") else BAD
            big_result(self.result, f"Backup {r['status']}: {r['copied']:,} new, {r['updated']:,} updated, "
                                    f"{r['archived']:,} archived, {human(r['bytes'])} copied in "
                                    f"{r['seconds'] // 60}m {r['seconds'] % 60}s. Errors: {r['errors']:,}", color)
            if r["error_list"]:
                self.details.setPlainText("\n".join(r["error_list"]))
                self.details.show()
            else:
                self.details.hide()
            self.refresh_history()
            self.refresh_status()

        self.run("Backing up", lambda conn, prog, cancel: backup.run(conn, self.ctx.settings, prog, cancel), done)

    def verify(self, full):
        self._save()

        def done(v):
            bad = len(v["missing"]) + len(v["different"])
            if bad == 0:
                big_result(self.result, f"✔ Backup verified ({'contents' if full else 'names and sizes'}): "
                                        f"all {v['checked']:,} files are on the backup and match.", GOOD)
                self.details.hide()
            else:
                big_result(self.result, f"✖ {len(v['missing']):,} files missing and {len(v['different']):,} different "
                                        "on the backup - run 'Back up now' to fix.", BAD)
                self.details.setPlainText("\n".join(["MISSING: " + x for x in v["missing"][:300]] +
                                                    ["DIFFERENT: " + x for x in v["different"][:300]]))
                self.details.show()

        self.run("Verifying backup", lambda conn, prog, cancel: backup.verify(self.ctx.settings, full, prog, cancel), done)

    def restore(self, folder):
        start = self.ctx.settings.backup_target
        if folder:
            path = QFileDialog.getExistingDirectory(self, "Folder to restore from the backup", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "File to restore from the backup", start)
        if not path:
            return
        try:
            dst = backup.restore(self.ctx.settings, os.path.normpath(path))
        except OSError as e:
            info(self, "Restore failed", str(e))
            return
        scanner.index_paths([dst] if os.path.isfile(dst) else
                            [os.path.join(dp, f) for dp, _, fs in os.walk(dst) for f in fs], self.ctx.settings, self.ctx.conn)
        self.ctx.conn.commit()
        info(self, "Restored", f"Restored to:\n{dst}")
