"""Zoom helpers, settings round-trip and theme token contrast (all Qt-free)."""
from photovault.core import config as cfg
from photovault.ui import theme


def test_zoom_clamp_and_step():
    assert cfg.clamp_zoom(0.1) == 0.7 and cfg.clamp_zoom(9) == 2.0
    assert cfg.clamp_zoom("x") == 1.0 and cfg.clamp_zoom(float("nan")) == 1.0
    assert cfg.step_zoom(1.0, 1) == 1.1 and cfg.step_zoom(0.7, -1) == 0.7 and cfg.step_zoom(2.0, 1) == 2.0
    z = 1.0
    for _ in range(3):
        z = cfg.step_zoom(z, 1)
    assert z == 1.3


def test_zoom_setting_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    s = cfg.Settings()
    assert s.ui_zoom == 1.0
    s.ui_zoom = 1.5
    s.save()
    assert cfg.Settings.load().ui_zoom == 1.5
    (tmp_path / "settings.json").write_text('{"ui_zoom": 99, "unknown": 1}')
    assert cfg.Settings.load().ui_zoom == 2.0


def test_scale_helper():
    assert theme.scale(10, 1.5) == 15 and theme.scale(0, 2.0) == 0 and theme.scale(1, 0.7) == 1
    assert "min-height: 51px" in theme.stylesheet(theme.DARK, 1.5)  # 34px button at 150%


def test_theme_token_contrast():
    for name, t in theme.THEMES.items():
        for surf in ("bg", "s1", "s2", "s3"):
            for fg in ("text", "text2"):
                assert theme.contrast(t[fg], t[surf]) >= 4.5, (name, fg, surf)
            assert theme.contrast(t["text3"], t[surf]) >= 3.0, (name, "text3", surf)
        for fg in ("accent_text", "good", "warn", "bad"):
            assert theme.contrast(t[fg], t["s1"]) >= 4.5, (name, fg)
        assert theme.contrast(t["on_accent"], t["accent"]) >= 4.5, name
    assert theme.contrast(theme.DARK["on_accent"], theme.DARK["bad"]) >= 4.5
    for c in (theme.GOOD, theme.WARN, theme.BAD, theme.ACCENT):  # theme-neutral status colours
        assert theme.contrast(c, "#000000") >= 4.5 and theme.contrast(c, "#ffffff") >= 2.4, c
