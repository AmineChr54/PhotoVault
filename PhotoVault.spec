# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.pyw'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.'), ('icon-256.png', '.'), ('docs/USER_GUIDE.md', 'docs'), ('CHANGELOG.md', '.'), ('photovault', 'worker_src/photovault'), ('requirements-ai.txt', 'worker_src'), ('constraints.txt', 'worker_src')],
    hiddenimports=['PySide6.QtSvg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PhotoVault',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PhotoVault',
)
