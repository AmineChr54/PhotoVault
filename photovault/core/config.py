"""Settings, paths and folder rules. Stored as JSON in %LOCALAPPDATA%\\PhotoVault."""
import fnmatch
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("PHOTOVAULT_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PhotoVault")
SETTINGS_FILE = APP_DIR / "settings.json"
DB_FILE = APP_DIR / "library.db"
THUMB_DIR = APP_DIR / "thumbs"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff", ".dng"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".mts", ".ts", ".mxf", ".wmv", ".webm"}
AUDIO_EXT = {".mp3", ".aac", ".m4a", ".wav", ".ogg", ".opus", ".amr", ".rec"}

# Folder / file names that are never treated as part of the library.
IGNORE_NAMES = {"thumbs.db", "desktop.ini"}

# Categories a top-level folder can have:
#   curated          hand-picked event/collection folder, kept intact
#   raw              phone/camera dump, sorted by date (chat media + screenshots auto-detected)
#   chat:<App>       chat app media
#   screenshots      screenshots and screen recordings
#   gaming:<Game>    game screenshots
#   projects         creative work, kept intact
#   documents        documents and scans, kept intact
#   other            anything else, kept intact under Other
#   skip             never moved by Organize
DEFAULT_RULES = [
    ["tsawer lmama w baba", "curated"],
    ["tsawer kdom yesser", "curated"],
    ["sif *", "curated"],
    ["2016-2019 zebda", "curated"],
    ["eid miled baba*", "curated"],
    ["Greatest Creations", "curated"],
    ["mucus (rest in peace)", "curated"],
    ["Redmi Recordings - GOLD", "curated"],
    ["nEW fOLDER", "curated"],
    ["Chr", "curated"],
    ["Redmi Whatsapp", "chat:WhatsApp"],
    ["Redmi Snapchat", "chat:Snapchat"],
    ["Redmi Instagram Pictures", "chat:Instagram"],
    ["Redmi Messenger Pictures", "chat:Messenger"],
    ["Redmi Screenshots", "screenshots"],
    ["Redmi ScreenRecordings", "screenshots"],
    ["Minecraft Screenshots", "gaming:Minecraft"],
    ["ScreenshotsWoW", "gaming:World of Warcraft"],
    ["Screenshots - World of Warcraft*", "gaming:World of Warcraft"],
    ["VIDEOT YOUTUBE HAROU", "projects"],
    ["Redmi Files", "other"],
    ["Documents Drive Kdom", "documents"],
    ["Scans", "documents"],
    ["Redmi 10*", "raw"],
    ["Pixel *", "raw"],
    ["CarteMemoire*", "raw"],
    ["101MSDCF", "raw"],
    ["Camera Roll*", "raw"],
    ["00_Inbox", "raw"],
    ["(root files)", "other"],
]

CATEGORY_HELP = {
    "curated": "Hand-picked folder, kept intact (event under its year, or Collections if it spans years)",
    "raw": "Phone/camera dump, sorted into Life\\YYYY\\YYYY-MM by date",
    "chat:<App>": "Chat media, sorted into Chat Media\\<App>\\YYYY",
    "screenshots": "Screenshots & Recordings\\YYYY",
    "gaming:<Game>": "Gaming\\<Game>, kept intact",
    "projects": "Projects, kept intact",
    "documents": "Documents & Scans, kept intact",
    "other": "Other, kept intact",
    "skip": "Never moved",
}


def _pictures() -> str:
    return str(Path.home() / "Pictures")


def _default_organized() -> str:
    return os.path.join(_pictures(), "Photos")


ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.7, 2.0, 0.1


def clamp_zoom(z) -> float:
    """Valid UI zoom: a float in [0.7, 2.0] rounded to 0.1 (bad values fall back to 1.0)."""
    try:
        z = float(z)
    except (TypeError, ValueError):
        return 1.0
    if z != z:  # NaN
        return 1.0
    return round(min(ZOOM_MAX, max(ZOOM_MIN, z)), 1)


def step_zoom(z, steps: int) -> float:
    """Zoom changed by `steps` x 0.1, clamped."""
    return clamp_zoom(round(clamp_zoom(z) + steps * ZOOM_STEP, 1))


@dataclass
class Settings:
    # Per-user defaults; nothing machine-specific. The backup target is left empty on purpose:
    # the user must choose a disk. `setup_complete` is True for any settings.json written before
    # this field existed (so existing users never see the first-run notice); only a brand-new
    # install creates it as False, until the user saves Settings once.
    library_roots: list = field(default_factory=lambda: [_pictures()])
    organized_root: str = field(default_factory=_default_organized)
    quarantine_dir: str = field(default_factory=lambda: os.path.join(_pictures(), "_Duplicates_To_Review"))
    backup_sources: list = field(default_factory=lambda: [_default_organized()])
    backup_target: str = ""
    setup_complete: bool = True
    similar_threshold: int = 6
    backup_reminder_days: int = 30
    backup_notify: bool = True  # Windows notification at startup when a backup is overdue
    theme: str = "dark"
    ui_zoom: float = 1.0
    folder_rules: list = field(default_factory=lambda: [list(r) for r in DEFAULT_RULES])

    @property
    def inbox(self) -> str:
        return os.path.join(self.organized_root, "00_Inbox")

    def existing_roots(self) -> list:
        return [r for r in self.library_roots if os.path.isdir(r)]

    def category_for(self, top: str) -> str:
        for pattern, cat in self.folder_rules:
            if fnmatch.fnmatch(top.lower(), pattern.lower()):
                return cat
        return "raw"

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)

    @classmethod
    def load(cls) -> "Settings":
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                s = cls(**known)
                s.ui_zoom = clamp_zoom(s.ui_zoom)
                return s
            except (ValueError, TypeError):
                pass
        s = cls(setup_complete=False)
        s.save()
        return s


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "other"
