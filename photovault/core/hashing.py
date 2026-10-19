"""File content hashes (xxh3-128) and perceptual image hashes (dHash)."""
import xxhash
from PIL import Image

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
