"""Extract release archives into clean extension namespaces and exercise them."""

from pathlib import Path
import sys
import tempfile
import zipfile

import bpy
import addon_utils

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from build_releases import PACKAGES
from _hair_fixtures import evaluated_data, make_garment, make_hair, select_objects
from run_tests import require_isolated_profile


def main():
    assert bpy.app.version == (5, 2, 2), bpy.app.version_string
    require_isolated_profile()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    dist = ROOT / "dist"
    repository_id = "helix_review"
    prefix = f"bl_ext.{repository_id}"
    repositories = bpy.context.preferences.extensions.repos
    if any(repository.module == repository_id for repository in repositories) or prefix in sys.modules:
        raise RuntimeError("Run the archive check in a fresh Blender process")
    with tempfile.TemporaryDirectory(prefix="helix-release-") as directory:
        repository = Path(directory) / "extensions"
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
        # Blender creates the extension namespace and loads each archive using
        # its normal compatibility checks and restricted registration context.
        # No user preferences are saved by this disposable check.
        extension_repository = repositories.new(
            name="Helix Archive Smoke",
            module=repository_id,
            custom_directory=str(repository),
            remote_url="",
            source="USER",
        )
        enabled = []
        try:
            for name in PACKAGES:
                module_name = f"{prefix}.{name}"
                errors = []
                module = addon_utils.enable(
                    module_name,
                    default_set=True,
                    persistent=True,
                    handle_error=errors.append,
                )
                if module is not None:
                    enabled.append(module_name)
                if errors or module is None or addon_utils.check(module_name) != (True, True):
                    failure = RuntimeError(f"Blender could not enable release extension {name}")
                    if errors:
                        raise failure from errors[0]
                    raise failure
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
            for name in ("hair_cull_build", "hair_cull_remove", "hair_cull_validate",
                         "hair_cull_add_items", "hair_cull_remove_item", "hair_cull_help"):
                assert getattr(bpy.ops.helix, name).get_rna_type()
            hair = sys.modules[f"{prefix}.hair_contact_culler"]
            source, garment = make_hair("Archive Hair"), make_garment("Archive Garment")
            select_objects(source, garment)
            assert bpy.ops.helix.hair_cull_build() == {"FINISHED"}
            metadata = hair.find_bundle(source)
            assert metadata and metadata.helix_hair_cull.valid
            assert len(evaluated_data(source).points) == 3
            assert len(source.data.points) == 4
            assert bpy.ops.helix.hair_cull_remove() == {"FINISHED"}
            assert hair.find_bundle(source) is None
            assert len(evaluated_data(source).points) == 4
            print(f"RELEASE_ARCHIVE_INSTALL_SMOKE_OK: all {len(PACKAGES)} clean archives enabled together")
        finally:
            errors = []
            try:
                for module_name in reversed(enabled):
                    addon_utils.disable(
                        module_name, default_set=True, handle_error=errors.append,
                    )
            finally:
                try:
                    repositories.remove(extension_repository)
                finally:
                    for key in list(sys.modules):
                        if key == prefix or key.startswith(prefix + "."):
                            del sys.modules[key]
            if errors:
                raise RuntimeError("Blender could not disable a release extension") from errors[0]


if __name__ == "__main__":
    main()
