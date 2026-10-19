"""AI pages: Search (semantic + people), People (name faces), AI Index (build/refresh the index)."""
import os
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QDoubleSpinBox, QHeaderView, QInputDialog, QListView, QListWidget, QListWidgetItem, QMenu,
                               QProgressBar, QPushButton, QScrollArea, QSplitter, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..ai import paths, people, setup_env, store
from ..ai.client import AiClient
from .common import button, confirm, header, hrow, info, open_file, px, show_in_explorer, square_pixmap
from .pages_a import Page


class _Sig(QObject):
    done = Signal(object)
    fail = Signal(str)


class _Job(QRunnable):
    """Runs fn() on the thread pool; result comes back on the UI thread via signals."""

    def __init__(self, fn):
        super().__init__()
        self.fn, self.sig = fn, _Sig()

    def run(self):
        try:
            self.sig.done.emit(self.fn())
        except Exception as e:  # shown to the user, never crashes the UI
            self.sig.fail.emit(f"{type(e).__name__}: {e}")


def call_async(fn, on_done, on_fail=None):
    job = _Job(fn)
    job.setAutoDelete(False)
    job.sig.done.connect(on_done)
    job.sig.fail.connect(on_fail or (lambda m: info(None, "AI worker", m)))
    QThreadPool.globalInstance().start(job)
    return job  # keep a reference so the signals stay alive


class SetupController(QObject):
    """Runs the first-run AI setup on a thread; shared by all AI pages (one per app via ctx)."""
    changed = Signal()
    progress = Signal(str, float, str)   # stage, fraction (-1 = unknown), text

    def __init__(self):
        super().__init__()
        self.running = False
        self.error = ""
        self.setup = None
        self._cb = lambda stage, frac, text: self.progress.emit(stage, -1.0 if frac is None else frac, text)

    def start(self, factory=None):
        if self.running:
            return
        self.error = ""
        try:
            self.setup = (factory or setup_env.AiSetup)(progress=self._cb)
        except Exception as e:
            self.error = f"Could not start the AI setup: {e}"
            self.changed.emit()
            return
        self.running = True
        self.changed.emit()

        def work():
            try:
                self.setup.run()
            except setup_env.SetupCancelled:
                self.error = "Setup cancelled. Click Enable AI features to resume."
            except Exception as e:
                self.error = str(e)
            self.running = False
            self.changed.emit()
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.setup and self.running:
            self.setup.cancel()


def setup_controller(ctx) -> SetupController:
    if not hasattr(ctx, "ai_setup"):
        ctx.ai_setup = SetupController()
    return ctx.ai_setup


class EnableAiPanel(QFrame):
    """'Enable AI features' button + progress; hidden once the AI environment and models are installed."""

    def __init__(self, ctx, on_ready=None):
        super().__init__()
        self.ctx, self.on_ready = ctx, on_ready
        self.ctl = setup_controller(ctx)
        lay = QVBoxLayout(self)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.bar = QProgressBar()
        self.go_btn = button("Enable AI features", primary=True)
        self.cancel_btn = button("Cancel")
        self.go_btn.clicked.connect(lambda _checked=False: self.ctl.start())
        self.cancel_btn.clicked.connect(self.ctl.cancel)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addLayout(hrow(self.go_btn, self.cancel_btn))
        self.ctl.changed.connect(self.refresh)
        self.ctl.progress.connect(self._progress)
        self.refresh()

    def _progress(self, stage, frac, text):
        self.bar.setRange(0, 0) if frac < 0 else (self.bar.setRange(0, 1000), self.bar.setValue(int(frac * 1000)))
        self.label.setText(text)

    def refresh(self):
        ready = get_client(self.ctx).available and setup_env.models_ready()
        running = self.ctl.running
        self.setVisible(not ready or running or bool(self.ctl.error))
        self.go_btn.setVisible(not running and not ready)
        self.cancel_btn.setVisible(running)
        self.bar.setVisible(running)
        if self.ctl.error:
            self.label.setText(self.ctl.error)
        elif not running and not ready:
            self.label.setText("AI features (search by description, people) are not installed yet. This downloads "
                               "the AI packages and models once (~1.5 GB, needs Python 3.13 and internet). "
                               "Your photos are not uploaded anywhere.")
        if ready and not running and self.on_ready:
            self.on_ready()


