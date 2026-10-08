"""Build reproducible standalone extensions and the optional Helix Tools suite."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("jump_by_time", "smart_empty", "camera_timeline_culler", "area_light_shadow_control",
            "hair_contact_culler", "cloth_cache_manager", "texture_resolution")
BUNDLE_ID = "helix_tools"


def release_specs():
    """Return the seven standalone releases followed by the optional full suite."""
    releases = []
    for name in (*PACKAGES, BUNDLE_ID):
        kind = "suite" if name == BUNDLE_ID else "standalone"
        source = ROOT / ("suite" if kind == "suite" else "addons") / name
        manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
        if manifest["id"] != name or manifest["blender_version_min"] != "5.2.2":
            raise ValueError(f"Unexpected package identity/version target: {name}")
        version = manifest["version"]
        if not isinstance(version, str) or not re.fullmatch(
                r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version):
            raise ValueError(f"Unexpected release version: {name}")
        releases.append({"id": name, "name": manifest["name"], "version": version,
                         "source": source, "file": f"{name}-{version}.zip", "kind": kind})
    return releases


def expected_archives():
    return {release["file"] for release in release_specs()}


def _source_files(source):
    for path in sorted(source.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Release source must not contain symbolic links: {path}")
        if path.is_file():
            yield path.relative_to(source).as_posix(), path.read_bytes()


def _write_archive(output, files):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(destination):
    from sync_updaters import check_mirrors
    check_mirrors()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    releases = []
    specs = release_specs()
    for spec in specs:
        files = dict(_source_files(spec["source"]))
        if spec["kind"] == "suite":
            if any(name.split('/')[0] in PACKAGES for name in files):
                raise ValueError("Suite skeleton must not duplicate component source directories")
            for component in specs[:-1]:
                for name, data in _source_files(component["source"]):
                    if Path(name).name != "blender_manifest.toml":
                        files[f"{component['id']}/{name}"] = data
            for name in ("AUTHORS.md", "LICENSE"):
                if name not in files:
                    files[name] = (ROOT / name).read_bytes()
            components = [{"id": release["id"], "name": release["name"],
                           "version": release["version"], "standalone_sha256": release["sha256"]}
                          for release in releases]
            files["components.json"] = (json.dumps(components, indent=2) + "\n").encode("utf-8")
        output = destination / spec["file"]
        _write_archive(output, files)
        releases.append({key: spec[key] for key in ("file", "id", "name", "version", "kind")}
                        | {"sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
    (destination / "SHA256SUMS").write_text("".join(
        f"{release['sha256']}  {release['file']}\n" for release in releases), encoding="utf-8")
    (destination / "releases.json").write_text(json.dumps(releases, indent=2) + "\n", encoding="utf-8")
    for release in releases:
        print(f"Built {release['file']}")
    return releases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    build(parser.parse_args().output)
