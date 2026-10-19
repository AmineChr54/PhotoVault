"""Locations of AI data.

Source runs keep everything next to the project (``<project>\\data``). Frozen (exe) builds can't write next to the
exe, so they use ``%LOCALAPPDATA%\\PhotoVault\\ai`` (``PHOTOVAULT_HOME`` overrides the PhotoVault part).
``PHOTOVAULT_AI_DATA`` overrides the data dir in both cases.
"""
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _frozen_data() -> Path:
    home = os.environ.get("PHOTOVAULT_HOME")
    if home:
        return Path(home) / "ai"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PhotoVault" / "ai"


if FROZEN:
    # The exe bundles the worker sources + requirement files under worker_src (see PhotoVault.spec).
    PROJECT = Path(os.environ.get("PHOTOVAULT_PROJECT")
                   or Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "worker_src")
    _default_data = _frozen_data()
else:
    PROJECT = Path(__file__).resolve().parents[2]
    _default_data = PROJECT / "data"
DATA = Path(os.environ.get("PHOTOVAULT_AI_DATA") or _default_data)
AI_DB = DATA / "ai.db"
THUMBS = DATA / "thumbs"
FACE_CROPS = DATA / "facecrops"
MODELS = DATA / "models"
SIGLIP = MODELS / "siglip2"
FACES = MODELS / "faces"
VENV = DATA / "venv"
VENV_PY = VENV / "Scripts" / "python.exe"
SETUP_MARKER = DATA / "setup_in_progress"   # present while a setup is running or was interrupted
EXTRA_ROOTS: list[str] = []  # extra folders to index besides the library roots
