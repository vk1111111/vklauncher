from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

from . import config

VALID_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-\.]{1,64}$")

INSTANCE_SUBDIRS = [
    "mods",
    "saves",
    "resourcepacks",
    "shaderpacks",
    "config",
    "screenshots",
    "logs",
    "crash-reports",
]


@dataclass
class Instance:
    name: str
    mc_version: str
    loader: str = "vanilla"
    loader_version: str = ""
    version_profile_id: str = ""
    created: float = field(default_factory=time.time)
    last_played: float = 0.0
    min_ram_mb: Optional[int] = None
    max_ram_mb: Optional[int] = None
    java_path: str = ""
    modpack_source: str = ""

    @property
    def dir(self) -> Path:
        return config.INSTANCES_DIR / self.name

    @property
    def minecraft_dir(self) -> Path:
        return self.dir / ".minecraft"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_all() -> dict[str, dict]:
    return config.load_json(config.INSTANCES_FILE, {})


def _save_all(data: dict[str, dict]) -> None:
    config.save_json(config.INSTANCES_FILE, data)


def list_instances() -> list[Instance]:
    data = _load_all()
    return [Instance(**v) for v in data.values()]


def get_instance(name: str) -> Optional[Instance]:
    data = _load_all()
    if name not in data:
        return None
    return Instance(**data[name])


def validate_name(name: str) -> None:
    if not VALID_NAME_RE.match(name):
        raise ValueError(
            "Instance name can only contain letters, numbers, spaces, "
            "dashes, underscores and dots (1-64 chars)."
        )


def create_instance(
    name: str,
    mc_version: str,
    loader: str = "vanilla",
    loader_version: str = "",
    version_profile_id: str = "",
) -> Instance:
    validate_name(name)
    data = _load_all()
    if name in data:
        raise ValueError(f"Instance '{name}' already exists.")

    inst = Instance(
        name=name,
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        version_profile_id=version_profile_id or mc_version,
    )
    inst.minecraft_dir.mkdir(parents=True, exist_ok=True)
    for sub in INSTANCE_SUBDIRS:
        (inst.minecraft_dir / sub).mkdir(parents=True, exist_ok=True)

    data[name] = inst.to_dict()
    _save_all(data)
    return inst


def save_instance(inst: Instance) -> None:
    data = _load_all()
    data[inst.name] = inst.to_dict()
    _save_all(data)


def touch_last_played(name: str) -> None:
    data = _load_all()
    if name in data:
        data[name]["last_played"] = time.time()
        _save_all(data)


def delete_instance(name: str) -> None:
    data = _load_all()
    inst_dict = data.pop(name, None)
    _save_all(data)
    if inst_dict:
        d = Path(config.INSTANCES_DIR) / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


def rename_instance(old_name: str, new_name: str) -> Instance:
    validate_name(new_name)
    data = _load_all()
    if old_name not in data:
        raise ValueError(f"No such instance: {old_name}")
    if new_name in data:
        raise ValueError(f"Instance '{new_name}' already exists.")
    old_dir = config.INSTANCES_DIR / old_name
    new_dir = config.INSTANCES_DIR / new_name
    old_dir.rename(new_dir)
    entry = data.pop(old_name)
    entry["name"] = new_name
    data[new_name] = entry
    _save_all(data)
    return Instance(**entry)


def duplicate_instance(src_name: str, new_name: str) -> Instance:
    validate_name(new_name)
    src = get_instance(src_name)
    if not src:
        raise ValueError(f"No such instance: {src_name}")
    data = _load_all()
    if new_name in data:
        raise ValueError(f"Instance '{new_name}' already exists.")
    shutil.copytree(src.dir, config.INSTANCES_DIR / new_name)
    entry = src.to_dict()
    entry["name"] = new_name
    entry["created"] = time.time()
    entry["last_played"] = 0.0
    data[new_name] = entry
    _save_all(data)
    return Instance(**entry)
