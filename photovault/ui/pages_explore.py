"""Timeline and Places: browse the AI index by date (year > month > event) and by GPS cell.
Read-only; the photos of a selection open in Browse."""
import calendar

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QComboBox, QLabel, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..ai import paths, views
from .common import button, header, hrow, px, square_pixmap
from .pages_a import Page
from .pages_ai import ai_conn, event_range

THUMB_PX = 56
HINT_NO_DATA = ("Nothing to show yet. Timeline and Places read the AI index: enable the AI features and build the "
                "index (AI Index page), then compute events on the Tags page.")


class ExplorePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.waiting = {"timeline": {}, "places": {}}   # tree -> {thumbnail path: [items awaiting it]}
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        lay.addWidget(header("Timeline & Places", "Browse your photos by when and where they were taken. Select a "
                                                  "year, month, event or place and open its photos in Browse. "
                                                  "Uses the AI index; nothing is changed."))
        self.status = QLabel("")
        self.status.setObjectName("pageSub")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # --- timeline
        tl = QWidget()
        tll = QVBoxLayout(tl)
        self.t_open = button("Show in Browse", primary=True)
        self.t_open.clicked.connect(lambda: self.open_selected(self.timeline_tree))
        self.t_open.setEnabled(False)
        tll.addLayout(hrow(self.t_open))
        self.timeline_tree = self._make_tree(["When", "Photos"])
        self.timeline_tree.itemDoubleClicked.connect(lambda *_: self.open_selected(self.timeline_tree))
        tll.addWidget(self.timeline_tree, 1)
        self.tabs.addTab(tl, "Timeline")

        # --- places
        pl = QWidget()
        pll = QVBoxLayout(pl)
        self.cell = QComboBox()
        for label, deg in views.CELL_SIZES:
            self.cell.addItem(label, deg)
        self.cell.setCurrentIndex(self.cell.findData(views.DEFAULT_CELL))
        self.cell.currentIndexChanged.connect(lambda _: self.refresh_places())
        self.p_open = button("Show in Browse", primary=True)
        self.p_open.clicked.connect(lambda: self.open_selected(self.places_tree))
        self.p_open.setEnabled(False)
        pll.addLayout(hrow(QLabel("Group by:"), self.cell, self.p_open))
        self.places_tree = self._make_tree(["Place (centre of the area)", "Photos", "When"])
        self.places_tree.itemDoubleClicked.connect(lambda *_: self.open_selected(self.places_tree))
        pll.addWidget(self.places_tree, 1)
        self.tabs.addTab(pl, "Places")
        ctx.thumbs.ready.connect(self._thumb_ready)

    def _make_tree(self, headers):
        t = QTreeWidget()
        t.setHeaderLabels(headers)
        t.setIconSize(QSize(px(THUMB_PX), px(THUMB_PX)))
        t.setColumnWidth(0, px(380))
        t.currentItemChanged.connect(lambda *_: self._sel_changed())
        return t

    def _sel_changed(self):
        self.t_open.setEnabled(self._payload(self.timeline_tree) is not None)
        self.p_open.setEnabled(self._payload(self.places_tree) is not None)

    @staticmethod
    def _payload(tree):
        it = tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def on_show(self):
        self.refresh()

    def on_zoom(self):
        super().on_zoom()
        for t in (self.timeline_tree, self.places_tree):
            t.setIconSize(QSize(px(THUMB_PX), px(THUMB_PX)))

    # ----- data
    def refresh(self):
        conn = ai_conn(self.ctx)
        self.status.setText("")
        if not views.has_data(conn):
            self.timeline_tree.clear()
            self.places_tree.clear()
            self.waiting = {"timeline": {}, "places": {}}
            self.status.setText(HINT_NO_DATA)
            return
        self.refresh_timeline(conn)
        self.refresh_places(conn)

    def _thumb_item(self, which, item, hash_):
        if hash_:
            th = str(paths.THUMBS / f"{hash_}.jpg")
            self.waiting[which].setdefault(th, []).append(item)
            self._apply_thumb(which, th)

    def _apply_thumb(self, which, th):
        pm = self.ctx.thumbs.get(th)
        if pm is not None:
            for it in self.waiting[which].get(th, []):
                it.setIcon(0, QIcon(square_pixmap(pm, px(THUMB_PX))))

    def _thumb_ready(self, path):
        for which in self.waiting:
            if path in self.waiting[which]:
                self._apply_thumb(which, path)

    def refresh_timeline(self, conn=None):
        conn = conn or ai_conn(self.ctx)
        tree = self.timeline_tree
        tree.clear()
        self.waiting["timeline"] = {}
        data = views.timeline(conn)
        n_events = 0
        for k, y in enumerate(data["years"]):
            yi = QTreeWidgetItem([str(y["year"]), f"{y['n']:,}"])
            yi.setData(0, Qt.UserRole, ("year", y["year"]))
            tree.addTopLevelItem(yi)
            for m in y["months"]:
                mi = QTreeWidgetItem([f"{calendar.month_name[m['month']]} {y['year']}", f"{m['n']:,}"])
                mi.setData(0, Qt.UserRole, ("month", y["year"], m["month"]))
                yi.addChild(mi)
                for e in m["events"]:
                    n_events += 1
                    label = f"{event_range(e['first_ts'], e['last_ts'])}  -  {e['name']}"
                    ei = QTreeWidgetItem([label, f"{e['n']:,}"])
                    ei.setData(0, Qt.UserRole, ("event", e["id"]))
                    ei.setToolTip(0, e["name"])
                    mi.addChild(ei)
                    self._thumb_item("timeline", ei, e["cover"])
            yi.setExpanded(k == 0)
        if not data["years"]:
            self.status.setText("No photo in the index has a date yet.")
        elif not n_events:
            self.status.setText("No events yet: open the Tags page and click Compute events to group photos by trip or day.")
        tree.setToolTip(f"{data['undated']:,} indexed photos have no date and are not listed." if data["undated"] else "")

    def refresh_places(self, conn=None):
        conn = conn or ai_conn(self.ctx)
        tree = self.places_tree
        tree.clear()
        self.waiting["places"] = {}
        cell = self.cell.currentData() or views.DEFAULT_CELL
        data = views.places(conn, cell)
        for c in data["cells"]:
            when = "" if c["first_ts"] is None else event_range(c["first_ts"], c["last_ts"])
            it = QTreeWidgetItem([c["label"], f"{c['n']:,}", when])
            it.setData(0, Qt.UserRole, ("cell", c["i"], c["j"], cell))
            tree.addTopLevelItem(it)
            self._thumb_item("places", it, c["cover"])
        tree.setToolTip(f"{data['no_gps']:,} indexed photos have no GPS position and are not listed."
                        if data["no_gps"] else "")
        if not data["cells"] and not self.status.text():
            self.status.setText("No photo in the index has a GPS position yet.")

    # ----- open in Browse
    def selection_photos(self, payload) -> tuple:
        """(title, [(hash, path)]) for a tree payload."""
        conn = ai_conn(self.ctx)
        kind = payload[0]
        if kind == "year":
            return str(payload[1]), views.year_photos(conn, payload[1])
        if kind == "month":
            return f"{calendar.month_name[payload[2]]} {payload[1]}", views.month_photos(conn, payload[1], payload[2])
        if kind == "event":
            return "Event", views.event_photos(conn, payload[1])
        return "Place", views.cell_photos(conn, payload[1], payload[2], payload[3])

    def open_selected(self, tree):
        payload = self._payload(tree)
        if payload is None:
            return
        title, photos = self.selection_photos(payload)
        self.ctx.main.open_browse_paths([p for _, p in photos], title)
