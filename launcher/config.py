from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _default_app_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / "vklauncher"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "vklauncher"
    # Linux / other unix
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "vklauncher"


def _package_dir() -> Path:
    """Directory containing packaged data (JSON defaults).

    PyInstaller onefile extracts to sys._MEIPASS; datas are placed under
    ``launcher/`` there. In a normal install, files live next to this module.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "launcher"
        if bundled.is_dir():
            return bundled
        return Path(meipass)
    return Path(__file__).resolve().parent


PACKAGE_DIR = _package_dir()
DEFAULT_SETTINGS_FILE = PACKAGE_DIR / "default_settings.json"
DEFAULT_INSTANCE_SETTINGS_FILE = PACKAGE_DIR / "default_instance_settings.json"

APP_DIR = Path(os.environ.get("MC_TUI_LAUNCHER_HOME", _default_app_dir()))
SHARED_DIR = APP_DIR / "shared"
VERSIONS_DIR = SHARED_DIR / "versions"
LIBRARIES_DIR = SHARED_DIR / "libraries"
ASSETS_DIR = SHARED_DIR / "assets"
ASSET_OBJECTS_DIR = ASSETS_DIR / "objects"
ASSET_INDEXES_DIR = ASSETS_DIR / "indexes"
NATIVES_DIR = SHARED_DIR / "natives"
INSTANCES_DIR = APP_DIR / "instances"

SETTINGS_FILE = APP_DIR / "settings.json"
ACCOUNTS_FILE = APP_DIR / "accounts.json"
INSTANCES_FILE = APP_DIR / "instances.json"


def ensure_dirs() -> None:
    for d in (
        APP_DIR,
        SHARED_DIR,
        VERSIONS_DIR,
        LIBRARIES_DIR,
        ASSET_OBJECTS_DIR,
        ASSET_INDEXES_DIR,
        NATIVES_DIR,
        INSTANCES_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(default))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def default_launcher_settings() -> dict[str, Any]:
    return load_json(DEFAULT_SETTINGS_FILE, {})


def default_instance_settings() -> dict[str, Any]:
    return load_json(DEFAULT_INSTANCE_SETTINGS_FILE, {})


def load_settings() -> dict[str, Any]:
    """Launcher settings: packaged defaults merged with user settings.json."""
    settings = default_launcher_settings()
    settings.update(load_json(SETTINGS_FILE, {}))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    save_json(SETTINGS_FILE, settings)


def current_os_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "osx"
    if system == "Windows":
        return "windows"
    return "linux"


def current_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def open_directory(path: Path) -> None:
    """Open a directory in the system file manager."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    target = str(path)
    if is_windows():
        os.startfile(target)  # type: ignore[attr-defined]
    elif is_macos():
        subprocess.Popen(["open", target], start_new_session=True)
    else:
        subprocess.Popen(["xdg-open", target], start_new_session=True)


def find_java(java_path: str | None = None) -> str:
    if java_path:
        return java_path

    settings = load_settings()
    if settings.get("java_path"):
        return settings["java_path"]

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if is_windows() else "java")
        if candidate.exists():
            return str(candidate)

    exe = "java.exe" if is_windows() else "java"
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(p) / exe
        if candidate.exists():
            return str(candidate)

    if is_macos():
        jvm_dir = Path("/Library/Java/JavaVirtualMachines")
        if jvm_dir.exists():
            for jdk in sorted(jvm_dir.glob("*/Contents/Home/bin/java"), reverse=True):
                if jdk.exists():
                    return str(jdk)

    return exe
