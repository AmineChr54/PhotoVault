"""Update check and self-update from GitHub Releases. Pure Python, no Qt.

Only the packaged exe's own install folder is ever replaced (old copy kept as `<install>.old` until the new one
starts). Photos, settings, the index and the journal live elsewhere and are never touched. The release zip must match
its published SHA-256 before anything is installed.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass

REPO = "AmineChr54/PhotoVault"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
TIMEOUT = 8
CHUNK = 1 << 20


@dataclass
class Release:
    tag: str
    notes: str
    page: str
    zip_url: str
    zip_name: str
    sha_url: str


def parse_version(v: str):
    """'v1.2.3' -> (1, 2, 3); pre-release / unparsable tags return None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag: str, current: str) -> bool:
    a, b = parse_version(tag), parse_version(current)
    return a is not None and b is not None and a > b


def _open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "PhotoVault-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def latest_release(opener=_open) -> Release | None:
    """Latest published (non-draft, non-prerelease) release with a Windows zip + .sha256, or None. Never raises."""
    try:
        with opener(API_LATEST) as r:
            d = json.loads(r.read().decode("utf-8"))
        assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
        zips = [n for n in assets if n.endswith("-win64.zip")]
        if not zips or zips[0] + ".sha256" not in assets:
            return None
        return Release(d["tag_name"], d.get("body") or "", d.get("html_url") or RELEASES_URL,
                       assets[zips[0]], zips[0], assets[zips[0] + ".sha256"])
    except Exception:
        return None


def check(current: str, opener=_open) -> Release | None:
    """The latest release if it is newer than `current`, else None."""
    rel = latest_release(opener)
    return rel if rel and is_newer(rel.tag, current) else None


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download_and_verify(rel: Release, dest_dir: str, progress=lambda done, total: None, cancel=None, opener=_open) -> str:
    """Download the zip into `dest_dir` and verify it against the published SHA-256. Returns the zip path.
    Raises RuntimeError on a hash mismatch or cancel (the partial/bad file is deleted)."""
    with opener(rel.sha_url) as r:
        m = re.search(r"\b[0-9a-fA-F]{64}\b", r.read().decode("utf-8", "replace"))
    if not m:
        raise RuntimeError("Release has no valid SHA-256 file.")
    expected = m.group(0).lower()
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, rel.zip_name)
    try:
        with opener(rel.zip_url) as r, open(path, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("Update cancelled.")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                progress(done, total)
        if sha256_of(path) != expected:
            raise RuntimeError("Downloaded update failed the SHA-256 check; nothing was installed.")
    except BaseException:
        if os.path.exists(path):
            os.remove(path)
        raise
    return path


def extract_staging(zip_path: str, staging: str) -> str:
    """Extract the zip into `staging` (rejecting paths that escape it) and return the folder holding PhotoVault.exe."""
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    root = os.path.realpath(staging)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError(f"Unsafe path in update zip: {name}")
        z.extractall(root)
    for base, _dirs, files in os.walk(root):
        if "PhotoVault.exe" in files:
            return base
    raise RuntimeError("Update zip does not contain PhotoVault.exe.")


SWAP_PS1 = r"""param([int]$AppPid, [string]$New, [string]$Inst, [string]$Exe)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $AppPid -Timeout 60 -ErrorAction SilentlyContinue } catch {}
$old = "$Inst.old"
if (Test-Path $old) { Remove-Item $old -Recurse -Force }
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    try { Move-Item $Inst $old; $ok = $true } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ok) { Start-Process $Exe; exit 1 }
try {
    Move-Item $New $Inst
} catch {
    if (Test-Path $Inst) { Remove-Item $Inst -Recurse -Force }
    Move-Item $old $Inst
    Start-Process $Exe
    exit 1
}
Start-Process $Exe
Start-Sleep -Seconds 5
Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$Inst.update" -Recurse -Force -ErrorAction SilentlyContinue
"""


def install_dir() -> str | None:
    """Folder of the running packaged exe, or None when running from source."""
    return os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else None


def apply_update(zip_path: str, inst: str | None = None, spawn=subprocess.Popen) -> None:
    """Stage the zip next to the install and start the swap helper; the caller must exit the app right after.
    The helper waits for this process to end, swaps folders, relaunches, and rolls back if the swap fails."""
    inst = os.path.abspath(inst or install_dir() or "")
    if not inst or not os.path.isfile(os.path.join(inst, "PhotoVault.exe")):
        raise RuntimeError("Self-update only works from the installed PhotoVault.exe.")
    staging_root = inst + ".update"
    new = extract_staging(zip_path, staging_root)
    script = os.path.join(tempfile.gettempdir(), "photovault_swap.ps1")
    with open(script, "w", encoding="utf-8") as f:
        f.write(SWAP_PS1)
    spawn(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script,
           "-AppPid", str(os.getpid()), "-New", new, "-Inst", inst, "-Exe", os.path.join(inst, "PhotoVault.exe")],
          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
