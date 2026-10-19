"""Find when a photo/video was taken.

Order of trust: EXIF (photos) -> MP4/MOV header (videos) -> date in the filename -> file modified time.
"""
import datetime as dt
import os
import re
import struct

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

_FULL = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_ T.]?(\d{2})[-_.:h]?(\d{2})[-_.:m]?(\d{2})(?:\d{3})?(?!\d)"
)
_DAY = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
_EPOCH_MS = re.compile(r"(?<!\d)(1[2-9]\d{11})(?!\d)")  # 13-digit unix ms, 2008..2033
_EPOCH_S = re.compile(r"(?<!\d)(1[2-9]\d{8})(?!\d)")  # 10-digit unix seconds

_MP4_EPOCH = dt.datetime(1904, 1, 1)
_MIN_YEAR = 1990


def _valid(d: dt.datetime) -> bool:
    """Reject impossible dates (wrong camera clocks): before 1990 or in the future."""
    return d.year >= _MIN_YEAR and d <= dt.datetime.now() + dt.timedelta(days=2)


def _mk(y, mo, d, h=0, mi=0, s=0):
    try:
        v = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None
    return v if _valid(v) else None


def parse_exif_date(value) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    m = re.match(r"\s*(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", value)
    if not m:
        return None
    return _mk(*m.groups())


def from_filename(name: str) -> dt.datetime | None:
    stem = os.path.splitext(name)[0]
    m = _FULL.search(stem)
    if m:
        v = _mk(*m.groups())
        if v:
            return v
    m = _DAY.search(stem)
    if m:
        v = _mk(*m.groups())
        if v:
            return v
    for rx, div in ((_EPOCH_MS, 1000), (_EPOCH_S, 1)):
        m = rx.search(stem)
        if m:
            try:
                v = dt.datetime.fromtimestamp(int(m.group(1)) / div)
            except (OverflowError, OSError, ValueError):
                continue
            if _valid(v):
                return v
    return None


def mp4_created(path: str) -> dt.datetime | None:
    """Read creation time from the 'mvhd' atom of MP4/MOV/3GP files."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            return _find_mvhd(f, 0, end, depth=0)
    except OSError:
        return None


def _find_mvhd(f, start, end, depth):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        size, typ = struct.unpack(">I4s", hdr)
        hlen = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return None
            size = struct.unpack(">Q", ext)[0]
            hlen = 16
        elif size == 0:
            size = end - pos
        if size < hlen:
            return None
        if typ == b"moov" and depth == 0:
            return _find_mvhd(f, pos + hlen, pos + size, depth + 1)
        if typ == b"mvhd" and depth == 1:
            body = f.read(12)
            if len(body) < 8:
                return None
            version = body[0]
            if version == 1:
                body += f.read(4)
                secs = struct.unpack(">Q", body[4:12])[0]
            else:
                secs = struct.unpack(">I", body[4:8])[0]
            if secs == 0:
                return None
            v = _MP4_EPOCH + dt.timedelta(seconds=secs)
            return v if _valid(v) else None
        pos += size
    return None


def image_info(path: str):
    """Return (exif_datetime, camera, width, height) for an image; Nones if unreadable."""
    try:
        with Image.open(path) as img:
            w, h = img.size
            exif = img.getexif()
            when = None
            camera = None
            if exif:
                sub = exif.get_ifd(0x8769)
                when = parse_exif_date(sub.get(36867)) or parse_exif_date(sub.get(36868)) or parse_exif_date(exif.get(306))
                make = str(exif.get(271) or "").strip(" \x00")
                model = str(exif.get(272) or "").strip(" \x00")
                if model:
                    camera = model if make and make.split()[0].lower() in model.lower() else f"{make} {model}".strip()
                    if len(camera) > 60:
                        camera = camera[:60]
            return when, camera, w, h
    except Exception:
        return None, None, None, None


def best_date(path: str, kind: str, mtime: float, exif_when=None):
    """Return (datetime, source) where source is exif/video/filename/mtime."""
    if exif_when:
        return exif_when, "exif"
    if kind == "video" and path.lower().endswith((".mp4", ".mov", ".m4v", ".3gp")):
        v = mp4_created(path)
        if v:
            return v, "video"
    v = from_filename(os.path.basename(path))
    if v:
        return v, "filename"
    return from_mtime(mtime), "mtime"


def from_mtime(mtime: float) -> dt.datetime:
    """File modified time as a datetime. Works for broken values (pre-1970, far future) that
    datetime.fromtimestamp rejects on Windows."""
    try:
        return dt.datetime.fromtimestamp(mtime)
    except (OSError, OverflowError, ValueError):
        try:
            return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=mtime)
        except OverflowError:
            return dt.datetime(1970, 1, 1)
