"""Update check and self-update from GitHub Releases. Pure Python, no Qt.

Only the packaged exe's own install folder is ever replaced, and only when it holds nothing but the app
(PhotoVault.exe + _internal) and overlaps none of the user's data folders. Photos, settings, the index and the
journal live elsewhere and are never touched. The release zip must match its published SHA-256 before anything is
installed, and the previous build is kept until the new one has started successfully (otherwise it is restored).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass

REPO = "AmineChr54/PhotoVault"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
TIMEOUT = 8
CHUNK = 1 << 20
MAX_ZIP = 1 << 30          # refuse downloads over 1 GiB
MAX_UNPACKED = 3 << 30     # refuse zips that unpack to over 3 GiB
ALLOWED_HOSTS = ("github.com", "githubusercontent.com")  # download hosts (https only, subdomains ok)
APP_ENTRIES = {"photovault.exe", "_internal"}  # a PyInstaller onedir install holds exactly these at its top level


@dataclass
class Release:
    tag: str
    notes: str
    page: str
    zip_url: str
    zip_name: str
    sha_url: str
    sha: str = ""  # filled in by download_and_verify; prepare() re-checks the zip against it


def parse_version(v: str):
    """'v1.2.3' -> (1, 2, 3); pre-release / unparsable tags return None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(tag: str, current: str) -> bool:
    a, b = parse_version(tag), parse_version(current)
    return a is not None and b is not None and a > b


