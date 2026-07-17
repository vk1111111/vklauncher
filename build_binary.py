#!/usr/bin/env python3

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"
PACKAGING = ROOT / "packaging"


def _run(cmd: list[str]) -> None:
    subprocess.check_call(cmd, cwd=ROOT)


def _generate_icons() -> Path:
    icons_dir = BUILD / "icons"
    _run([sys.executable, str(ROOT / "scripts" / "generate_icons.py"), "--out", str(icons_dir)])
    return icons_dir


def _platform_info() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine

    if system == "darwin":
        return "macos", arch
    if system == "windows":
        return "windows", arch
    return "linux", arch


def _maybe_make_icns(src_png: Path, dest_icns: Path) -> bool:
    if platform.system() != "Darwin":
        return False
    iconset = BUILD / "vklauncher.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    for size in (16, 32, 128, 256, 512):
        out = iconset / f"icon_{size}x{size}.png"
        out2 = iconset / f"icon_{size}x{size}@2x.png"
        subprocess.check_call(
            ["sips", "-z", str(size), str(size), str(src_png), "--out", str(out)],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["sips", "-z", str(size * 2), str(size * 2), str(src_png), "--out", str(out2)],
            stdout=subprocess.DEVNULL,
        )
    subprocess.check_call(["iconutil", "-c", "icns", str(iconset), "-o", str(dest_icns)])
    return dest_icns.exists()


def main() -> int:
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller is required. Install with:")
        print(f"  {sys.executable} -m pip install pyinstaller pillow")
        return 1

    try:
        import PIL
    except ImportError:
        print("Pillow is required for icons. Install with:")
        print(f"  {sys.executable} -m pip install pillow")
        return 1

    platform_name, arch = _platform_info()
    icons_dir = _generate_icons()
    ico = icons_dir / "vklauncher.ico"

    print(f"Building vklauncher for {platform_name}-{arch} ...")

    os.environ["VKLAUNCHER_ICON_ICO"] = str(ico) if ico.exists() else ""
    mac_png = ASSETS / "icon_mac.png"
    icns = icons_dir / "vklauncher.icns"
    if mac_png.exists():
        _maybe_make_icns(mac_png, icns)
    os.environ["VKLAUNCHER_ICON_ICNS"] = str(icns) if icns.exists() else ""

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "vklauncher.spec"),
    ]
    subprocess.check_call(cmd, cwd=ROOT)

    built = DIST / ("vklauncher.exe" if platform_name == "windows" else "vklauncher")
    if not built.exists():
        print(f"Build finished but binary not found at {built}", file=sys.stderr)
        return 1

    out_dir = DIST / f"vklauncher-{platform_name}-{arch}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    dest_name = "vklauncher.exe" if platform_name == "windows" else "vklauncher"
    dest = out_dir / dest_name
    shutil.copy2(built, dest)
    if platform_name != "windows":
        dest.chmod(dest.stat().st_mode | 0o111)

    # Bundle icons + desktop template for installers.
    if (ASSETS / "icon_universal.png").exists():
        shutil.copy2(ASSETS / "icon_universal.png", out_dir / "icon_universal.png")
    if (ASSETS / "icon_mac.png").exists():
        shutil.copy2(ASSETS / "icon_mac.png", out_dir / "icon_mac.png")
    if ico.exists():
        shutil.copy2(ico, out_dir / "vklauncher.ico")
    if icns.exists():
        shutil.copy2(icns, out_dir / "vklauncher.icns")
    desktop_in = PACKAGING / "vklauncher.desktop.in"
    if desktop_in.exists():
        shutil.copy2(desktop_in, out_dir / "vklauncher.desktop.in")

    archive = DIST / f"vklauncher-{platform_name}-{arch}"
    zip_path = archive.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(archive), "zip", root_dir=out_dir)
    print(f"Binary:  {dest}")
    print(f"Archive: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