def get_client(ctx) -> AiClient:
    if not hasattr(ctx, "ai"):
        ctx.ai = AiClient()
    return ctx.ai


def ai_conn(ctx):
    if not hasattr(ctx, "ai_conn"):
        ctx.ai_conn = store.connect()
    return ctx.ai_conn


def eta_text(sec) -> str:
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


# =================================================================== Search
class SearchPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.items = {}
        self.jobs = []
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Search", "Describe a photo in English, French, German or Arabic - or type a name you "
                                       "gave in People, e.g. “Mom”, “Nils at the beach”, “Mom and Nils”."))
        lay.addWidget(EnableAiPanel(ctx))
        self.query = QLineEdit()
        self.query.setPlaceholderText("beach sunset · plage · Mom · Nils and Mom at a restaurant …")
        self.query.returnPressed.connect(self.go)
        self.year = QComboBox()
        self.year.addItem("Any year", None)
        for y in range(time.localtime().tm_year, 1999, -1):
            self.year.addItem(str(y), str(y))
        btn = button("Search", primary=True)
        btn.clicked.connect(self.go)
        self.actions.append(btn)
        lay.addLayout(hrow(self.query, self.year, btn))
        self.tag_filters = []
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Filter by tag (beach, birthday, an event …) - press Enter to add")
        self.tag_edit.setMaximumWidth(380)
        self.tag_edit.returnPressed.connect(self.add_tag_from_edit)
        self.chips = QHBoxLayout()
        lay.addLayout(hrow(self.tag_edit, self.chips, stretch=False))
        self.status = QLabel("")
        self.status.setObjectName("pageSub")
        lay.addWidget(self.status)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self._grid_sizes()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.itemDoubleClicked.connect(lambda it: open_file(it.data(Qt.UserRole)))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.menu)
        lay.addWidget(self.grid, 1)
        ctx.thumbs.ready.connect(self._thumb_ready)

    def set_query(self, text):
        self.query.setText(text)
        self.go()

    def set_tag_filters(self, names):
        """Replace the tag chips (used by the Tags page 'open in Search')."""
        self.tag_filters = list(dict.fromkeys(names))
        self.render_chips()

    def add_tag_from_edit(self):
        nm = self.tag_edit.text().strip()
        if nm and nm.lower() not in [t.lower() for t in self.tag_filters]:
            self.tag_filters.append(nm)
            self.render_chips()
        self.tag_edit.clear()
        self.go()

    def remove_tag(self, nm):
        self.tag_filters = [t for t in self.tag_filters if t != nm]
        self.render_chips()
        self.go()

    def render_chips(self):
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        for nm in self.tag_filters:
            b = button(f"{nm}  ✕")
            b.setToolTip("Remove this tag filter")
            b.clicked.connect(lambda _=False, n=nm: self.remove_tag(n))
            self.chips.addWidget(b)
        self.chips.addStretch(1)

    def on_show(self):
        try:
            names = [r["name"] for r in ai_conn(self.ctx).execute("SELECT DISTINCT name FROM tag ORDER BY name")]
        except Exception:
            names = []
        self.tag_edit.setCompleter(QCompleter(names, self.tag_edit))

    def go(self):
        ai = get_client(self.ctx)
        if not ai.available:
            self.status.setText("AI not set up: click Enable AI features above (or on the AI Index page).")  # inline, not modal: a dialog hangs headless runs
            return
        q, year, tags = self.query.text().strip(), self.year.currentData(), list(self.tag_filters)
        self.status.setText("Searching…  (the first search loads the model, ~10 s)")
        self.jobs = [call_async(lambda: ai.request("search", q=q, year=year, tags=tags, limit=300), self.show_results,
                                lambda m: self.status.setText("Search failed: " + m))]

    def show_results(self, res):
        self.grid.clear()
        self.items = {}
        rows = res["results"]
        parts = []
        if res["people"]:
            parts.append("people: " + ", ".join(res["people"]))
        if res.get("tags"):
            parts.append("tags: " + ", ".join(res["tags"]))
        if res["text"]:
            parts.append(f"text: “{res['text']}”")
        self.status.setText(f"{len(rows):,} photos shown · " + (" · ".join(parts) or "newest first") +
                            f" · {res['ms']} ms")
        for r in rows:
            th = str(paths.THUMBS / f"{r['hash']}.jpg")
            it = QListWidgetItem(os.path.basename(r["path"]))
            it.setData(Qt.UserRole, r["path"])
            it.setToolTip(r["path"] + (f"\nscore {r['score']:.3f}" if r["score"] else ""))
            blank = QPixmap(self.grid.iconSize())
            blank.fill(Qt.transparent)
            it.setIcon(QIcon(blank))
            self.grid.addItem(it)
            self.items[th] = it
            pm = self.ctx.thumbs.get(th)
            if pm is not None:
                it.setIcon(QIcon(square_pixmap(pm, self.grid.iconSize().width())))

    def _grid_sizes(self):
        self.grid.setIconSize(QSize(px(190), px(190)))
        self.grid.setGridSize(QSize(px(210), px(224)))
        self.grid.setSpacing(px(6))

    def on_zoom(self):
        super().on_zoom()
        self._grid_sizes()
        self._refresh_icons()

    def _refresh_icons(self):
        for path in list(self.items):
            self._thumb_ready(path)

    def _thumb_ready(self, path):
        it = self.items.get(path)
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
        m.addAction("Show in Explorer", lambda: show_in_explorer(it.data(Qt.UserRole)))
        m.exec(self.grid.mapToGlobal(pos))