def trusted_url(url: str) -> bool:
    u = urllib.parse.urlparse(url or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "PhotoVault-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def latest_release(opener=_open) -> Release | None:
    """Latest published (non-draft, non-prerelease) release with a Windows zip + .sha256, or None. Never raises."""
    try:
        with opener(API_LATEST) as r:
            d = json.loads(r.read().decode("utf-8"))
        assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
        zips = [n for n in assets if n.endswith("-win64.zip") and os.path.basename(n) == n]
        if not zips or zips[0] + ".sha256" not in assets:
            return None
        zip_url, sha_url = assets[zips[0]], assets[zips[0] + ".sha256"]
        if not (trusted_url(zip_url) and trusted_url(sha_url)):
            return None
        return Release(d["tag_name"], d.get("body") or "", d.get("html_url") or RELEASES_URL, zip_url, zips[0], sha_url)
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
    """Download the zip into `dest_dir` and verify it against the published SHA-256 (the `<hash>  <zipname>` line).
    Returns the zip path and records the hash in rel.sha. Raises RuntimeError on a mismatch, cancel or oversize
    download (the partial/bad file is deleted)."""
    if not (trusted_url(rel.zip_url) and trusted_url(rel.sha_url)):
        raise RuntimeError("Update download is not from an https GitHub URL.")
    name = os.path.basename(rel.zip_name)
    with opener(rel.sha_url) as r:
        text = r.read(4096).decode("utf-8", "replace")
    expected = ""
    for line in text.splitlines():
        m = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*", line)
        if m and os.path.basename(m.group(2)) == name:
            expected = m.group(1).lower()
    if not expected:
        raise RuntimeError("Release has no valid SHA-256 entry for the update zip.")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(os.path.realpath(dest_dir), name)
    try:
        with opener(rel.zip_url) as r, open(path, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            if total > MAX_ZIP:
                raise RuntimeError("Update download is unexpectedly large; refused.")
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("Update cancelled.")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if done > MAX_ZIP:
                    raise RuntimeError("Update download is unexpectedly large; refused.")
                progress(done, total)
        if sha256_of(path) != expected:
            raise RuntimeError("Downloaded update failed the SHA-256 check; nothing was installed.")
    except BaseException:
        if os.path.exists(path):
            os.remove(path)
        raise
    rel.sha = expected
    return path


def extract_staging(zip_path: str, staging: str) -> str:
    """Extract the zip into `staging` (rejecting paths that escape it, huge archives and ambiguous layouts) and return
    the folder holding exactly one PhotoVault.exe (at the root or one level down)."""
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    root = os.path.realpath(staging)
    with zipfile.ZipFile(zip_path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
            raise RuntimeError("Update zip unpacks to an unexpected size; refused.")
        for name in z.namelist():
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise RuntimeError(f"Unsafe path in update zip: {name}")
        z.extractall(root)
    found = [d for d in (root, *(os.path.join(root, n) for n in os.listdir(root)))
             if os.path.isfile(os.path.join(d, "PhotoVault.exe"))]
    if len(found) != 1:
        raise RuntimeError("Update zip must contain exactly one PhotoVault.exe.")
    return found[0]


SWAP_PS1 = r"""param([int]$AppPid, [string]$New, [string]$Inst, [string]$Exe)
$ErrorActionPreference = 'Stop'
# 1. Wait for the app to exit. If it is still running, do nothing (never run two copies against the same data).
$p = Get-Process -Id $AppPid -ErrorAction SilentlyContinue
if ($p) { if (-not $p.WaitForExit(90000)) { exit 2 } }
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$old = "$Inst.old"
# 2. Keep any leftover .old from an earlier update under a unique name instead of deleting it.
if (Test-Path $old) { try { Move-Item $old "$old.$stamp" } catch { exit 3 } }
$moved = $false
for ($i = 0; $i -lt 20 -and -not $moved; $i++) {
    try { Move-Item $Inst $old; $moved = $true } catch { Start-Sleep -Milliseconds 500 }
}
function Restore {
    if (Test-Path $Inst) { Move-Item $Inst "$Inst.failed.$stamp" }
    Move-Item $old $Inst
    try { Start-Process $Exe } catch {}
}
if (-not $moved) { try { Start-Process $Exe } catch {}; exit 1 }
# 3. Install the new build; it must land as the install folder itself (not nested) and contain the exe.
try {
    if (Test-Path $Inst) { throw 'install path reappeared' }
    Move-Item $New $Inst
    if (-not (Test-Path $Exe)) { throw 'new build has no exe' }
    $proc = Start-Process $Exe -PassThru
} catch { Restore; exit 1 }
# 4. Health check: if the new build dies at start, roll back to the previous build.
Start-Sleep -Seconds 10
if ($proc.HasExited) { Restore; exit 1 }
# 5. Success: the old build (verified at update time to hold only the app) is removed.
Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$Inst.update" -Recurse -Force -ErrorAction SilentlyContinue
"""


def install_dir() -> str | None:
    """Folder of the running packaged exe, or None when running from source."""
    return os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else None


def _overlaps(path: str, folder: str) -> bool:
    p, f = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return p == f or p.startswith(f + os.sep) or f.startswith(p + os.sep)


def check_install_safe(inst: str, protected=()) -> None:
    """Refuse unless `inst` is a plain PyInstaller app folder (only PhotoVault.exe + _internal at its top level, not a
    drive root) that overlaps none of the `protected` user-data folders (library, backup, settings, AI data...).
    The swap deletes the previous build, so it must never be a folder that holds anything but the app."""
    inst = os.path.realpath(inst)
    if os.path.dirname(inst) == inst:
        raise RuntimeError("Refusing to update a drive root.")
    if not os.path.isfile(os.path.join(inst, "PhotoVault.exe")):
        raise RuntimeError("Self-update only works from the installed PhotoVault.exe.")
    extra = [n for n in os.listdir(inst) if n.lower() not in APP_ENTRIES]
    if extra:
        raise RuntimeError("Install folder holds files that are not part of the app (" + ", ".join(extra[:3])
                           + "); refusing to replace it. Move PhotoVault into its own folder.")
    for p in protected:
        if p and _overlaps(p, inst):
            raise RuntimeError(f"Your data folder {p} overlaps the install folder; refusing to update.")


def prepare(zip_path: str, inst: str, protected=(), sha: str = "") -> str:
    """Safety checks, re-verify the zip hash, and extract beside the install. Returns the new app folder.
    Does the slow work; safe to call from a background thread."""
    inst = os.path.abspath(inst)
    check_install_safe(inst, protected)
    if sha and sha256_of(zip_path) != sha:
        raise RuntimeError("Update zip changed after verification; nothing was installed.")
    return extract_staging(zip_path, inst + ".update")


def launch_swap(new: str, inst: str, spawn=subprocess.Popen) -> None:
    """Start the swap helper; the caller must exit the app right after."""
    inst = os.path.abspath(inst)
    script = os.path.join(tempfile.mkdtemp(prefix="photovault_swap_"), "swap.ps1")
    with open(script, "w", encoding="utf-8") as f:
        f.write(SWAP_PS1)
    spawn(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script,
           "-AppPid", str(os.getpid()), "-New", new, "-Inst", inst, "-Exe", os.path.join(inst, "PhotoVault.exe")],
          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def apply_update(zip_path: str, inst: str | None = None, protected=(), sha: str = "", spawn=subprocess.Popen) -> None:
    inst = os.path.abspath(inst or install_dir() or "")
    launch_swap(prepare(zip_path, inst, protected, sha), inst, spawn)
