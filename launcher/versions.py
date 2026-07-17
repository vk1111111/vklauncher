from __future__ import annotations

import concurrent.futures
import hashlib
import json
import platform
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import requests

from . import config

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META_BASE = "https://meta.fabricmc.net/v2"
QUILT_META_BASE = "https://meta.quiltmc.org/v3"

ProgressCB = Callable[[str, int, int], None]


def _noop_progress(label: str, done: int, total: int) -> None:
    pass


@dataclass
class VersionEntry:
    id: str
    type: str
    url: str
    release_time: str


@dataclass
class ResolvedVersion:
    id: str
    raw: dict = field(repr=False)
    inherits_from: str | None = None


def fetch_version_manifest() -> tuple[list[VersionEntry], dict]:
    resp = requests.get(VERSION_MANIFEST_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    entries = [
        VersionEntry(v["id"], v["type"], v["url"], v["releaseTime"])
        for v in data["versions"]
    ]
    return entries, data["latest"]


def _sha1_matches(path: Path, expected: str | None) -> bool:
    if not path.exists():
        return False
    if not expected:
        return path.stat().st_size > 0
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == expected


def _download(url: str, dest: Path, expected_sha1: str | None = None) -> None:
    if _sha1_matches(dest, expected_sha1):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    tmp.replace(dest)


def get_version_json(version_id: str, version_url: str | None = None) -> dict:
    ver_dir = config.VERSIONS_DIR / version_id
    json_path = ver_dir / f"{version_id}.json"
    if not json_path.exists():
        if version_url is None:
            entries, _ = fetch_version_manifest()
            match = next((e for e in entries if e.id == version_id), None)
            if not match:
                raise ValueError(f"Unknown version id: {version_id}")
            version_url = match.url
        resp = requests.get(version_url, timeout=30)
        resp.raise_for_status()
        ver_dir.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(resp.json(), f)
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _rule_applies(rule: dict, features: dict[str, bool] | None = None) -> bool:
    features = features or {}

    os_rule = rule.get("os")
    if os_rule:
        name = os_rule.get("name")
        if name and name != config.current_os_name():
            return False
        arch = os_rule.get("arch")
        if arch and arch != config.current_arch():
            return False

    feature_rule = rule.get("features")
    if feature_rule:
        for key, expected in feature_rule.items():
            if features.get(key, False) != bool(expected):
                return False

    return True


def _rules_allow(
    rules: list[dict] | None,
    features: dict[str, bool] | None = None,
) -> bool:
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if _rule_applies(rule, features):
            allowed = rule.get("action", "allow") == "allow"
    return allowed


def download_client_jar(version_json: dict, progress: ProgressCB = _noop_progress) -> Path:
    version_id = version_json["id"]
    downloads = version_json.get("downloads", {})
    client = downloads.get("client")
    if not client:
        raise ValueError(f"No client jar listed for {version_id}")
    dest = config.VERSIONS_DIR / version_id / f"{version_id}.jar"
    progress("client.jar", 0, 1)
    _download(client["url"], dest, client.get("sha1"))
    progress("client.jar", 1, 1)
    return dest


def _library_dest(path_str: str) -> Path:
    return config.LIBRARIES_DIR / path_str


def maven_coords_to_path(name: str) -> str:
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    group_path = group.replace(".", "/")
    filename = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return f"{group_path}/{artifact}/{version}/{filename}"


def _artifact_url_and_dest(lib: dict) -> tuple[str, Path, str | None] | None:
    downloads = lib.get("downloads", {})
    artifact = downloads.get("artifact")
    if artifact:
        return artifact["url"], _library_dest(artifact["path"]), artifact.get("sha1")

    name = lib.get("name")
    if not name:
        return None
    rel_path = maven_coords_to_path(name)
    base_url = lib.get("url", "https://libraries.minecraft.net/")
    if not base_url.endswith("/"):
        base_url += "/"
    return base_url + rel_path, _library_dest(rel_path), None


def collect_libraries(version_json: dict) -> list[dict]:
    out = []
    for lib in version_json.get("libraries", []):
        if not _rules_allow(lib.get("rules")):
            continue
        out.append(lib)
    return out


def download_libraries_and_natives(
    version_json: dict, natives_dest: Path, progress: ProgressCB = _noop_progress
) -> list[Path]:
    libs = collect_libraries(version_json)
    classpath: list[Path] = []
    jobs: list[tuple[str, Path, str | None]] = []
    natives_to_extract: list[Path] = []

    for lib in libs:
        resolved = _artifact_url_and_dest(lib)
        if resolved:
            url, dest, sha1 = resolved
            jobs.append((url, dest, sha1))
            classpath.append(dest)

        downloads = lib.get("downloads", {})
        classifiers = downloads.get("classifiers")
        natives_map = lib.get("natives")
        if classifiers and natives_map:
            os_key = natives_map.get(config.current_os_name())
            if os_key:
                os_key = os_key.replace(
                    "${arch}", "64" if config.current_arch() == "x64" else "32"
                )
                native_art = classifiers.get(os_key)
                if native_art:
                    dest = _library_dest(native_art["path"])
                    jobs.append((native_art["url"], dest, native_art.get("sha1")))
                    natives_to_extract.append(dest)

    total = len(jobs)
    done = 0
    progress("libraries", done, max(total, 1))

    def _worker(job):
        url, dest, sha1 = job
        _download(url, dest, sha1)
        return dest

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_worker, j) for j in jobs]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()
            done += 1
            progress("libraries", done, max(total, 1))

    natives_dest.mkdir(parents=True, exist_ok=True)
    for lib in libs:
        name = lib.get("name", "")
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        if artifact and "natives" in name:
            natives_to_extract.append(_library_dest(artifact["path"]))

    for jar_path in natives_to_extract:
        if not jar_path.exists():
            continue
        try:
            with zipfile.ZipFile(jar_path) as zf:
                for member in zf.namelist():
                    if member.startswith("META-INF"):
                        continue
                    if member.endswith((".so", ".dylib", ".dll")):
                        zf.extract(member, natives_dest)
        except zipfile.BadZipFile:
            continue

    return classpath


