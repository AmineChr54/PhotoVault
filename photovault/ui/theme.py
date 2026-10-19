"""Design tokens, zoom scaling and the Qt style sheet. Pure Python: no Qt imports, so it is unit-testable.

The look follows the Umbra app: true-black surfaces, hairline borders instead of shadows, one indigo accent.
All sizes in the style sheet go through px() so Ctrl+/Ctrl- zoom rescales the whole UI.
"""
from ..core import config

DARK = {
    "bg": "#000000", "s1": "#0a0a0c", "s2": "#111114", "s3": "#17171c",
    "line": "rgba(255,255,255,18)", "line2": "rgba(255,255,255,31)",
    "text": "#f4f5f7", "text2": "#a3a7b1", "text3": "#6b707b",
    "accent": "#8b93ff", "accent_strong": "#a9afff", "accent_text": "#a9afff",
    "accent_soft": "rgba(139,147,255,36)", "on_accent": "#0a0b1a",
    "good": "#2fd58b", "warn": "#e8b04b", "bad": "#ff5a5f",
}
LIGHT = {
    "bg": "#eef0f5", "s1": "#ffffff", "s2": "#f6f7fa", "s3": "#e8ebf2",
    "line": "rgba(10,12,30,24)", "line2": "rgba(10,12,30,46)",
    "text": "#14151c", "text2": "#4a4f5e", "text3": "#6b7080",
    "accent": "#8b93ff", "accent_strong": "#7a83f5", "accent_text": "#3f48c8",
    "accent_soft": "rgba(139,147,255,46)", "on_accent": "#0a0b1a",
    "good": "#13824f", "warn": "#9a6700", "bad": "#cf222e",
}
THEMES = {"dark": DARK, "light": LIGHT}

# Status colours that read on BOTH themes (>= 3:1 on black and on white) - used for big text, dots, badges.
ACCENT = "#8b93ff"
GOOD = "#1fa56a"
WARN = "#c98a0b"
BAD = "#e5484d"

FONT_FAMILY = "Segoe UI Variable Text"
FONT_FALLBACKS = ["Segoe UI Variable", "Segoe UI"]
BASE_FONT_PX = 14

_state = {"theme": "dark", "zoom": 1.0}


def set_state(theme: str, zoom: float) -> None:
    _state["theme"] = theme if theme in THEMES else "dark"
    _state["zoom"] = config.clamp_zoom(zoom)


def zoom() -> float:
    return _state["zoom"]


def tokens(theme: str | None = None) -> dict:
    return THEMES.get(theme or _state["theme"], DARK)


def scale(n, z: float) -> int:
    """n pixels at zoom z (never collapses a non-zero size to 0)."""
    if not n:
        return 0
    v = int(round(n * z))
    return v if v else (1 if n > 0 else -1)


def px(n) -> int:
    return scale(n, _state["zoom"])


def pt(n: float) -> float:
    return round(n * _state["zoom"], 1)


# ------------------------------------------------------------------ contrast (WCAG 2.x)
def _rgb(c: str) -> tuple:
    if c.startswith("rgba"):
        r, g, b, a = [float(x) for x in c[c.index("(") + 1:c.index(")")].split(",")]
        return (r, g, b, a / 255)
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 1.0)


def blend(fg: str, bg: str) -> tuple:
    r, g, b, a = _rgb(fg)
    br, bgc, bb, _ = _rgb(bg)
    return (r * a + br * (1 - a), g * a + bgc * (1 - a), b * a + bb * (1 - a))


