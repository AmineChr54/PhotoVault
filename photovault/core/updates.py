"""Optional update check: asks GitHub for the latest release tag, at most once a day.

Opt-out (`Settings.check_updates`), never raises, fails silently offline. It only *tells* the user a newer
version exists; nothing is downloaded or installed. The single request sends no identifying data beyond
what any HTTPS request carries (a generic User-Agent, no library or settings data).
"""
import json
import re
import time
import urllib.request

from .. import __version__

REPO = "AmineChr54/PhotoVault"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600
TIMEOUT = 5
MAX_BYTES = 1 << 20

_VERSION_RE = re.compile(r"^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})$")


def parse_version(text) -> tuple | None:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3); anything else (pre-release suffixes, junk) -> None."""
    m = _VERSION_RE.match(str(text or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def is_newer(latest, current=__version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def release_url(tag) -> str:
    """Release page for a tag. Only a strictly parsed version is ever put into the URL."""
    v = parse_version(tag)
    return f"{RELEASES_URL}/tag/v{'.'.join(map(str, v))}" if v else RELEASES_URL


def fetch_latest(urlopen=urllib.request.urlopen, timeout=TIMEOUT) -> str | None:
    """Latest release tag ('v1.2.3') or None on any failure (offline, rate limit, bad JSON, ...)."""
    try:
        req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json",
                                                       "User-Agent": f"PhotoVault/{__version__}"})
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(MAX_BYTES).decode("utf-8"))
        tag = data["tag_name"]
        return tag if parse_version(tag) else None
    except Exception:
        return None


def check_due(settings, now=None) -> bool:
    """True when the check is enabled and the last successful one was more than a day ago."""
    if not settings.check_updates:
        return False
    now = time.time() if now is None else now
    last = settings.last_update_check
    return not isinstance(last, (int, float)) or last > now or now - last >= CHECK_INTERVAL


def record_result(settings, tag, now=None) -> None:
    """Remember a successful check (tag may be the current version). Caller saves the settings."""
    settings.last_update_check = time.time() if now is None else now
    settings.latest_version = tag or ""


def available(settings) -> str:
    """The newer release tag to announce, or '' (disabled, nothing newer, or never checked)."""
    if settings.check_updates and is_newer(settings.latest_version):
        return str(settings.latest_version)
    return ""
