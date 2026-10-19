"""Update check: version compare, opt-out, once-a-day throttle, silent failure. The network is always mocked."""
import io
import json
import urllib.error

import pytest

from photovault import __version__
from photovault.core import config as cfg
from photovault.core import updates


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(payload, seen=None):
    def _open(req, timeout=None):
        if seen is not None:
            seen.append((req, timeout))
        return FakeResp(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    return _open


def test_parse_and_compare():
    assert updates.parse_version("v1.2.3") == (1, 2, 3) and updates.parse_version("0.10.0") == (0, 10, 0)
    for bad in ("", None, "v1.2", "1.2.3-rc1", "latest", "1.2.3/../x"):
        assert updates.parse_version(bad) is None
    assert updates.is_newer("v0.10.0", "0.9.0")  # numeric, not string, comparison
    assert not updates.is_newer("v0.1.0", "0.1.0") and not updates.is_newer("v0.0.9", "0.1.0")
    assert not updates.is_newer("garbage", "0.1.0") and not updates.is_newer("v9.9.9", "garbage")


def test_release_url_only_from_clean_version():
    assert updates.release_url("v1.2.3") == "https://github.com/AmineChr54/PhotoVault/releases/tag/v1.2.3"
    assert updates.release_url("1.2.3") == "https://github.com/AmineChr54/PhotoVault/releases/tag/v1.2.3"
    assert updates.release_url("evil\" onclick=") == updates.RELEASES_URL


def test_fetch_latest_parses_tag_and_sends_no_library_data():
    seen = []
    assert updates.fetch_latest(opener({"tag_name": "v2.0.0", "html_url": "http://evil"}, seen)) == "v2.0.0"
    req, timeout = seen[0]
    assert req.full_url == updates.API_URL and timeout == updates.TIMEOUT
    assert req.data is None  # plain GET
    assert not any(k.lower() in ("cookie", "authorization") for k in req.headers)


@pytest.mark.parametrize("payload", [b"not json", {"nope": 1}, {"tag_name": "nightly"}, {"tag_name": None}, b"\xff\xfe"])
def test_fetch_latest_bad_responses_are_none(payload):
    assert updates.fetch_latest(opener(payload)) is None


@pytest.mark.parametrize("exc", [urllib.error.URLError("offline"), TimeoutError(), OSError("no route"),
                                 urllib.error.HTTPError("u", 403, "rate limited", {}, None)])
def test_fetch_latest_offline_fails_silently(exc):
    def boom(req, timeout=None):
        raise exc
    assert updates.fetch_latest(boom) is None


def test_check_due_throttle_and_opt_out():
    s = cfg.Settings()
    assert s.check_updates and s.last_update_check == 0.0 and s.latest_version == ""
    assert updates.check_due(s, now=1_000_000)  # never checked
    s.last_update_check = 1_000_000
    assert not updates.check_due(s, now=1_000_000 + 3600)
    assert updates.check_due(s, now=1_000_000 + updates.CHECK_INTERVAL)
    assert updates.check_due(s, now=500_000)  # clock went backwards: don't get stuck
    s.last_update_check = "junk"
    assert updates.check_due(s, now=1_000_000)
    s.check_updates = False
    assert not updates.check_due(s, now=10**9)


def test_record_and_available():
    s = cfg.Settings()
    assert updates.available(s) == ""
    updates.record_result(s, "v99.0.0", now=5.0)
    assert s.last_update_check == 5.0 and updates.available(s) == "v99.0.0"
    s.check_updates = False
    assert updates.available(s) == ""  # opted out: no notice even if one was stored
    s.check_updates = True
    updates.record_result(s, "v" + __version__, now=6.0)
    assert updates.available(s) == ""  # up to date


def test_settings_roundtrip_and_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    (tmp_path / "settings.json").write_text('{"theme": "light"}')  # written before these fields existed
    s = cfg.Settings.load()
    assert s.check_updates is True and s.latest_version == ""
    s.check_updates = False
    s.latest_version = "v1.0.0"
    s.save()
    t = cfg.Settings.load()
    assert t.check_updates is False and t.latest_version == "v1.0.0"
