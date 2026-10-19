"""SigLIP 2 model variants the AI index can use. Stdlib only (also imported by the GUI process).

Embeddings from different variants (or different input fitting) are NOT comparable, so every index records the
identity of the model that produced it (see ``store.ensure_index_model``). The default is the model the app
shipped with; the larger ones are opt-in via the ``ai_model`` setting and need their own download.
"""
from dataclasses import dataclass

HF = "https://huggingface.co"
FITS = ("squash", "pad")    # squash: stretch to a square (what the model was trained with); pad: keep aspect ratio


@dataclass(frozen=True)
class Variant:
    id: str
    repo: str       # onnx-community repository on Hugging Face
    dirname: str    # folder under paths.MODELS
    size: int       # input resolution (square)
    dim: int        # embedding dimension
    label: str


VARIANTS = {v.id: v for v in (
    Variant("base-256", "siglip2-base-patch16-256-ONNX", "siglip2", 256, 768, "SigLIP 2 base 256 (default)"),
    Variant("base-384", "siglip2-base-patch16-384-ONNX", "siglip2-base-384", 384, 768, "SigLIP 2 base 384"),
    Variant("so400m-384", "siglip2-so400m-patch14-384-ONNX", "siglip2-so400m-384", 384, 1152,
            "SigLIP 2 so400m 384 (largest, slowest)"),
)}
DEFAULT_ID = "base-256"
DEFAULT_FIT = "squash"


def get(variant_id) -> Variant:
    """The variant for an id; unknown or empty ids fall back to the default."""
    return VARIANTS.get(variant_id if isinstance(variant_id, str) else "", VARIANTS[DEFAULT_ID])


def clean_fit(fit) -> str:
    return fit if fit in FITS else DEFAULT_FIT


def identity(variant_id=DEFAULT_ID, fit=DEFAULT_FIT) -> str:
    """Stable string stored with an index, e.g. ``base-256:squash``."""
    return f"{get(variant_id).id}:{clean_fit(fit)}"


DEFAULT_IDENTITY = identity()


def parse_identity(text) -> tuple:
    """(variant_id, fit) from an identity string; anything unrecognised means the default model."""
    vid, _, fit = (text or "").partition(":")
    return get(vid).id, clean_fit(fit)


def files(variant: Variant) -> list:
    """(url, destination relative to paths.MODELS) for everything the variant needs."""
    base = f"{HF}/onnx-community/{variant.repo}/resolve/main"
    return [(f"{base}/onnx/vision_model.onnx", f"{variant.dirname}/onnx/vision_model.onnx"),
            (f"{base}/onnx/text_model.onnx", f"{variant.dirname}/onnx/text_model.onnx"),
            (f"{base}/tokenizer.json", f"{variant.dirname}/tokenizer.json")]
