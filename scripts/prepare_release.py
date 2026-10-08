"""Verify built releases and stage versioned and stable GitHub download assets."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tomllib
import zipfile

from build_releases import BUNDLE_ID, ROOT, release_specs


def prepare(distribution, destination, tag=None):
    distribution, destination = Path(distribution), Path(destination)
    if distribution.resolve() == destination.resolve():
        raise ValueError("Stage release assets in a separate directory from the validated distribution")
    specs = release_specs()
    suite = next(spec for spec in specs if spec["id"] == BUNDLE_ID)
    expected_tag = f"helix-tools-v{suite['version']}"
    if tag is not None and tag != expected_tag:
        raise ValueError(f"Release tag must match the suite manifest: expected {expected_tag}, got {tag}")
    records = json.loads((distribution / "releases.json").read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) != len(specs):
        raise ValueError("Release index must contain exactly the current standalone and suite releases")
    expected_names = {spec["file"] for spec in specs}
    if {path.name for path in distribution.glob("*.zip")} != expected_names:
        raise ValueError("Distribution archives do not match the current release manifests")
    indexed = {}
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get("id"), str)
                or record["id"] in indexed):
            raise ValueError("Release index has an invalid or duplicate entry")
        indexed[record.get("id")] = record
    if set(indexed) != {spec["id"] for spec in specs}:
        raise ValueError("Release index package IDs do not match the current manifests")
    expected_sums = []
    for spec in specs:
        record = indexed[spec["id"]]
        if any(record.get(key) != spec[key] for key in ("file", "name", "version", "kind")):
            raise ValueError(f"Release metadata does not match the manifest: {spec['id']}")
        source = distribution / spec["file"]
        if source.is_symlink():
            raise ValueError(f"Release archive must be a regular file: {source.name}")
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        if checksum != record.get("sha256"):
            raise ValueError(f"Archive checksum does not match the release index: {source.name}")
        with zipfile.ZipFile(source) as archive:
            manifest = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
        if (manifest.get("id") != spec["id"] or manifest.get("version") != spec["version"]
                or manifest.get("blender_version_min") != "5.2.2"):
            raise ValueError(f"Archive manifest does not match release metadata: {source.name}")
        expected_sums.append(f"{checksum}  {source.name}\n")
    if (distribution / "SHA256SUMS").read_text(encoding="utf-8") != "".join(expected_sums):
        raise ValueError("Distribution SHA256SUMS does not match its verified release index")

    asset_names = expected_names | {f"{spec['id']}.zip" for spec in specs}
    generated_names = asset_names | {"SHA256SUMS", "releases.json", "RELEASE_NOTES.md"}
    destination.mkdir(parents=True, exist_ok=True)
    if any(path.name not in generated_names or not path.is_file() or path.is_symlink()
           for path in destination.iterdir()):
        raise ValueError("Release asset directory contains unrelated files; use a clean output directory")
    checksums = {}
    staged_records = []
    for spec in specs:
        record = dict(indexed[spec["id"]])
        alias = f"{spec['id']}.zip"
        for name in (spec["file"], alias):
            target = destination / name
            shutil.copyfile(distribution / spec["file"], target)
            checksum = hashlib.sha256(target.read_bytes()).hexdigest()
            if checksum != record["sha256"]:
                raise OSError(f"Staged release verification failed: {target.name}")
            checksums[name] = checksum
        record["stable_file"] = alias
        staged_records.append(record)
    (destination / "SHA256SUMS").write_text("".join(
        f"{checksums[name]}  {name}\n" for name in sorted(checksums)), encoding="utf-8")
    (destination / "releases.json").write_text(json.dumps(staged_records, indent=2) + "\n", encoding="utf-8")
    components = "\n".join(f"- {spec['name']}: {spec['version']}" for spec in specs if spec["id"] != BUNDLE_ID)
    notes = (
        f"Helix Tools {suite['version']} for **Blender 5.2.2 LTS**.\n\n"
        "Install **helix_tools.zip** from Preferences → Get Extensions → Install from Disk for all seven tools. "
        "Disable their standalone copies before enabling the suite. The suite and standalone copies register "
        "the same tools, so choose one installation method for each tool.\n\n"
        "Each tool remains available as its own independent extension. Stable `<tool_id>.zip` downloads "
        "and the versioned archives contain identical verified bytes.\n\n"
        f"Included component versions:\n\n{components}\n\n"
        "The extensions are distributed under **GPL-3.0-or-later**; each package includes its license "
        "and attribution. `SHA256SUMS` covers every stable and versioned ZIP, and `releases.json` lists "
        "their versions and stable filenames.\n"
    )
    (destination / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    for name in sorted(asset_names):
        print(f"Prepared {name}")
    return staged_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "release-assets")
    parser.add_argument("--tag", help="Require a helix-tools-v tag matching the suite manifest")
    options = parser.parse_args()
    prepare(options.dist, options.output, options.tag)