# =================================================================== People
FACE_PX = 84


def face_pixmap(face_id):
    pm = QPixmap(str(paths.FACE_CROPS / f"{face_id}.jpg"))
    return square_pixmap(pm, FACE_PX) if not pm.isNull() else QPixmap(FACE_PX, FACE_PX)


class ClusterCard(QFrame):
    def __init__(self, page, c):
        super().__init__()
        self.page, self.c = page, c
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        faces = QHBoxLayout()
        for fid in c["faces"][:8]:
            lb = QLabel()
            lb.setPixmap(face_pixmap(fid))
            faces.addWidget(lb)
        faces.addStretch(1)
        lay.addLayout(faces)
        self.name = QLineEdit()
        self.name.setMinimumWidth(260)
        self.name.setPlaceholderText(f"Who is this? ({c['size']} faces)")
        self.name.setCompleter(QCompleter([p["name"] for p in people.persons(page.conn)]))
        self.name.returnPressed.connect(self.save)
        ok = button("Save name", primary=True)
        ok.clicked.connect(self.save)
        no = button("Not a person")
        no.clicked.connect(self.reject)
        lay.addLayout(hrow(self.name, ok, no))

    def save(self):
        nm = self.name.text().strip()
        if nm:
            people.name_cluster(self.page.conn, self.c["cluster"], nm)
            self.page.card_done(self, f"Saved “{nm}”")

    def reject(self):
        people.reject_cluster(self.page.conn, self.c["cluster"])
        self.page.card_done(self, "Hidden")


class PeoplePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.jobs = []
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("People", "Faces are grouped automatically. Type a name once per group (“Mom”, “Nils”) "
                                       "and every photo in that group is labelled. Then search by name."))
        lay.addWidget(EnableAiPanel(ctx))
        find = button("Find groups")
        find.setToolTip("Group the unnamed faces by similarity")
        find.clicked.connect(self.recluster)
        match = button("Match known people")
        match.setToolTip("Give unnamed faces to people you already named, when they look similar enough")
        match.clicked.connect(self.assign)
        self.actions += [find, match]
        lay.addLayout(hrow(find, match, stretch=False))
        self.status = QLabel("")
        self.status.setObjectName("pageSub")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.cards_box = QVBoxLayout()
        inner = QWidget()
        inner.setLayout(self.cards_box)
        self.cards_box.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        row.addWidget(sa, 3)
        side = QVBoxLayout()
        side.addWidget(QLabel("Named people (double-click to see their photos)"))
        self.named = QListWidget()
        self.named.itemDoubleClicked.connect(self.open_person)
        side.addWidget(self.named, 1)
        row.addLayout(side, 1)
        lay.addLayout(row, 1)

    @property
    def conn(self):
        return ai_conn(self.ctx)

    def on_show(self):
        self.refresh()

    def refresh(self):
        while self.cards_box.count() > 1:
            w = self.cards_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        cl = people.clusters(self.conn, limit=40)
        for c in cl:
            self.cards_box.insertWidget(self.cards_box.count() - 1, ClusterCard(self, c))
        st = store.stats(self.conn)
        self.status.setText(f"{st['faces']:,} faces found · {st['faces_named']:,} named · "
                            f"{len(cl)} groups waiting for a name" + (
                                "" if st["faces"] else " - build the index first (AI Index page)"))
        self.named.clear()
        for p in people.persons(self.conn):
            it = QListWidgetItem(f"{p['name']}  -  {p['photos']:,} photos")
            it.setData(Qt.UserRole, p["name"])
            self.named.addItem(it)

    def card_done(self, card, msg):
        card.deleteLater()
        self.status.setText(msg)
        QTimer.singleShot(0, self.refresh_named)

    def refresh_named(self):
        self.named.clear()
        for p in people.persons(self.conn):
            it = QListWidgetItem(f"{p['name']}  -  {p['photos']:,} photos")
            it.setData(Qt.UserRole, p["name"])
            self.named.addItem(it)

    def open_person(self, it):
        self.ctx.main.open_search(it.data(Qt.UserRole))

    def _bg(self, label, cmd, done):
        ai = get_client(self.ctx)
        self.status.setText(label)
        self.jobs = [call_async(lambda: ai.request(cmd), done,
                                lambda m: self.status.setText("Failed: " + m))]

    def recluster(self):
        self._bg("Grouping faces…", "recluster", lambda r: (self.refresh(),
                                                          self.status.setText(f"Found {r['clusters']} groups")))

    def assign(self):
        self._bg("Matching faces to known people…", "assign",
                 lambda r: (self.refresh(), self.status.setText(f"{r['assigned']:,} faces matched to named people")))


# =================================================================== Tags
KIND_TITLES = {"person": "People", "event": "Events", "thing": "Things"}
THUMB_PX = 150


def fmt_day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"


def event_range(first_ts, last_ts) -> str:
    a, b = fmt_day(first_ts), fmt_day(last_ts)
    return a if a == b else f"{a} to {b}"


