"""Extract release archives into clean extension namespaces and exercise them."""

import importlib
from pathlib import Path
import sys
import tempfile
import types
import zipfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_releases import PACKAGES


def main():
    assert bpy.app.version == (5, 2, 2), bpy.app.version_string
    dist = ROOT / "dist"
    with tempfile.TemporaryDirectory(prefix="helix-release-", dir="/tmp") as directory:
        repository = Path(directory) / "bl_ext" / "helix_review"
        repository.mkdir(parents=True)
        for name in PACKAGES:
            archives = sorted(dist.glob(f"{name}-*.zip"))
            if len(archives) != 1:
                raise RuntimeError(f"Build exactly one current archive for {name}")
            destination = repository / name
            destination.mkdir()
            with zipfile.ZipFile(archives[0]) as archive:
                for member in archive.infolist():
                    if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                        raise ValueError("Unsafe archive member")
                archive.extractall(destination)
        sys.path.insert(0, directory)
        prefix = "bl_ext.helix_review"
        # bpy initializes bl_ext; give this disposable repository its own namespace.
        namespace = types.ModuleType(prefix)
        namespace.__path__ = [str(repository)]
        sys.modules[prefix] = namespace
        modules = [importlib.import_module(f"{prefix}.{name}") for name in PACKAGES]
        enabled = []
        try:
            for module in modules:
                module.register()
                enabled.append(module)
            bpy.ops.wm.read_factory_settings(use_empty=True)
            scene = bpy.context.scene
            scene.render.fps = 30
            scene.jbt_offset = 30
            scene.jbt_seconds = 2
            scene.jbt_frames = 5
            assert bpy.ops.jbt.jump_to_time() == {"FINISHED"}
            assert scene.frame_current == 95
            target = bpy.data.objects.new("Release Target", None)
            scene.collection.objects.link(target)
            target.location = (2, 3, 4)
            bpy.context.view_layer.objects.active = target
            target.select_set(True)
            bpy.context.view_layer.update()
            assert bpy.ops.object.add_smart_empty_baked() == {"FINISHED"}
            assert bpy.context.object.type == "EMPTY"
            assert (bpy.context.object.location - target.location).length < 1e-5
            assert bpy.ops.object.camera_cull_timeline.get_rna_type()
            assert bpy.ops.alsc.refresh_sizes.get_rna_type()
            print("RELEASE_ARCHIVE_INSTALL_SMOKE_OK: all four clean archives enabled together")
        finally:
            for module in reversed(enabled):
                module.unregister()
            sys.path.remove(directory)
            for key in list(sys.modules):
                if key == prefix or key.startswith(prefix + "."):
                    del sys.modules[key]


if __name__ == "__main__":
    main()
