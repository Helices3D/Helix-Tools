"""Build reproducible, independently installable Blender extension archives."""

import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("jump_by_time", "smart_empty", "camera_timeline_culler", "area_light_shadow_control")


def build(destination):
    destination.mkdir(parents=True, exist_ok=True)
    releases = []
    for name in PACKAGES:
        source = ROOT / "addons" / name
        manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
        if manifest["id"] != name or manifest["blender_version_min"] != "5.2.2":
            raise ValueError(f"Unexpected package identity/version target: {name}")
        files = sorted(path for path in source.rglob("*") if path.is_file()
                       and "__pycache__" not in path.parts and path.suffix != ".pyc")
        output = destination / f"{name}-{manifest['version']}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        releases.append({"file": output.name, "id": name, "version": manifest["version"],
                         "sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
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
