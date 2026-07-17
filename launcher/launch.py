from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import config, instances, versions
from .auth import Account
from .instances import Instance

OutputCB = Callable[[str], None]

DEFAULT_FEATURES: dict[str, bool] = {
    "is_demo_user": False,
    "has_custom_resolution": False,
    "has_quick_plays_support": False,
    "is_quick_play_singleplayer": False,
    "is_quick_play_multiplayer": False,
    "is_quick_play_realms": False,
}


def _flatten_args(
    entries,
    subs: dict[str, str],
    features: dict[str, bool] | None = None,
) -> list[str]:
    features = DEFAULT_FEATURES if features is None else features
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(_substitute(entry, subs))
        elif isinstance(entry, dict):
            if not versions._rules_allow(entry.get("rules"), features):
                continue
            value = entry.get("value", [])
            if isinstance(value, str):
                out.append(_substitute(value, subs))
            else:
                out.extend(_substitute(v, subs) for v in value)
    return out


def _substitute(s: str, subs: dict[str, str]) -> str:
    for key, val in subs.items():
        s = s.replace("${" + key + "}", val)
    return s


def _legacy_minecraft_arguments(minecraft_arguments: str, subs: dict[str, str]) -> list[str]:
    return [_substitute(tok, subs) for tok in minecraft_arguments.split(" ") if tok]


def _client_jar_path(instance: Instance, version_json: dict) -> Path:
    version_id = version_json["id"]
    jar_id = (
        version_json.get("jar")
        or version_json.get("_inheritsFrom")
        or instance.mc_version
        or version_id
    )
    candidates = [
        config.VERSIONS_DIR / version_id / f"{version_id}.jar",
        config.VERSIONS_DIR / jar_id / f"{jar_id}.jar",
        config.VERSIONS_DIR / instance.mc_version / f"{instance.mc_version}.jar",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def _natives_dir(instance: Instance, version_json: dict) -> Path:
    version_id = version_json["id"]
    candidates = [config.NATIVES_DIR / version_id]
    inherits = version_json.get("_inheritsFrom")
    if inherits:
        candidates.append(config.NATIVES_DIR / inherits)
    if instance.mc_version:
        candidates.append(config.NATIVES_DIR / instance.mc_version)
    for path in candidates:
        if path.exists() and any(path.iterdir()):
            return path
    return candidates[0]


def build_command(
    instance: Instance,
    version_json: dict,
    account: Account,
    settings: dict,
) -> list[str]:
    version_id = version_json["id"]
    natives_dir = _natives_dir(instance, version_json)
    classpath_jars = versions.collect_libraries(version_json)
    classpath = []
    for lib in classpath_jars:
        resolved = versions._artifact_url_and_dest(lib)
        if resolved:
            classpath.append(str(resolved[1]))

    classpath.append(str(_client_jar_path(instance, version_json)))

    classpath_sep = ";" if config.is_windows() else ":"
    classpath_str = classpath_sep.join(classpath)

    asset_index_id = version_json.get("assets") or version_json.get("assetIndex", {}).get("id", "legacy")

    inst_settings = instances.load_instance_settings(instance)
    defaults = config.default_launcher_settings()
    min_ram = inst_settings.get("min_ram_mb")
    if min_ram is None:
        min_ram = settings.get("min_ram_mb", defaults.get("min_ram_mb", 1024))
    max_ram = inst_settings.get("max_ram_mb")
    if max_ram is None:
        max_ram = settings.get("max_ram_mb", defaults.get("max_ram_mb", 4096))

    subs = {
        "auth_player_name": account.username,
        "version_name": version_id,
        "game_directory": str(instance.minecraft_dir),
        "assets_root": str(config.ASSETS_DIR),
        "assets_index_name": asset_index_id,
        "auth_uuid": account.uuid,
        "auth_access_token": account.access_token or "0",
        "clientid": "",
        "auth_xuid": "",
        "user_type": "msa" if account.kind == "microsoft" else "legacy",
        "version_type": version_json.get("type", "release"),
        "natives_directory": str(natives_dir),
        "launcher_name": "vklauncher",
        "launcher_version": "0.1.0",
        "classpath": classpath_str,
        "library_directory": str(config.LIBRARIES_DIR),
        "classpath_separator": classpath_sep,
    }

    java_path = config.find_java(inst_settings.get("java_path") or None)
    cmd: list[str] = [java_path]

    args_block = version_json.get("arguments")
    if args_block:
        jvm_args = _flatten_args(args_block.get("jvm", []), subs)
        cmd.extend(jvm_args)
        if "-cp" not in jvm_args and "-classpath" not in jvm_args:
            cmd.extend(["-cp", classpath_str])
    else:
        cmd.append(f"-Djava.library.path={natives_dir}")
        cmd.append("-cp")
        cmd.append(classpath_str)

    cmd.append(f"-Xms{min_ram}M")
    cmd.append(f"-Xmx{max_ram}M")

    extra = settings.get("extra_jvm_args", "").strip()
    if extra:
        cmd.extend(extra.split())

    cmd.append(version_json.get("mainClass", "net.minecraft.client.main.Main"))
    if args_block:
        cmd.extend(_flatten_args(args_block.get("game", []), subs))
    elif "minecraftArguments" in version_json:
        cmd.extend(_legacy_minecraft_arguments(version_json["minecraftArguments"], subs))

    return cmd


def launch(
    instance: Instance,
    account: Account,
    settings: dict,
    on_output: Optional[OutputCB] = None,
) -> subprocess.Popen:
    version_json = versions.resolve_version_json(instance.version_profile_id)
    cmd = build_command(instance, version_json, account, settings)

    instance.minecraft_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        cmd,
        cwd=str(instance.minecraft_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ},
    )
    return proc


def stream_output(proc: subprocess.Popen, on_line: OutputCB) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        on_line(line.rstrip("\n"))
