"""Launch the real window on a sandbox library, drive every page and save screenshots.
Usage: python tests/ui_smoke.py <out_dir>
Optional env: PV_SMOKE_THEME=light, PV_SMOKE_ZOOM=1.5 (screenshots at another theme/zoom)."""
import json
import os
import shutil
import sys
import tempfile

sandbox = tempfile.mkdtemp(prefix="pv_ui_")
os.environ["PHOTOVAULT_HOME"] = os.path.join(sandbox, "home")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

lib = os.path.join(sandbox, "Library")
org = os.path.join(sandbox, "Photos")


def jpg(rel, color, size=(640, 480), date=None):
    p = os.path.join(lib, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    im = Image.new("RGB", size, color)
    for x in range(0, size[0], 9):
        for y in range(size[1]):
            im.putpixel((x, y), ((x + color[0]) % 255, (y + color[1]) % 255, color[2]))
    ex = Image.Exif()
    if date:
        ex.get_ifd(0x8769)[36867] = date
        ex[272] = "Redmi 10" if "Redmi" in rel else "Pixel 9"
    im.save(p, quality=85, exif=ex)
    return p


for i in range(24):
    y = 2016 + i % 9
    jpg(f"Redmi 10 ({i % 4 + 2})\\DCIM\\Camera\\IMG_{y}0{i % 9 + 1}12_1010{i:02d}.jpg", (i * 10, 90, 160),
        date=f"{y}:0{i % 9 + 1}:12 10:10:{i:02d}")
for i in range(6):
    jpg(f"sif 2020\\IMG_{i}.jpg", (200, i * 30, 40), date=f"2020:07:{10 + i} 12:00:00")
src = os.path.join(lib, "sif 2020", "IMG_0.jpg")
os.makedirs(os.path.join(lib, "Redmi 10 (9)"), exist_ok=True)
shutil.copy2(src, os.path.join(lib, "Redmi 10 (9)", "IMG_0.jpg"))
with Image.open(src) as im:
    os.makedirs(os.path.join(lib, "Redmi Whatsapp"), exist_ok=True)
    im.resize((320, 240)).save(os.path.join(lib, "Redmi Whatsapp", "IMG-20200710-WA0001.jpg"), quality=50)
open(os.path.join(lib, "Redmi 10 (2)", "zero.jpg"), "wb").close()

os.makedirs(os.environ["PHOTOVAULT_HOME"], exist_ok=True)
with open(os.path.join(os.environ["PHOTOVAULT_HOME"], "settings.json"), "w") as f:
    json.dump({"theme": os.environ.get("PV_SMOKE_THEME", "dark"), "ui_zoom": float(os.environ.get("PV_SMOKE_ZOOM", "1.0")),
               "library_roots": [lib, org], "organized_root": org, "quarantine_dir": os.path.join(sandbox, "_Q"),
               "backup_sources": [org], "backup_target": os.path.join(sandbox, "Backup")}, f)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from photovault.app import PAGES, MainWindow  # noqa: E402
from photovault.ai.client import AiClient  # noqa: E402
from photovault.core import config  # noqa: E402
from photovault.ui.common import apply_theme  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(sandbox, "shots")
os.makedirs(out, exist_ok=True)
app = QApplication(sys.argv)
s = config.Settings.load()
apply_theme(app, s.theme, s.ui_zoom)
w = MainWindow(s)
w.resize(1400, 900)
w.show()

steps = []


def wait_idle(then):
    def check():
        if w.ctx.tasks.busy:
            QTimer.singleShot(150, check)
        else:
            QTimer.singleShot(400, then)
    check()


def shot(name):
    w.grab().save(os.path.join(out, f"{name}.png"))
    print("saved", name)


def page(i, name, action=None):
    def go():
        w.nav.setCurrentRow(i)
        if action:
            action()
        wait_idle(lambda: (shot(name), nxt()))
    return go


def nxt():
    if steps:
        steps.pop(0)()
    else:
        app.quit()


from PySide6.QtWidgets import QTabWidget  # noqa: E402

from photovault.ui.pages_a import GroupView  # noqa: E402
from photovault.ui.pages_help import AboutDialog  # noqa: E402

p = w.pages
IDX = {cls.__name__: i for i, (_, cls) in enumerate(PAGES)}
exact = next(g for g in p[1].findChildren(GroupView) if not g.similar)
similar = next(g for g in p[1].findChildren(GroupView) if g.similar)
dup_tabs = p[1].findChild(QTabWidget)


def thumbs_then(name, delay=1500):
    """Give async thumbnails a moment before the screenshot."""
    def go():
        QTimer.singleShot(delay, lambda: (shot(name), nxt()))
    return go


def about_shot():
    def snap():
        dlg = w.findChild(AboutDialog)
        dlg.grab().save(os.path.join(out, "13e_about.png"))
        print("saved 13e_about")
        dlg.accept()
    QTimer.singleShot(400, snap)
    w.show_about()  # modal: returns once snap() closes it
    nxt()


steps += [
    page(0, "01_dashboard"),
    page(1, "02_duplicates_exact", lambda: (dup_tabs.setCurrentIndex(0), exact.find())),
    thumbs_then("02b_duplicates_exact_thumbs"),
    page(1, "03_duplicates_similar", lambda: (dup_tabs.setCurrentIndex(1), similar.find())),
    page(2, "04_organize_preview", lambda: p[2].build()),
    page(5, "05_health", lambda: p[5].check()),
    page(6, "06_browse"),
    thumbs_then("06b_browse_thumbs"),
    page(4, "07_backup"),
    page(IDX["QuarantinePage"], "08_quarantine"),
    page(IDX["LogPage"], "09_log"),
    page(IDX["SettingsPage"], "10_settings"),
    page(IDX["AiIndexPage"], "11_ai_index"),
    page(IDX["PeoplePage"], "12_people"),
    page(IDX["TagsPage"], "12b_tags"),
    page(IDX["ExplorePage"], "12c_timeline_places"),
    page(IDX["SearchPage"], "13_search", lambda: p[IDX["SearchPage"]].set_query("beach")),
    thumbs_then("13b_search_results", 25000 if AiClient().available else 1000),
    page(IDX["HelpPage"], "13c_help"),
    lambda: (p[IDX["HelpPage"]].index.setCurrentRow(5), wait_idle(lambda: (shot("13d_help_section"), nxt()))),
    about_shot,
    # zoom in/out via the same path as Ctrl+= / Ctrl+- / Ctrl+0
    lambda: (w.set_zoom(1.5), w.nav.setCurrentRow(0), wait_idle(lambda: (shot("14_zoom_150_dashboard"), nxt()))),
    lambda: (w.nav.setCurrentRow(IDX["BrowsePage"]), wait_idle(lambda: (shot("15_zoom_150_browse"), nxt()))),
    lambda: (w.set_zoom(0.7), wait_idle(lambda: (shot("16_zoom_070_browse"), nxt()))),
    lambda: (w.set_zoom(1.0), nxt()),
]
wait_idle(nxt)
app.exec()
print("sandbox:", sandbox)
