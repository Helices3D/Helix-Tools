"""Mirror the canonical updater into every independently installable package."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "shared" / "helix_updates"
PACKAGES = ("jump_by_time", "smart_empty", "camera_timeline_culler",
            "area_light_shadow_control", "hair_contact_culler", "cloth_cache_manager", "texture_resolution")


def files(directory):
    return {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def destinations():
    return [ROOT / "addons" / name / "_updates" for name in PACKAGES] + [ROOT / "suite" / "helix_tools" / "_updates"]


def check_mirrors():
    expected = files(CANONICAL)
    if not expected or any(files(destination) != expected for destination in destinations()):
        raise ValueError("Updater mirrors differ from shared/helix_updates; run python scripts/sync_updaters.py")


def synchronize():
    expected = files(CANONICAL)
    for destination in destinations():
        destination.mkdir(parents=True, exist_ok=True)
        for path in destination.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and path.relative_to(destination) not in expected:
                path.unlink()
        for relative, data in expected.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    check_mirrors()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check_mirrors() if args.check else synchronize()
