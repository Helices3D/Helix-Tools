"""Validate the ZIPs with Blender 5.2.2's official extension validator."""

from pathlib import Path
import subprocess
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_releases import expected_archives


def main(distribution=None):
    if bpy.app.version != (5, 2, 2):
        raise RuntimeError(f"Expected Blender 5.2.2 LTS; found {bpy.app.version_string}")
    distribution = Path(distribution) if distribution is not None else ROOT / "dist"
    archives = sorted(distribution.glob("*.zip"))
    expected = expected_archives()
    if {archive.name for archive in archives} != expected:
        raise RuntimeError("Build exactly the current release archives before validation")
    if bpy.app.binary_path:
        command = [
            bpy.app.binary_path,
            "--background", "--factory-startup", "--disable-autoexec",
            "--command", "extension", "validate",
        ]
    else:
        scripts = Path(bpy.utils.system_resource("SCRIPTS"))
        validator = scripts / "addons_core" / "bl_pkg" / "cli" / "blender_ext.py"
        if not validator.is_file():
            raise RuntimeError("Blender's extension validator was not found in this runtime")
        command = [sys.executable, str(validator), "validate"]
    for archive in archives:
        subprocess.run([*command, str(archive)], check=True)


if __name__ == "__main__":
    main()
