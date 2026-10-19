"""First-run setup of the optional AI environment. Stdlib only (runs inside the GUI process).

Steps: find Python 3.13 -> create venv in paths.VENV -> pip install requirements-ai.txt with constraints.txt ->
download the ONNX models (resumable). Every step is idempotent so a cancelled/failed setup can simply be re-run.
Python source: an installed Python 3.13 (``py -3.13`` launcher, then ``python`` on PATH). A bundled *embeddable*
Python is NOT used: it ships without ``venv``/``pip``, so it can't build a normal venv reliably.
"""
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

from . import paths

PYTHON_URL = "https://www.python.org/downloads/windows/"
HF = "https://huggingface.co"
# (url, destination relative to paths.MODELS)
MODEL_FILES = [
    (f"{HF}/onnx-community/siglip2-base-patch16-256-ONNX/resolve/main/onnx/vision_model.onnx",
     "siglip2/onnx/vision_model.onnx"),
    (f"{HF}/onnx-community/siglip2-base-patch16-256-ONNX/resolve/main/onnx/text_model.onnx",
     "siglip2/onnx/text_model.onnx"),
    (f"{HF}/onnx-community/siglip2-base-patch16-256-ONNX/resolve/main/tokenizer.json", "siglip2/tokenizer.json"),
    (f"{HF}/public-data/insightface/resolve/main/models/buffalo_l/det_10g.onnx", "faces/det_10g.onnx"),
    (f"{HF}/public-data/insightface/resolve/main/models/buffalo_l/w600k_r50.onnx", "faces/w600k_r50.onnx"),
]
NOFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupCancelled(Exception):
    pass


class SetupError(Exception):
    pass


def _version_ok(text: str) -> bool:
    m = re.search(r"Python (\d+)\.(\d+)", text or "")
    return bool(m) and (int(m[1]), int(m[2])) == (3, 13)


def find_python(run=subprocess.run, which=shutil.which):
    """Command list for a Python 3.13 interpreter, or None."""
    for cmd in (["py", "-3.13"], ["python"], ["python3"]):
        if which(cmd[0]) is None:
            continue
        try:
            r = run(cmd + ["--version"], capture_output=True, text=True, timeout=30, creationflags=NOFLAGS)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and _version_ok((r.stdout or "") + (r.stderr or "")):
            return cmd
    return None


def models_ready() -> bool:
    return all((paths.MODELS / rel).exists() for _, rel in MODEL_FILES)


def download(url, dest, progress=None, cancel=None, opener=urllib.request.urlopen):
    """Download to dest via dest.part, resuming with a Range request. progress(done, total)."""
    dest = Path(dest)
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with opener(req, timeout=60) as r:
        if have and getattr(r, "status", 200) != 206:
            have = 0  # server ignored Range: restart
        length = int(r.headers.get("Content-Length") or 0)
        total = have + length if length else 0
        with open(part, "ab" if have else "wb") as f:
            done = have
            while True:
                if cancel is not None and cancel.is_set():
                    raise SetupCancelled()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if total and part.stat().st_size != total:
        raise SetupError(f"incomplete download: {dest.name}")
    os.replace(part, dest)


class AiSetup:
    """Runs the setup. Call run() on a worker thread; cancel() from anywhere. progress(stage, fraction|None, text)."""

    def __init__(self, progress=None, stream=None, downloader=download, find=find_python):
        self.progress = progress or (lambda *a: None)
        self.cancel_ev = threading.Event()
        self.proc = None
        self._downloader = downloader
        self._find = find
        self._stream = stream or self._stream_cmd

    def cancel(self):
        self.cancel_ev.set()
        p = self.proc
        if p and p.poll() is None:
            p.terminate()

    def _stream_cmd(self, cmd, on_line):
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     creationflags=NOFLAGS)
        for line in self.proc.stdout:
            on_line(line.rstrip())
        rc = self.proc.wait()
        if self.cancel_ev.is_set():
            raise SetupCancelled()
        return rc

    def _check(self):
        if self.cancel_ev.is_set():
            raise SetupCancelled()

    def run(self):
        paths.DATA.mkdir(parents=True, exist_ok=True)
        paths.SETUP_MARKER.write_text("running", encoding="utf-8")
        if not paths.VENV_PY.exists():
            py = self._find()
            if py is None:
                raise SetupError(f"Python 3.13 was not found. Install it from {PYTHON_URL} "
                                 "(tick 'py launcher') and retry.")
            self.progress("venv", None, "Creating the AI environment...")
            if self._stream(py + ["-m", "venv", str(paths.VENV)], lambda s: None) != 0 \
                    or not paths.VENV_PY.exists():
                shutil.rmtree(paths.VENV, ignore_errors=True)
                raise SetupError("Could not create the Python environment (venv).")
        self._check()
        req, cons = paths.PROJECT / "requirements-ai.txt", paths.PROJECT / "constraints.txt"
        cmd = [str(paths.VENV_PY), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)]
        if cons.exists():
            cmd += ["-c", str(cons)]
        self.progress("pip", None, "Installing AI packages (a few minutes)...")
        if self._stream(cmd, lambda s: self.progress("pip", None, s[:90]) if s else None) != 0:
            raise SetupError("Installing the AI packages failed (network / disk space?) - retry to resume.")
        for i, (url, rel) in enumerate(MODEL_FILES):
            self._check()
            name = rel.rsplit("/", 1)[-1]

            def prog(done, total, i=i, name=name):
                f = (i + (done / total if total else 0)) / len(MODEL_FILES)
                self.progress("models", f, f"Downloading {name}: {done >> 20} MB"
                              + (f" / {total >> 20} MB" if total else ""))
            try:
                self._downloader(url, paths.MODELS / rel, prog, self.cancel_ev)
            except (OSError, SetupError) as e:
                raise SetupError(f"Downloading {name} failed: {e} - retry to resume.") from e
        paths.SETUP_MARKER.unlink(missing_ok=True)
        self.progress("done", 1.0, "AI features are ready.")
