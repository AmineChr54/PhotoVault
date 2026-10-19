"""Updater tests: version compare, release parsing, hash verification, safe extraction, swap staging. No network."""
import hashlib
import io
import json
import threading
import zipfile

import pytest

from photovault.core import updater


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def test_is_newer():
    assert updater.is_newer("v0.2.0", "0.1.0")
    assert updater.is_newer("0.1.10", "0.1.9")
    assert not updater.is_newer("v0.1.0", "0.1.0")
    assert not updater.is_newer("v0.0.9", "0.1.0")
    assert not updater.is_newer("v0.2.0-rc1", "0.1.0")  # pre-release tags are ignored
    assert not updater.is_newer("garbage", "0.1.0")


def release_json(tag="v0.2.0", assets=("PhotoVault-v0.2.0-win64.zip", "PhotoVault-v0.2.0-win64.zip.sha256")):
    return json.dumps({"tag_name": tag, "body": "notes", "html_url": "http://x",
                       "assets": [{"name": n, "browser_download_url": "http://dl/" + n} for n in assets]}).encode()


def test_latest_release_parses_and_fails_silently():
    rel = updater.latest_release(lambda url: FakeResp(release_json()))
    assert rel.tag == "v0.2.0" and rel.zip_url.endswith(".zip") and rel.sha_url.endswith(".sha256")
    assert updater.latest_release(lambda url: FakeResp(release_json(assets=("a.zip",)))) is None  # no sha256 asset
    assert updater.latest_release(lambda url: FakeResp(b"not json")) is None

    def offline(url):
        raise OSError("no network")
    assert updater.latest_release(offline) is None
    assert updater.check("0.2.0", lambda url: FakeResp(release_json())) is None
    assert updater.check("0.1.0", lambda url: FakeResp(release_json())).tag == "v0.2.0"


def make_release(tmp_path, zip_bytes, sha):
    rel = updater.Release("v0.2.0", "", "", "http://dl/p.zip", "p.zip", "http://dl/p.zip.sha256")

    def opener(url):
        return FakeResp((sha + "  p.zip\n").encode() if url.endswith(".sha256") else zip_bytes)
    return rel, opener


def test_download_verifies_sha(tmp_path):
    data = b"zipdata"
    rel, opener = make_release(tmp_path, data, hashlib.sha256(data).hexdigest())
    p = updater.download_and_verify(rel, str(tmp_path / "dl"), opener=opener)
    assert open(p, "rb").read() == data


def test_download_hash_mismatch_refused_and_cleaned(tmp_path):
    rel, opener = make_release(tmp_path, b"zipdata", "0" * 64)
    with pytest.raises(RuntimeError, match="SHA-256"):
        updater.download_and_verify(rel, str(tmp_path / "dl"), opener=opener)
    assert not (tmp_path / "dl" / "p.zip").exists()


def test_download_cancel_cleans_up(tmp_path):
    data = b"zipdata"
    rel, opener = make_release(tmp_path, data, hashlib.sha256(data).hexdigest())
    ev = threading.Event()
    ev.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        updater.download_and_verify(rel, str(tmp_path / "dl"), cancel=ev, opener=opener)
    assert not (tmp_path / "dl" / "p.zip").exists()


def test_extract_rejects_path_traversal(tmp_path):
    z = tmp_path / "bad.zip"
    make_zip(z, {"PhotoVault/PhotoVault.exe": b"x", "../evil.txt": b"x"})
    with pytest.raises(RuntimeError, match="Unsafe"):
        updater.extract_staging(str(z), str(tmp_path / "stage"))
    assert not (tmp_path / "evil.txt").exists()


def test_extract_finds_exe_folder_and_requires_exe(tmp_path):
    z = tmp_path / "ok.zip"
    make_zip(z, {"PhotoVault/PhotoVault.exe": b"x", "PhotoVault/_internal/a.dll": b"y"})
    folder = updater.extract_staging(str(z), str(tmp_path / "stage"))
    assert folder.endswith("PhotoVault") and (tmp_path / "stage" / "PhotoVault" / "_internal" / "a.dll").exists()
    z2 = tmp_path / "noexe.zip"
    make_zip(z2, {"PhotoVault/readme.txt": b"x"})
    with pytest.raises(RuntimeError, match="PhotoVault.exe"):
        updater.extract_staging(str(z2), str(tmp_path / "stage2"))


def test_apply_update_stages_beside_install_and_spawns_helper(tmp_path):
    inst = tmp_path / "Programs" / "PhotoVault"
    inst.mkdir(parents=True)
    (inst / "PhotoVault.exe").write_bytes(b"old")
    photo = tmp_path / "Pictures" / "a.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"photo")
    z = tmp_path / "new.zip"
    make_zip(z, {"PhotoVault/PhotoVault.exe": b"new"})
    calls = []
    updater.apply_update(str(z), str(inst), spawn=lambda *a, **k: calls.append(a[0]))
    assert (inst / "PhotoVault.exe").read_bytes() == b"old"  # swap happens after exit, in the helper
    assert (tmp_path / "Programs" / "PhotoVault.update" / "PhotoVault" / "PhotoVault.exe").read_bytes() == b"new"
    assert photo.read_bytes() == b"photo"
    argv = calls[0]
    assert argv[0] == "powershell.exe" and str(inst) in argv and "-AppPid" in argv


def test_apply_update_refuses_non_install(tmp_path):
    z = tmp_path / "new.zip"
    make_zip(z, {"PhotoVault/PhotoVault.exe": b"new"})
    with pytest.raises(RuntimeError, match="installed"):
        updater.apply_update(str(z), str(tmp_path / "nothing"), spawn=lambda *a, **k: None)
    assert not (tmp_path / "nothing.update").exists()
