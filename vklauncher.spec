# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for vklauncher (onefile binary)."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
root = Path(SPECPATH)

datas = [
    (str(root / "launcher" / "default_settings.json"), "launcher"),
    (str(root / "launcher" / "default_instance_settings.json"), "launcher"),
]
datas += collect_data_files("textual")

binaries = []
hiddenimports = [
    "textual",
    "textual.app",
    "textual.widgets",
    "textual.containers",
    "textual.screen",
    "textual.binding",
    "textual.worker",
    "requests",
    "launcher",
    "launcher.tui",
    "launcher.config",
    "launcher.instances",
    "launcher.versions",
    "launcher.launch",
    "launcher.auth",
    "launcher.modrinth",
]

tmp_ret = collect_all("textual")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="vklauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
