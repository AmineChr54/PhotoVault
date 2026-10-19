"""File content hashes (xxh3-128), perceptual image hashes (dHash) and a sharpness measure."""
import xxhash
from PIL import Image, ImageFilter, ImageStat

CHUNK = 1 << 20
EDGE = 65536


def full_hash(path: str, cancel=None) -> str:
    h = xxhash.xxh3_128()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def partial_hash(path: str, size: int) -> str:
    """First and last 64 KB - cheap pre-filter before a full hash."""
    h = xxhash.xxh3_128()
    with open(path, "rb") as f:
        h.update(f.read(EDGE))
        if size > 2 * EDGE:
            f.seek(-EDGE, 2)
            h.update(f.read(EDGE))
    return h.hexdigest()


def dhash(path: str) -> str | None:
    """64-bit difference hash as 16 hex chars. None if the image can't be read."""
    try:
        with Image.open(path) as img:
            img.draft("L", (256, 256))
            g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            px = list(g.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


_LAPLACIAN = ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128)
SHARP_EDGE = 512


def sharpness(path: str) -> float | None:
    """Variance of the Laplacian on a copy scaled to fit 512 px, so photos of different sizes are comparable.
    Higher = sharper. No model needed (Pillow only). None if the image can't be read."""
    try:
        with Image.open(path) as img:
            img.draft("L", (SHARP_EDGE * 2, SHARP_EDGE * 2))
            g = img.convert("L")
        g.thumbnail((SHARP_EDGE, SHARP_EDGE), Image.Resampling.BOX)
        return round(ImageStat.Stat(g.filter(_LAPLACIAN)).var[0], 1)
    except Exception:
        return None


def copy_with_hash(src: str, dst: str, cancel=None) -> str:
    """Copy src -> dst while hashing the data; returns the source hash."""
    h = xxhash.xxh3_128()
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            b = fi.read(CHUNK)
            if not b:
                break
            h.update(b)
            fo.write(b)
    return h.hexdigest()