def _lum(rgb) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of fg drawn over (opaque) bg."""
    a, b = _lum(blend(fg, bg)), _lum(_rgb(bg)[:3])
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------ style sheet
def stylesheet(t: dict, z: float) -> str:
    def p(n):
        return scale(n, z)
    r_s, r_m = p(8), p(12)
    return f"""
    /* Windows gives these classes their own (smaller) system font; make them follow the zoomed base size */
    QAbstractItemView, QHeaderView, QMenu, QTabBar, QToolTip, QStatusBar, QGroupBox {{ font-size: {p(BASE_FONT_PX)}px; }}
    QToolTip {{ background: {t['s3']}; color: {t['text']}; border: 1px solid {t['line2']};
                padding: {p(5)}px {p(8)}px; border-radius: {r_s}px; }}
    QMainWindow, QDialog, QMessageBox {{ background: {t['bg']}; }}
    QStatusBar {{ background: {t['s1']}; border-top: 1px solid {t['line']}; color: {t['text2']}; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ padding-left: {p(12)}px; }}

    #sidebarBox {{ background: {t['s1']}; border-right: 1px solid {t['line']}; }}
    #sidebar {{ background: transparent; border: none; outline: 0; }}
    #brandName {{ font-size: {p(16)}px; font-weight: 600; color: {t['text']}; }}
    #brandVer {{ font-size: {p(12)}px; color: {t['text3']}; }}
    #pageTitle {{ font-size: {p(24)}px; font-weight: 600; color: {t['text']}; }}
    #pageSub {{ color: {t['text2']}; }}
    #sectionTitle {{ font-weight: 600; font-size: {p(15)}px; color: {t['text']}; }}
    #card, #panel {{ background: {t['s1']}; border: 1px solid {t['line']}; border-radius: {r_m}px; }}
    #cardTitle {{ color: {t['text2']}; font-size: {p(13)}px; font-weight: 500; }}
    #cardValue {{ font-size: {p(26)}px; font-weight: 600; }}
    #cardSub {{ color: {t['text2']}; font-size: {p(13)}px; }}
    #bigResult {{ font-size: {p(16)}px; font-weight: 600; padding: {p(12)}px; border-radius: {r_m}px; }}

    QPushButton {{ min-height: {p(34)}px; padding: 0 {p(16)}px; border-radius: {p(10)}px;
                   border: 1px solid {t['line2']}; background: {t['s2']}; color: {t['text']}; }}
    QPushButton:hover {{ background: {t['s3']}; border-color: {t['accent']}; }}
    QPushButton:pressed {{ background: {t['accent_soft']}; }}
    QPushButton:disabled {{ color: {t['text3']}; background: {t['s1']}; border-color: {t['line']}; }}
    QPushButton:focus {{ border-color: {t['accent']}; }}
    QPushButton#flat {{ background: transparent; border: none; color: {t['text3']}; text-align: left;
                        padding: {p(6)}px {p(20)}px; }}
    QPushButton#flat:hover {{ color: {t['accent_text']}; background: transparent; }}
    QTextBrowser {{ background: {t['s1']}; border: 1px solid {t['line']}; border-radius: {r_m}px; padding: {p(10)}px; }}
    QPushButton#primary {{ background: {t['accent']}; color: {t['on_accent']}; border: 1px solid {t['accent']};
                           font-weight: 600; }}
    QPushButton#primary:hover {{ background: {t['accent_strong']}; border-color: {t['accent_strong']}; }}
    QPushButton#danger {{ background: {t['bad']}; color: {t['on_accent']}; border: 1px solid {t['bad']};
                          font-weight: 600; }}
    QPushButton#danger:hover {{ border-color: {t['text']}; }}
    QPushButton#primary:disabled, QPushButton#danger:disabled {{ background: {t['s3']}; color: {t['text3']};
                                                                 border-color: {t['line']}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        min-height: {p(22)}px; padding: {p(5)}px {p(10)}px; border: 1px solid {t['line2']};
        border-radius: {p(10)}px; background: {t['s2']}; color: {t['text']};
        selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {t['text3']}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
        border: 1px solid {t['accent']}; background: {t['s1']}; }}
    QComboBox QAbstractItemView {{ background: {t['s2']}; border: 1px solid {t['line2']}; outline: 0;
        selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
    QCheckBox, QRadioButton {{ spacing: {p(8)}px; color: {t['text']}; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {t['text3']}; }}

    QTableWidget, QTableView, QListWidget, QListView, QTreeWidget, QTreeView {{
        border: 1px solid {t['line']}; border-radius: {r_s}px; background: {t['s1']};
        alternate-background-color: {t['s2']}; gridline-color: {t['line']}; outline: 0; color: {t['text']};
        selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
    QTableWidget::item, QTreeWidget::item {{ padding: {p(3)}px {p(6)}px; }}
    QListWidget::item:hover, QTableWidget::item:hover {{ background: {t['s3']}; }}
    QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
        background: {t['accent_soft']}; color: {t['text']}; }}
    QHeaderView::section {{ background: {t['s2']}; color: {t['text2']}; font-weight: 600;
        padding: {p(7)}px {p(10)}px; border: none; border-bottom: 1px solid {t['line2']}; }}
    QTableCornerButton::section {{ background: {t['s2']}; border: none; }}

    QTabWidget::pane {{ border: none; border-top: 1px solid {t['line']}; }}
    QTabBar::tab {{ padding: {p(9)}px {p(18)}px; border: none; color: {t['text2']};
                    border-bottom: 2px solid transparent; }}
    QTabBar::tab:hover {{ color: {t['text']}; }}
    QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}

    QProgressBar {{ border: none; background: {t['s3']}; border-radius: {p(4)}px; max-height: {p(8)}px;
                    min-height: {p(8)}px; color: transparent; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: {p(4)}px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: {p(10)}px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: {p(10)}px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {t['line2']}; border-radius: {p(3) + 2}px; min-height: {p(32)}px;
                                   margin: 0 {p(2)}px; }}
    QScrollBar::handle:horizontal {{ background: {t['line2']}; border-radius: {p(3) + 2}px; min-width: {p(32)}px;
                                     margin: {p(2)}px 0; }}
    QScrollBar::handle:hover {{ background: {t['text3']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; background: none; border: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    QMenu {{ background: {t['s2']}; border: 1px solid {t['line2']}; border-radius: {r_s}px; padding: {p(4)}px; }}
    QMenu::item {{ padding: {p(7)}px {p(22)}px; border-radius: {p(6)}px; color: {t['text']}; }}
    QMenu::item:selected {{ background: {t['accent_soft']}; }}
    QMenu::item:disabled {{ color: {t['text3']}; }}
    QMenu::separator {{ height: 1px; background: {t['line']}; margin: {p(4)}px {p(6)}px; }}
    QSplitter::handle {{ background: {t['line']}; }}
    QGroupBox {{ border: 1px solid {t['line']}; border-radius: {r_m}px; margin-top: {p(10)}px; padding-top: {p(10)}px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: {p(12)}px; padding: 0 {p(4)}px; color: {t['text2']}; }}
    """
