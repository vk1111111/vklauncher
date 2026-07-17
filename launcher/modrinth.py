# experimental modrinth browser

from __future__ import annotations

import concurrent.futures
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from . import config, instances, versions

API_BASE = "https://api.modrinth.com/v2"
HEADERS = {"User-Agent": "vklauncher/0.1.0 (github.com/example/vklauncher)"}

ProgressCB = Callable[[str, int, int], None]


def _noop_progress(label: str, done: int, total: int) -> None:
    pass


@dataclass
class ModpackHit:
    slug: str
    title: str
    description: str
    downloads: int
    author: str
    project_id: str


@dataclass
class PackVersion:
    id: str
    name: str
    version_number: str
    game_versions: list[str]
    loaders: list[str]
    file_url: str
    file_name: str


def search_modpacks(query: str, limit: int = 20, offset: int = 0) -> list[ModpackHit]:
    facets = json.dumps([["project_type:modpack"]])
    resp = requests.get(
        f"{API_BASE}/search",
        params={"query": query, "facets": facets, "limit": limit, "offset": offset},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    hits = []
    for h in resp.json().get("hits", []):
        hits.append(
            ModpackHit(
                slug=h.get("slug", ""),
                title=h.get("title", ""),
                description=h.get("description", ""),
                downloads=h.get("downloads", 0),
                author=h.get("author", ""),
                project_id=h.get("project_id", ""),
            )
        )
    return hits


def get_project_versions(project_id_or_slug: str) -> list[PackVersion]:
    resp = requests.get(
        f"{API_BASE}/project/{project_id_or_slug}/version", headers=HEADERS, timeout=30
    )
    resp.raise_for_status()
    out = []
    for v in resp.json():
        files = v.get("files", [])
        primary = next((f for f in files if f.get("primary")), files[0] if files else None)
        if not primary:
            continue
        out.append(
            PackVersion(
                id=v["id"],
                name=v.get("name", v.get("version_number", "")),
                version_number=v.get("version_number", ""),
                game_versions=v.get("game_versions", []),
                loaders=v.get("loaders", []),
                file_url=primary["url"],
                file_name=primary["filename"],
            )
        )
    return out


def _download_mrpack(pack_version: PackVersion, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pack_version.file_name
    with requests.get(pack_version.file_url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def install_modpack(
    pack_version: PackVersion,
    instance_name: str,
    progress: ProgressCB = _noop_progress,
) -> instances.Instance:
    with tempfile.TemporaryDirectory(prefix="mrpack-") as tmp:
        tmp_path = Path(tmp)
        progress("Downloading modpack", 0, 1)
        mrpack_path = _download_mrpack(pack_version, tmp_path)
        progress("Downloading modpack", 1, 1)

        extract_dir = tmp_path / "extracted"
        with zipfile.ZipFile(mrpack_path) as zf:
            zf.extractall(extract_dir)

        index_path = extract_dir / "modrinth.index.json"
        if not index_path.exists():
            raise ValueError("Not a valid .mrpack file (missing modrinth.index.json).")
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

        deps: dict = index.get("dependencies", {})
        mc_version = deps.get("minecraft")
        if not mc_version:
            raise ValueError("Modpack index is missing a Minecraft version.")

        loader = "vanilla"
        loader_version = ""
        if "fabric-loader" in deps:
            loader, loader_version = "fabric", deps["fabric-loader"]
        elif "quilt-loader" in deps:
            loader, loader_version = "quilt", deps["quilt-loader"]
        elif "forge" in deps or "neoforge" in deps:
            raise ValueError(
                "This modpack requires Forge/NeoForge, which isn't supported yet. "
                "Try a Fabric- or Quilt-based modpack instead."
            )

        progress("Installing game files", 0, 1)
        if loader == "fabric":
            vjson = versions.install_fabric(
                mc_version, loader_version or None, lambda l, d, t: progress(l, d, t)
            )
        elif loader == "quilt":
            vjson = versions.install_quilt(
                mc_version, loader_version or None, lambda l, d, t: progress(l, d, t)
            )
        else:
            vjson = versions.full_install(
                mc_version, progress=lambda l, d, t: progress(l, d, t)
            )
        profile_id = vjson["id"]

        base_name = instance_name
        n = 1
        final_name = base_name
        while instances.get_instance(final_name):
            n += 1
            final_name = f"{base_name} ({n})"

        inst = instances.create_instance(
            name=final_name,
            mc_version=mc_version,
            loader=loader,
            loader_version=loader_version,
            version_profile_id=profile_id,
        )
        inst.modpack_source = f"modrinth:{pack_version.id}"
        instances.save_instance(inst)

        files = index.get("files", [])
        applicable = []
        for f in files:
            env = f.get("env", {})
            if env.get("client") == "unsupported":
                continue
            applicable.append(f)

        total = len(applicable)
        done = 0
        progress("Downloading modpack files", done, max(total, 1))

        def _fetch_one(entry):
            rel_path = entry["path"]
            dest = inst.minecraft_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            downloads = entry.get("downloads", [])
            if not downloads:
                return
            last_err = None
            for url in downloads:
                try:
                    with requests.get(url, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        with open(dest, "wb") as fh:
                            for chunk in r.iter_content(1 << 16):
                                fh.write(chunk)
                    return
                except requests.RequestException as e:
                    last_err = e
                    continue
            if last_err:
                raise last_err

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_fetch_one, e) for e in applicable]
            for fut in concurrent.futures.as_completed(futs):
                fut.result()
                done += 1
                progress("Downloading modpack files", done, max(total, 1))

        for override_dir_name in ("overrides", "client-overrides"):
            override_dir = extract_dir / override_dir_name
            if override_dir.exists():
                shutil.copytree(override_dir, inst.minecraft_dir, dirs_exist_ok=True)

        progress("Done", 1, 1)
        return inst
