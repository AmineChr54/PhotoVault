"""Shared UI pieces: background task runner, thumbnails, charts, small widgets, theme."""
import collections
import io
import os
import subprocess
import threading
import traceback

import xxhash
from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QHeaderView, QAbstractItemView)

from ..core import config, db
from . import theme
from .icons import qcolor
from .theme import ACCENT, BAD, GOOD, WARN, px  # noqa: F401  (re-exported for the pages)


# ---------------------------------------------------------------- formatting
def human(b) -> str:
    b = b or 0
    for unit, div in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b} B"


def show_in_explorer(path: str) -> None:
    if os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isdir(os.path.dirname(path)):
        os.startfile(os.path.dirname(path))


def open_file(path: str) -> None:
    if os.path.exists(path):
        os.startfile(path)


def confirm(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


def info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


# ---------------------------------------------------------------- background tasks
class _Signals(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)


class _Worker(QRunnable):
    def __init__(self, fn, cancel):
        super().__init__()
        self.fn = fn
        self.cancel = cancel
        self.signals = _Signals()

    def run(self):
        conn = db.connect()
        try:
            res = self.fn(conn, lambda d, t, m: self.signals.progress.emit(int(d), int(t), str(m)), self.cancel)
            self.signals.done.emit(res)
        except InterruptedError:
            self.signals.failed.emit("Cancelled")
        except Exception as e:  # shown to the user, details in console
            traceback.print_exc()
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        finally:
            conn.close()


class Tasks(QObject):
    """Runs one long job at a time in the background, wired to the status bar."""
    busyChanged = Signal(bool)
    finished = Signal(str)  # title of finished task (pages refresh on this)

    def __init__(self, label: QLabel, bar: QProgressBar, cancel_btn: QPushButton):
        super().__init__()
        self.label, self.bar, self.cancel_btn = label, bar, cancel_btn
        self.pool = QThreadPool.globalInstance()
        self.current = None
        self.cancel_event = None
        cancel_btn.clicked.connect(self.cancel)
        self._idle("Ready")

    @property
    def busy(self) -> bool:
        return self.current is not None

    def _idle(self, msg):
        self.label.setText(msg)
        self.bar.hide()
        self.cancel_btn.hide()

    def cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
            self.label.setText("Cancelling...")

    def run(self, title: str, fn, on_done=None, on_fail=None, parent=None) -> bool:
        if self.busy:
            QMessageBox.information(parent, "Busy", "Another task is still running. Please wait or cancel it first.")
            return False
        self.cancel_event = threading.Event()
        w = _Worker(fn, self.cancel_event)
        self.current = w
        self.label.setText(title + "...")
        self.bar.setRange(0, 0)
        self.bar.show()
        self.cancel_btn.show()
        self.busyChanged.emit(True)

        def progress(d, t, m):
            if t > 0:
                self.bar.setRange(0, t)
                self.bar.setValue(d)
            else:
                self.bar.setRange(0, 0)
            self.label.setText(m)

        def done(res):
            self.current = None
            self._idle(f"{title}: done")
            self.busyChanged.emit(False)
            if on_done:
                on_done(res)
            self.finished.emit(title)

        def failed(msg):
            self.current = None
            self._idle(f"{title}: {msg}")
            self.busyChanged.emit(False)
            if on_fail:
                on_fail(msg)
            elif msg != "Cancelled":
                QMessageBox.warning(parent, title, msg)
            self.finished.emit(title)

        w.signals.progress.connect(progress)
        w.signals.done.connect(done)
        w.signals.failed.connect(failed)
        self.pool.start(w)
        return True


# ---------------------------------------------------------------- thumbnails
class _ThumbSignals(QObject):
    ready = Signal(str, QImage)


class _ThumbJob(QRunnable):
    def __init__(self, path, size, signals):
        super().__init__()
        self.path, self.size, self.signals = path, size, signals

    def run(self):
        img = load_thumb_image(self.path, self.size)
        self.signals.ready.emit(self.path, img)


def _video_placeholder(size) -> QImage:
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor("#2b2f36"))
    p = QPainter(img)
    p.setPen(QColor("#d0d4da"))
    f = QFont("Segoe UI", max(8, size // 7))
    p.setFont(f)
    p.drawText(img.rect(), Qt.AlignCenter, "▶\nVIDEO")
    p.end()
    return img


def load_thumb_image(path: str, size: int) -> QImage:
    ext = os.path.splitext(path)[1].lower()
    if config.kind_of(ext) != "image":
        return _video_placeholder(size) if config.kind_of(ext) == "video" else _text_placeholder(ext, size)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _text_placeholder("missing", size)
    key = xxhash.xxh64(f"{path}|{mtime}|{size}".encode("utf-8", "surrogatepass")).hexdigest()
    cache = config.THUMB_DIR / f"{key}.jpg"
    if cache.exists():
        q = QImage(str(cache))
        if not q.isNull():
            return q
    try:
        with Image.open(path) as im:
            im.draft("RGB", (size * 2, size * 2))
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
        data = buf.getvalue()
        config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
        try:
            cache.write_bytes(data)
        except OSError:
            pass
        q = QImage()
        q.loadFromData(data, "JPG")
        return q
    except Exception:
        return _text_placeholder("unreadable", size)


def _text_placeholder(text, size) -> QImage:
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor("#3a3f47"))
    p = QPainter(img)
    p.setPen(QColor("#c0c4ca"))
    p.drawText(img.rect(), Qt.AlignCenter, text)
    p.end()
    return img


class ThumbCache(QObject):
    """Async thumbnail loader with an in-memory LRU cache."""
    ready = Signal(str)

    def __init__(self, size=180, capacity=1500):
        super().__init__()
        self.size = size
        self.capacity = capacity
        self.cache = collections.OrderedDict()
        self.pending = set()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(4)
        self.signals = _ThumbSignals()
        self.signals.ready.connect(self._on_ready)

    def get(self, path: str):
        if path in self.cache:
            self.cache.move_to_end(path)
            return self.cache[path]
        if path not in self.pending:
            self.pending.add(path)
            self.pool.start(_ThumbJob(path, self.size, self.signals))
        return None

    def clear_queue(self):
        self.pool.clear()
        self.pending.clear()

    def _on_ready(self, path, img):
        self.pending.discard(path)
        self.cache[path] = QPixmap.fromImage(img)
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        self.ready.emit(path)


def square_pixmap(pm: QPixmap, size: int) -> QPixmap:
    """Thumbnail centered on a transparent square, so grid tiles are all the same size."""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    scaled = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p = QPainter(out)
    p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return out


# ---------------------------------------------------------------- widgets
class Card(QFrame):
    """Dashboard stat tile."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        self._lay = lay
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        self.value = QLabel("–")
        self.value.setObjectName("cardValue")
        self.sub = QLabel("")
        self.sub.setObjectName("cardSub")
        self.sub.setWordWrap(True)
        for w in (self.title, self.value, self.sub):
            lay.addWidget(w)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rescale()

    def rescale(self):
        self._lay.setContentsMargins(px(16), px(12), px(16), px(12))
        self._lay.setSpacing(px(2))

    def set(self, value: str, sub: str = "", color: str | None = None):
        self.value.setText(value)
        self.sub.setText(sub)
        self.value.setStyleSheet(f"color:{color};" if color else "")


class BarChart(QWidget):
    """Simple bar chart. vertical=True draws columns (e.g. years), else horizontal bars (e.g. folders)."""

    def __init__(self, vertical=False):
        super().__init__()
        self.vertical = vertical
        self.data = []  # (label, value, value_text)
        self.rescale()

    def rescale(self):
        self.setMinimumHeight(px(220) if self.vertical else max(px(120), px(24) * len(self.data) + px(10)))
        self.update()

    def set_data(self, data):
        self.data = list(data)
        self.rescale()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = theme.tokens()
        text = qcolor(t["text"])
        muted = qcolor(t["text2"])
        bar = qcolor(t["accent"])
        if not self.data:
            p.setPen(muted)
            p.drawText(self.rect(), Qt.AlignCenter, "No data yet - scan the library first")
            return
        mx = max(v for _, v, _ in self.data) or 1
        fm = p.fontMetrics()
        if self.vertical:
            n = len(self.data)
            top_pad, bottom_pad = fm.height() + px(6), fm.height() + px(8)
            h = self.height() - top_pad - bottom_pad
            slot = self.width() / n
            bw = max(px(4), min(px(48), slot * 0.7))
            for i, (lab, v, vt) in enumerate(self.data):
                x = i * slot + (slot - bw) / 2
                bh = h * v / mx
                y = top_pad + h - bh
                p.setPen(Qt.NoPen)
                p.setBrush(bar)
                p.drawRoundedRect(QRectF(x, y, bw, max(bh, 1)), px(4), px(4))
                p.setPen(text)
                if slot > fm.horizontalAdvance(vt) * 0.9:
                    p.drawText(int(i * slot), int(y - fm.height() - px(2)), int(slot), fm.height(), Qt.AlignCenter, vt)
                p.setPen(muted)
                lab_s = lab if slot > fm.horizontalAdvance(lab) + 2 else lab[-2:]
                p.drawText(int(i * slot), self.height() - bottom_pad + px(4), int(slot), fm.height(), Qt.AlignCenter, lab_s)
        else:
            row_h = px(24)
            label_w = min(px(260), max(fm.horizontalAdvance(l) for l, _, _ in self.data) + px(12))
            val_w = max(fm.horizontalAdvance(vt) for _, _, vt in self.data) + px(12)
            avail = self.width() - label_w - val_w - px(8)
            for i, (lab, v, vt) in enumerate(self.data):
                y = px(4) + i * row_h
                p.setPen(text)
                p.drawText(0, y, label_w - px(8), row_h - px(4), Qt.AlignRight | Qt.AlignVCenter,
                           fm.elidedText(lab, Qt.ElideMiddle, label_w - px(8)))
                w = max(px(2), avail * v / mx)
                bh = px(12)
                p.setPen(Qt.NoPen)
                p.setBrush(bar)
                p.drawRoundedRect(QRectF(label_w, y + (row_h - px(4) - bh) / 2, w, bh), px(4), px(4))
                p.setPen(muted)
                p.drawText(int(label_w + w + px(8)), y, val_w, row_h - px(4), Qt.AlignLeft | Qt.AlignVCenter, vt)


def make_table(headers, stretch_col=None, selection=QAbstractItemView.ExtendedSelection) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(selection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setSortingEnabled(False)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_col is not None:
        hh.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    return t


class NumItem(QTableWidgetItem):
    """Table item that sorts by a numeric key but shows formatted text."""

    def __init__(self, text, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        if isinstance(other, NumItem):
            return self.key < other.key
        return super().__lt__(other)


def fill_table(t: QTableWidget, rows, data_key=None, limit=20000):
    """rows: list of tuples of cells (str or (text, sortkey)). data_key(row_index) -> stored in col 0 UserRole."""
    t.setSortingEnabled(False)
    t.setRowCount(0)
    rows = rows[:limit]
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, c in enumerate(r):
            item = NumItem(c[0], c[1]) if isinstance(c, tuple) else QTableWidgetItem(str(c))
            if j == 0 and data_key is not None:
                item.setData(Qt.UserRole, data_key(i))
            t.setItem(i, j, item)
    t.setSortingEnabled(True)


FIRST_RUN_TEXT = ("<b>Welcome - first run.</b> Photo Vault has not been set up yet. Open <b>Settings</b> and choose "
                  "your library folders, the organized-photos folder and the quarantine folder, then choose a backup "
                  "disk under <b>Backup</b>. Click <b>Save settings</b> once to dismiss this notice. "
                  "Until then the defaults point at your Pictures folder. "
                  'New here? Read the <a href="help">Help</a> (F1).')


def header(text: str, sub: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, px(8))
    h = QLabel(text)
    h.setObjectName("pageTitle")
    lay.addWidget(h)
    if sub:
        s = QLabel(sub)
        s.setObjectName("pageSub")
        s.setWordWrap(True)
        lay.addWidget(s)
    return w


def hrow(*widgets, stretch=True) -> QHBoxLayout:
    lay = QHBoxLayout()
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch and None not in widgets:
        lay.addStretch(1)
    return lay


def button(text: str, primary=False, danger=False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if danger:
        b.setObjectName("danger")
    b.setCursor(Qt.PointingHandCursor)
    return b


# ---------------------------------------------------------------- theme
def apply_theme(app: QApplication, theme_name: str, zoom: float = 1.0):
    """Palette, font and style sheet for `theme_name` ("dark"/"light") at UI zoom `zoom` (0.7-2.0)."""
    theme.set_state(theme_name, zoom)
    t, z = theme.tokens(), theme.zoom()
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "s1"),
                      (QPalette.AlternateBase, "s2"), (QPalette.Text, "text"), (QPalette.Button, "s2"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "on_accent"), (QPalette.ToolTipBase, "s3"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "text3"),
                      (QPalette.Link, "accent_text")):
        pal.setColor(role, qcolor(t[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, qcolor(t["text3"]))
    app.setPalette(pal)
    font = QFont(theme.FONT_FAMILY)
    font.setFamilies([theme.FONT_FAMILY, *theme.FONT_FALLBACKS])
    font.setPixelSize(theme.scale(theme.BASE_FONT_PX, z))
    app.setFont(font)
    app.setStyleSheet(theme.stylesheet(t, z))


def panel() -> tuple:
    f = QFrame()
    f.setObjectName("panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(px(16), px(14), px(16), px(14))
    return f, lay


def section_title(text) -> QLabel:
    l = QLabel(text)
    l.setObjectName("sectionTitle")
    return l


THUMB_SIZE = QSize(180, 180)
