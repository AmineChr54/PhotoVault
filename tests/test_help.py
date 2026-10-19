import os
import sys

from photovault.ui import pages_help


def test_guide_resolves_from_source():
    assert os.path.exists(pages_help.guide_path())
    assert os.path.exists(pages_help.changelog_path())
    assert "What's new" in pages_help.help_markdown()


def test_guide_resolves_when_frozen(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "USER_GUIDE.md").write_text("# Guide\n", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert pages_help.guide_path() == str(tmp_path / "docs" / "USER_GUIDE.md")
    assert pages_help.help_markdown().startswith("# Guide")


def test_whats_new_takes_unreleased_and_latest():
    md = "# C\n\n## Unreleased\n### Added\n- a\n\n## 0.2.0\n- b\n\n## 0.1.0\n- c\n\n## 0.0.1\n- d\n"
    out = pages_help.whats_new(md, versions=1)
    assert "### Unreleased" in out and "### 0.2.0" in out and "0.1.0" not in out
