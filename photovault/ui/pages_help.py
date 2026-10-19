"""Help page (user guide + what's new) and the About dialog."""
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPalette, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTextBrowser, QVBoxLayout

from .. import __version__
from . import theme
from ..core import updates
from .common import button, header, open_url
from .pages_a import Page

GITHUB_URL = "https://github.com/AmineChr54/PhotoVault"
TAGLINE = "De-duplicate, organize, health-check and back up your personal photo library."
PRIVACY = "Your photos never leave your machine."
CREDITS = [
    ("SigLIP2", "image/text embeddings for search and auto tags (Google, Apache-2.0 licence)."),
    ("InsightFace buffalo_l", "face detection and recognition for People. These models are for "
                              "non-commercial research use only."),
]


def resource(name):
    from ..app import resource as _resource  # lazy: app imports this module
    return _resource(name)


def guide_path() -> str:
    return resource(os.path.join("docs", "USER_GUIDE.md"))


def changelog_path() -> str:
    return resource("CHANGELOG.md")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def whats_new(changelog: str, versions: int = 2) -> str:
    """The Unreleased section plus the newest released section(s) of CHANGELOG.md, headings demoted one level."""
    parts, cur = [], None
    for line in changelog.splitlines():
        if line.startswith("## "):
            cur = []
            parts.append(cur)
        if cur is not None:
            cur.append(line.replace("## ", "### ", 1) if line.startswith("## ") else line)
    return "\n".join("\n".join(p) for p in parts[:versions + 1])


def help_markdown() -> str:
    guide = _read(guide_path())
    if not guide:
        guide = "# Photo Vault user guide\n\nThe user guide file was not found in this installation.\n"
    news = whats_new(_read(changelog_path()))
    return guide.rstrip() + "\n\n## What's new\n\n" + (news or "No changelog available.") + "\n"


class HelpPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        self.set_margins(lay)
        top = QHBoxLayout()
        top.addWidget(header("Help", "User guide and what's new. Press F1 anywhere to come back here."), 1)
        self.about_btn = button("About Photo Vault")
        self.about_btn.clicked.connect(lambda: AboutDialog(self, self.ctx.settings).exec())
        top.addWidget(self.about_btn, 0, Qt.AlignTop)
        lay.addLayout(top)
        body = QHBoxLayout()
        self.index = QListWidget()
        self.index.setObjectName("helpIndex")
        self.index.currentRowChanged.connect(self._jump)
        body.addWidget(self.index)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        body.addWidget(self.view, 1)
        lay.addLayout(body, 1)
        self._md = help_markdown()
        self._anchors = []
        self._render()

    def _render(self):
        t = theme.tokens()
        pal = self.view.palette()
        pal.setColor(QPalette.Link, QColor(t["accent_text"]))
        pal.setColor(QPalette.Text, QColor(t["text"]))
        self.view.setPalette(pal)
        self.view.setMarkdown(self._md)
        self._style_headings(t)
        self._anchors = []
        self.index.blockSignals(True)
        self.index.clear()
        blk = self.view.document().begin()
        while blk.isValid():
            if blk.blockFormat().headingLevel() == 2:
                self.index.addItem(QListWidgetItem(blk.text()))
                self._anchors.append(blk.position())
            blk = blk.next()
        self.index.blockSignals(False)
        self.index.setFixedWidth(theme.px(210))

    def _style_headings(self, t):
        """Colour headings and give them breathing room (setMarkdown ignores the default style sheet)."""
        cur = QTextCursor(self.view.document())
        blk = self.view.document().begin()
        while blk.isValid():
            lvl = blk.blockFormat().headingLevel()
            if lvl:
                cur.setPosition(blk.position())
                cur.setPosition(blk.position() + max(blk.length() - 1, 0), QTextCursor.KeepAnchor)
                fmt = cur.charFormat()
                fmt.setForeground(QColor(t["accent_text"] if lvl <= 2 else t["text"]))
                cur.mergeCharFormat(fmt)
                bf = QTextBlockFormat(blk.blockFormat())
                bf.setTopMargin(theme.px(18 if lvl <= 2 else 10))
                bf.setBottomMargin(theme.px(4))
                cur.setBlockFormat(bf)
            blk = blk.next()

    def _jump(self, row):
        if 0 <= row < len(self._anchors):
            cur = QTextCursor(self.view.document())
            cur.setPosition(self._anchors[row])
            lay = self.view.document().documentLayout()
            self.view.verticalScrollBar().setValue(int(lay.blockBoundingRect(cur.block()).top()))

    def on_zoom(self):
        super().on_zoom()
        self._render()  # theme or zoom changed: re-colour and re-measure


class AboutDialog(QDialog):
    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("About Photo Vault")
        t = theme.tokens()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.px(28), theme.px(24), theme.px(28), theme.px(22))
        lay.setSpacing(theme.px(10))
        ico = QLabel()
        path = resource("icon-256.png")
        path = path if os.path.exists(path) else resource("icon.ico")
        size = theme.px(88)
        ico.setPixmap(QIcon(path).pixmap(QSize(size, size)))
        ico.setAlignment(Qt.AlignCenter)
        lay.addWidget(ico)
        name = QLabel("Photo Vault")
        name.setObjectName("pageTitle")
        name.setAlignment(Qt.AlignCenter)
        lay.addWidget(name)
        ver = QLabel(f"Version {__version__}")
        ver.setObjectName("pageSub")
        ver.setAlignment(Qt.AlignCenter)
        lay.addWidget(ver)
        newer = updates.available(settings) if settings is not None else ""
        if newer:
            upd = button(f"Version {newer.lstrip('v')} is available - view release")
            upd.clicked.connect(lambda: open_url(updates.release_url(newer)))
            lay.addWidget(upd)
        tag = QLabel(TAGLINE)
        tag.setWordWrap(True)
        tag.setAlignment(Qt.AlignCenter)
        lay.addWidget(tag)
        link = QLabel(f'<a href="{GITHUB_URL}" style="color:{t["accent_text"]}">{GITHUB_URL}</a>')
        link.setOpenExternalLinks(True)
        link.setAlignment(Qt.AlignCenter)
        lay.addWidget(link)
        priv = QLabel(f"<b>{PRIVACY}</b> Everything runs locally; nothing is uploaded. "
                     "The only network request is the optional daily update check (Settings), which just asks "
                     "GitHub for the latest version number.")
        priv.setWordWrap(True)
        priv.setAlignment(Qt.AlignCenter)
        lay.addWidget(priv)
        cr = "".join(f"<li><b>{n}</b>: {d}</li>" for n, d in CREDITS)
        credits = QLabel(f"<p><b>Optional AI models</b> (downloaded on request):</p><ul>{cr}</ul>")
        credits.setWordWrap(True)
        credits.setTextFormat(Qt.RichText)
        lay.addWidget(credits)
        ok = button("Close", primary=True)
        ok.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        lay.addLayout(row)
        self.setMinimumWidth(theme.px(480))
