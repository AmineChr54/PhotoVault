"""Image -> SigLIP input tensor (numpy + Pillow only, so it is testable without onnxruntime)."""
import numpy as np
from PIL import Image


def prep_image(img: Image.Image, size: int, fit: str = "squash") -> np.ndarray:
    """float32 CHW in [-1, 1]. ``squash`` stretches to size x size (the model's training setup); ``pad`` keeps the
    aspect ratio and centres the photo on a mid-grey square (no distortion, less pixel detail on the long side)."""
    im = img.convert("RGB")
    if fit == "pad":
        w, h = im.size
        s = size / max(w, h)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        small = im.resize((nw, nh), Image.Resampling.BILINEAR)
        im = Image.new("RGB", (size, size), (128, 128, 128))
        im.paste(small, ((size - nw) // 2, (size - nh) // 2))
    else:
        im = im.resize((size, size), Image.Resampling.BILINEAR)
    a = np.asarray(im, dtype=np.float32) / 255.0
    return ((a - 0.5) / 0.5).transpose(2, 0, 1)
