#!/usr/bin/env python3
"""Build a standalone vklauncher binary for the current platform."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is required. Install with:")
        print(f"  {sys.executable} -m pip install pyinstaller")
        return 1

    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine

    if system == "darwin":
        platform_name = "macos"
    elif system == "windows":
        platform_name = "windows"
    else:
        platform_name = "linux"

    print(f"Building vklauncher for {platform_name}-{arch} ...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "vklauncher.spec"),
    ]
    subprocess.check_call(cmd, cwd=ROOT)

    built = DIST / ("vklauncher.exe" if system == "windows" else "vklauncher")
    if not built.exists():
        print(f"Build finished but binary not found at {built}", file=sys.stderr)
        return 1

    out_dir = DIST / f"vklauncher-{platform_name}-{arch}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    dest_name = "vklauncher.exe" if system == "windows" else "vklauncher"
    dest = out_dir / dest_name
    shutil.copy2(built, dest)
    if system != "windows":
        dest.chmod(dest.stat().st_mode | 0o111)

    archive = DIST / f"vklauncher-{platform_name}-{arch}"
    shutil.make_archive(str(archive), "zip", root_dir=out_dir)
    print(f"Binary:  {dest}")
    print(f"Archive: {archive}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
