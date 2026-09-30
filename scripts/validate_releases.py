"""Validate the ZIPs with Blender 5.2.2's official extension validator."""

from pathlib import Path
import subprocess
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    if bpy.app.version != (5, 2, 2):
        raise RuntimeError(f"Expected Blender 5.2.2 LTS; found {bpy.app.version_string}")
    scripts = Path(bpy.utils.system_resource("SCRIPTS"))
    validator = scripts / "addons_core" / "bl_pkg" / "cli" / "blender_ext.py"
    if not validator.is_file():
        raise RuntimeError("Blender's extension validator was not found in this runtime")
    archives = sorted((ROOT / "dist").glob("*.zip"))
    if len(archives) != 4:
        raise RuntimeError("Build the four current release archives before validation")
    for archive in archives:
        subprocess.run([sys.executable, str(validator), "validate", str(archive)], check=True)


if __name__ == "__main__":
    main()