def download_assets(version_json: dict, progress: ProgressCB = _noop_progress) -> Path:
    asset_index = version_json.get("assetIndex")
    if not asset_index:
        return config.ASSET_INDEXES_DIR

    index_id = asset_index["id"]
    index_path = config.ASSET_INDEXES_DIR / f"{index_id}.json"
    _download(asset_index["url"], index_path, asset_index.get("sha1"))

    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

    objects = index_data.get("objects", {})
    jobs = []
    for obj in objects.values():
        h = obj["hash"]
        sub = h[:2]
        dest = config.ASSET_OBJECTS_DIR / sub / h
        url = f"https://resources.download.minecraft.net/{sub}/{h}"
        jobs.append((url, dest, h))

    total = len(jobs)
    done = 0
    progress("assets", done, max(total, 1))

    def _worker(job):
        url, dest, sha1 = job
        _download(url, dest, sha1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(_worker, j) for j in jobs]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()
            done += 1
            if done % 25 == 0 or done == total:
                progress("assets", done, max(total, 1))

    return index_path


def full_install(
    version_id: str, version_url: str | None = None, progress: ProgressCB = _noop_progress
) -> dict:
    vjson = get_version_json(version_id, version_url)
    download_client_jar(vjson, progress)
    natives_dest = config.NATIVES_DIR / version_id
    download_libraries_and_natives(vjson, natives_dest, progress)
    download_assets(vjson, progress)
    return vjson

def fetch_fabric_loader_versions(mc_version: str) -> list[str]:
    resp = requests.get(f"{FABRIC_META_BASE}/versions/loader/{mc_version}", timeout=30)
    resp.raise_for_status()
    return [entry["loader"]["version"] for entry in resp.json()]


def fetch_quilt_loader_versions(mc_version: str) -> list[str]:
    resp = requests.get(f"{QUILT_META_BASE}/versions/loader/{mc_version}", timeout=30)
    resp.raise_for_status()
    return [entry["loader"]["version"] for entry in resp.json()]


def install_fabric(
    mc_version: str, loader_version: str | None = None, progress: ProgressCB = _noop_progress
) -> dict:
    if loader_version is None:
        versions = fetch_fabric_loader_versions(mc_version)
        if not versions:
            raise ValueError(f"No Fabric loader available for {mc_version}")
        loader_version = versions[0]

    profile_id = f"fabric-loader-{loader_version}-{mc_version}"
    ver_dir = config.VERSIONS_DIR / profile_id
    json_path = ver_dir / f"{profile_id}.json"
    if not json_path.exists():
        url = f"{FABRIC_META_BASE}/versions/loader/{mc_version}/{loader_version}/profile/json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        vjson = resp.json()
        vjson["id"] = profile_id
        ver_dir.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(vjson, f)

    vanilla = get_version_json(mc_version)
    download_client_jar(vanilla, progress)
    download_assets(vanilla, progress)

    with open(json_path, "r", encoding="utf-8") as f:
        vjson = json.load(f)
    vjson.setdefault("downloads", vanilla.get("downloads", {}))
    vjson.setdefault("assetIndex", vanilla.get("assetIndex"))
    vjson.setdefault("assets", vanilla.get("assets"))
    natives_dest = config.NATIVES_DIR / profile_id
    download_libraries_and_natives(vjson, natives_dest, progress)
    vanilla_natives_dest = config.NATIVES_DIR / mc_version
    download_libraries_and_natives(vanilla, vanilla_natives_dest, progress)
    return vjson


def install_quilt(
    mc_version: str, loader_version: str | None = None, progress: ProgressCB = _noop_progress
) -> dict:
    if loader_version is None:
        versions = fetch_quilt_loader_versions(mc_version)
        if not versions:
            raise ValueError(f"No Quilt loader available for {mc_version}")
        loader_version = versions[0]

    profile_id = f"quilt-loader-{loader_version}-{mc_version}"
    ver_dir = config.VERSIONS_DIR / profile_id
    json_path = ver_dir / f"{profile_id}.json"
    if not json_path.exists():
        url = f"{QUILT_META_BASE}/versions/loader/{mc_version}/{loader_version}/profile/json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        vjson = resp.json()
        vjson["id"] = profile_id
        ver_dir.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(vjson, f)

    vanilla = get_version_json(mc_version)
    download_client_jar(vanilla, progress)
    download_assets(vanilla, progress)

    with open(json_path, "r", encoding="utf-8") as f:
        vjson = json.load(f)
    vjson.setdefault("downloads", vanilla.get("downloads", {}))
    vjson.setdefault("assetIndex", vanilla.get("assetIndex"))
    vjson.setdefault("assets", vanilla.get("assets"))
    natives_dest = config.NATIVES_DIR / profile_id
    download_libraries_and_natives(vjson, natives_dest, progress)
    vanilla_natives_dest = config.NATIVES_DIR / mc_version
    download_libraries_and_natives(vanilla, vanilla_natives_dest, progress)
    return vjson