class TagsPage(Page):
    """Browse tags by kind, review auto tags, add/remove tags by hand, and browse events."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.jobs = []
        self.items = {}
        self.cur = None        # (kind, tag_id or person name)
        self.cur_hash = None
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Tags", "Things, events and people found in your photos. Auto tags (italic, with a score) "
                                     "are suggestions: accept or reject them. Accepted and manual tags are kept."))
        lay.addWidget(EnableAiPanel(ctx))
        self.events_btn = button("Compute events")
        self.events_btn.setToolTip("Group photos into events by date gaps and GPS jumps (renamed events are kept)")
        self.events_btn.clicked.connect(self.compute_events)
        self.search_btn = button("Open tag in Search")
        self.search_btn.clicked.connect(self.open_in_search)
        self.actions += [self.events_btn, self.search_btn]
        lay.addLayout(hrow(self.events_btn, self.search_btn, stretch=False))
        self.status = QLabel("")
        self.status.setObjectName("pageSub")
        lay.addWidget(self.status)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # --- tab 1: tags
        tab = QWidget()
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Tag", "Photos", "To review"])
        self.tree.currentItemChanged.connect(self.pick_tag)
        split.addWidget(self.tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.rename_btn = button("Rename event")
        self.rename_btn.clicked.connect(self.rename_event)
        self.thr = QDoubleSpinBox()
        self.thr.setDecimals(2)
        self.thr.setSingleStep(0.05)
        self.thr.setRange(0.0, 1.0)
        self.thr.setValue(0.30)
        self.thr.setToolTip("Minimum auto score")
        self.accept_all_btn = button("Accept all above")
        self.accept_all_btn.clicked.connect(self.accept_all)
        rl.addLayout(hrow(self.rename_btn, QLabel("Accept auto tags with score ≥"), self.thr,
                          self.accept_all_btn, stretch=False))
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self._grid_sizes()
        self.grid.currentItemChanged.connect(self.pick_photo)
        self.grid.itemDoubleClicked.connect(lambda it: open_file(it.data(Qt.UserRole)["path"]))
        rl.addWidget(self.grid, 1)
        rl.addWidget(QLabel("Tags on the selected photo (auto = italic with score)"))
        self.photo_tags = QListWidget()
        self.photo_tags.setMaximumHeight(130)
        rl.addWidget(self.photo_tags)
        self.accept_btn = button("Accept")
        self.accept_btn.clicked.connect(self.accept_selected)
        self.remove_btn = button("Reject / remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("Add a tag to this photo (e.g. holiday)")
        self.add_edit.returnPressed.connect(self.add_manual)
        add = button("Add tag", primary=True)
        add.clicked.connect(self.add_manual)
        add_row = hrow(self.accept_btn, self.remove_btn, self.add_edit, add)
        add_row.setStretchFactor(self.add_edit, 1)  # keep the placeholder readable
        rl.addLayout(add_row)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        self.tree.setMinimumWidth(300)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        split.setSizes([320, 900])
        tl = QVBoxLayout(tab)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(split)
        self.tabs.addTab(tab, "Tags")

        # --- tab 2: events
        etab = QWidget()
        el = QVBoxLayout(etab)
        el.addWidget(QLabel("Events (double-click to see its photos)"))
        self.events = QListWidget()
        self.events.itemDoubleClicked.connect(self.open_event)
        el.addWidget(self.events, 1)
        self.tabs.addTab(etab, "Events")
        ctx.thumbs.ready.connect(self._thumb_ready)

    @property
    def conn(self):
        return ai_conn(self.ctx)

    def on_show(self):
        self.refresh()

    # ----- data
    def refresh(self):
        keep = self.cur
        self.tree.clear()
        tags = store.tag_counts(self.conn)
        by_kind = {"person": [], "event": [], "thing": []}
        for t in tags:
            if t["kind"] != "person":
                by_kind[t["kind"]].append(("tag", t["id"], t["name"], t["n"], t["n_auto"]))
        for p in people.persons(self.conn):
            if p["photos"]:
                by_kind["person"].append(("person", p["name"], p["name"], p["photos"], 0))
        select = None
        for kind in ("person", "event", "thing"):
            top = QTreeWidgetItem([f"{KIND_TITLES[kind]}  ({len(by_kind[kind])})", "", ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for typ, key, name, n, n_auto in by_kind[kind]:
                it = QTreeWidgetItem([name, f"{n:,}", f"{n_auto:,}" if n_auto else ""])
                it.setData(0, Qt.UserRole, (kind, key, name))
                top.addChild(it)
                if keep and keep[:2] == (kind, key):
                    select = it
            top.setExpanded(kind != "event")
        if select is not None:
            self.tree.setCurrentItem(select)
        st = [f"{len(by_kind[k])} {KIND_TITLES[k].lower()}" for k in ("person", "event", "thing")]
        if not tags and not by_kind["person"]:
            self.status.setText("No tags yet - build the AI index first (AI Index page), then compute events.")
        else:
            self.status.setText(" · ".join(st))
        self.refresh_events()

    def refresh_events(self):
        self.events.clear()
        for e in store.list_events(self.conn):
            it = QListWidgetItem(f"{event_range(e['first_ts'], e['last_ts'])}  -  {e['n']:,} photos  -  {e['name']}")
            it.setData(Qt.UserRole, e["id"])
            self.events.addItem(it)

    def pick_tag(self, it, _prev=None):
        data = it.data(0, Qt.UserRole) if it else None
        self.grid.clear()
        self.items = {}
        self.photo_tags.clear()
        self.cur, self.cur_hash = None, None
        is_event = bool(data) and data[0] == "event"
        self.rename_btn.setVisible(is_event)
        if not data:
            return
        kind, key, name = data
        self.cur = (kind, key, name)
        rows = store.person_photos(self.conn, key) if kind == "person" else store.tag_photos(self.conn, key)
        self.accept_all_btn.setEnabled(kind == "thing")
        for r in rows[:2000]:
            auto = r["source"] == "auto"
            label = os.path.basename(r["path"])
            if auto:
                label += f"\n~ {r['score']:.2f}" if r["score"] is not None else "\n~ auto"
            gi = QListWidgetItem(label)
            if auto:
                f = QFont(gi.font())
                f.setItalic(True)
                gi.setFont(f)
            gi.setData(Qt.UserRole, {"hash": r["hash"], "path": r["path"]})
            gi.setToolTip(r["path"])
            blank = QPixmap(self.grid.iconSize())
            blank.fill(Qt.transparent)
            gi.setIcon(QIcon(blank))
            self.grid.addItem(gi)
            th = str(paths.THUMBS / f"{r['hash']}.jpg")
            self.items[th] = gi
            pm = self.ctx.thumbs.get(th)
            if pm is not None:
                gi.setIcon(QIcon(square_pixmap(pm, self.grid.iconSize().width())))
        self.status.setText(f"{name}: {len(rows):,} photos" + (" (first 2,000 shown)" if len(rows) > 2000 else ""))

    def _grid_sizes(self):
        self.grid.setIconSize(QSize(px(THUMB_PX), px(THUMB_PX)))
        self.grid.setGridSize(QSize(px(THUMB_PX + 20), px(THUMB_PX + 44)))
        self.grid.setSpacing(px(4))

    def on_zoom(self):
        super().on_zoom()
        self._grid_sizes()
        for path in list(self.items):
            self._thumb_ready(path)

    def _thumb_ready(self, path):
        it = self.items.get(path)
        if it is not None:
            pm = self.ctx.thumbs.get(path)
            if pm is not None:
                it.setIcon(QIcon(square_pixmap(pm, self.grid.iconSize().width())))

    def pick_photo(self, it, _prev=None):
        self.photo_tags.clear()
        self.cur_hash = it.data(Qt.UserRole)["hash"] if it else None
        if not self.cur_hash:
            return
        for nm in store.person_tags_for_photo(self.conn, self.cur_hash):
            li = QListWidgetItem(f"person: {nm}")
            li.setFlags(Qt.ItemIsEnabled)
            self.photo_tags.addItem(li)
        for t in store.tags_for_photo(self.conn, self.cur_hash):
            auto = t["source"] == "auto"
            li = QListWidgetItem(f"{t['kind']}: {t['name']}" + (f"   ~ {t['score']:.2f} (auto)" if auto and t["score"] is not None
                                                                else "   (auto)" if auto else ""))
            if auto:
                f = QFont(li.font())
                f.setItalic(True)
                li.setFont(f)
            li.setData(Qt.UserRole, (t["id"], auto))
            self.photo_tags.addItem(li)

    # ----- actions
    def _sel_tag(self):
        it = self.photo_tags.currentItem()
        return it.data(Qt.UserRole) if it and it.data(Qt.UserRole) else None

    def _reselect(self):
        h = self.cur_hash
        self.refresh()
        if h:
            for i in range(self.grid.count()):
                if self.grid.item(i).data(Qt.UserRole)["hash"] == h:
                    self.grid.setCurrentRow(i)
                    break

    def accept_selected(self):
        sel = self._sel_tag()
        if sel and self.cur_hash:
            store.accept_photo_tag(self.conn, self.cur_hash, sel[0])
            self._reselect()

    def remove_selected(self):
        sel = self._sel_tag()
        if sel and self.cur_hash:
            store.reject_photo_tag(self.conn, self.cur_hash, sel[0])
            self._reselect()

    def add_manual(self):
        nm = self.add_edit.text().strip()
        if not nm or not self.cur_hash:
            return
        store.add_photo_tag(self.conn, self.cur_hash, store.get_or_create_tag(self.conn, nm, "thing"), "manual")
        self.add_edit.clear()
        self._reselect()

    def accept_all(self):
        if not self.cur or self.cur[0] != "thing":
            return
        n = store.accept_all_above(self.conn, self.cur[1], self.thr.value())
        self.refresh()
        self.status.setText(f"Accepted {n:,} auto tags for “{self.cur[2] if self.cur else ''}”")

    def rename_event(self):
        if not self.cur or self.cur[0] != "event":
            return
        name, ok = QInputDialog.getText(self, "Rename event", "Event name:", text=self.cur[2])
        if ok and name.strip():
            from ..ai import events
            events.rename_event(self.conn, self.cur[1], name.strip())
            self.refresh()

    def open_event(self, li):
        tid = li.data(Qt.UserRole)
        self.tabs.setCurrentIndex(0)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                d = top.child(j).data(0, Qt.UserRole)
                if d and d[0] == "event" and d[1] == tid:
                    top.setExpanded(True)
                    self.tree.setCurrentItem(top.child(j))
                    return

    def open_in_search(self):
        if self.cur:
            person = self.cur[0] == "person"
            self.ctx.main.open_search_tags([] if person else [self.cur[2]], self.cur[2] if person else "")

    def compute_events(self):
        ai = get_client(self.ctx)
        if not ai.available:
            self.status.setText("AI not set up: click Enable AI features above.")  # inline, not modal (headless-safe)
            return
        self.status.setText("Computing events…")
        self.jobs = [call_async(lambda: ai.request("events"),
                                lambda r: (self.refresh(), self.status.setText(f"{r['events']:,} events computed")),
                                lambda m: self.status.setText("Failed: " + m))]


# =================================================================== AI Index
class AiIndexPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.jobs = []
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("AI Index", "Reads every photo once (originals are never changed) to build search "
                                         "and face data. Safe to stop and continue later."))
        self.info = QLabel("")  # must exist before EnableAiPanel: it calls refresh_stats at once when AI is set up
        self.info.setWordWrap(True)
        lay.addWidget(EnableAiPanel(ctx, self.refresh_stats))
        lay.addWidget(self.info)
        self.start_btn = button("Index new photos", primary=True)
        self.start_btn.clicked.connect(self.start)
        self.test_btn = button("Quick test (200 photos)")
        self.test_btn.clicked.connect(lambda: self.start(limit=200))
        self.stop_btn = button("Stop")
        self.stop_btn.clicked.connect(self.stop)
        lay.addLayout(hrow(self.start_btn, self.test_btn, self.stop_btn, stretch=False))
        self.bar = QProgressBar()
        lay.addWidget(self.bar)
        self.prog = QLabel("")
        lay.addWidget(self.prog)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)

    def on_show(self):
        self.refresh_stats()

    def refresh_stats(self):
        st = store.stats(ai_conn(self.ctx))
        ai = get_client(self.ctx)
        self.info.setText(
            f"<b>{st['embedded']:,}</b> photos searchable · <b>{st['faces']:,}</b> faces · "
            f"<b>{st['faces_named']:,}</b> named · <b>{st['people']}</b> people · {st['errors']} unreadable<br>"
            f"Worker environment: {'ready' if ai.available else 'not installed (use Enable AI features)'} · "
            f"GPU: AMD Radeon 890M via DirectML (faces run on CPU)")

    def start(self, limit=None):
        ai = get_client(self.ctx)
        limit = limit if isinstance(limit, int) else None
        self.prog.setText("Starting worker…")
        self.jobs = [call_async(lambda: ai.request("index", limit=limit), lambda r: None,
                                lambda m: self.prog.setText("Failed: " + m))]

    def stop(self):
        ai = get_client(self.ctx)
        if ai.proc and ai.proc.poll() is None:
            self.jobs = [call_async(lambda: ai.request("cancel"), lambda r: self.prog.setText("Stopping…"))]

    def poll(self):
        ai = getattr(self.ctx, "ai", None)
        if ai is None:
            return
        e = ai.last_progress()
        if not e:
            return
        ev = e.get("event")
        if ev == "listing":
            self.prog.setText("Listing photos…")
        elif ev == "start":
            self.bar.setMaximum(max(1, e["total"]))
            self.prog.setText(f"{e['total']:,} new photos to read (library: {e['library']:,})")
        elif ev == "progress":
            self.bar.setMaximum(e["total"])
            self.bar.setValue(e["done"])
            self.prog.setText(f"{e['done']:,} / {e['total']:,} · {e['per_sec']} photos/s · {e['faces']:,} faces · "
                              f"about {eta_text(e['eta'])} left")
        elif ev == "done":
            self.bar.setValue(self.bar.maximum())
            self.prog.setText(f"Finished: {e['processed']:,} photos in {eta_text(e['seconds'])}")
            self.refresh_stats()
        elif ev == "index_error":
            self.prog.setText("Error: " + e["error"])
