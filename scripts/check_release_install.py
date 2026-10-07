"""Install each packaging choice in a disposable Blender extension repository."""

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import tomllib
import zipfile

import bpy
import addon_utils

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from build_releases import BUNDLE_ID, PACKAGES
from _hair_fixtures import evaluated_data, make_garment, make_hair, select_objects
from run_tests import require_isolated_profile


def release_archives(dist, package_ids):
    """Resolve exactly one current archive per requested extension."""
    archives = {}
    for name in package_ids:
        matches = sorted(Path(dist).glob(f"{name}-*.zip"))
        if len(matches) != 1:
            raise RuntimeError(f"Build exactly one current archive for {name}")
        archives[name] = matches[0]
    return archives


def enable_extension(prefix, package_id):
    """Use Blender's ordinary restricted add-on enablement, including errors."""
    module_name = f"{prefix}.{package_id}"
    errors = []
    module = addon_utils.enable(
        module_name, default_set=True, persistent=True, handle_error=errors.append,
    )
    if errors or module is None or addon_utils.check(module_name) != (True, True):
        failure = RuntimeError(f"Blender could not enable release extension {package_id}")
        if errors:
            raise failure from errors[0]
        raise failure
    return module


@contextmanager
def installed_release_repository(archives, *, repository_id, enable_on_install=False):
    """Install genuine ZIPs without saving preferences or using a shared profile.

    Both the full executable and the supported bpy runtime ship Blender's native
    extension installer. Its enable-on-install path imports under RestrictBlend.
    This helper is also used by lifecycle tests against the generated suite ZIP.
    """
    require_isolated_profile()
    prefix = f"bl_ext.{repository_id}"
    repositories = bpy.context.preferences.extensions.repos
    if any(repository.module == repository_id for repository in repositories) or prefix in sys.modules:
        raise RuntimeError("Run the archive check in a fresh Blender process")
    with tempfile.TemporaryDirectory(prefix="helix-release-") as directory:
        repository = Path(directory) / "extensions"
        repository.mkdir(parents=True)
        extension_repository = repositories.new(
            name="Helix Archive Smoke",
            module=repository_id,
            custom_directory=str(repository),
            remote_url="",
            source="USER",
        )
        try:
            for name, archive_path in archives.items():
                destination = repository / name
                with zipfile.ZipFile(archive_path) as archive:
                    manifest = tomllib.loads(archive.read("blender_manifest.toml").decode())
                    if manifest["id"] != name:
                        raise ValueError(f"Unexpected extension identity in {archive_path.name}")
                    for member in archive.infolist():
                        if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                            raise ValueError("Unsafe archive member")
                result = bpy.ops.extensions.package_install_files(
                    filepath=str(Path(archive_path).resolve()), repo=repository_id,
                    enable_on_install=enable_on_install,
                )
                if result != {"FINISHED"}:
                    raise RuntimeError(f"Blender could not install release extension {name}: {result}")
                with zipfile.ZipFile(archive_path) as archive:
                    for member in archive.infolist():
                        if not member.is_dir():
                            installed = destination / member.filename
                            if installed.read_bytes() != archive.read(member):
                                raise RuntimeError(f"Installed extension differs from ZIP: {name}/{member.filename}")
                if enable_on_install and addon_utils.check(f"{prefix}.{name}") != (True, True):
                    raise RuntimeError(f"Installed extension did not enable: {name}")
            yield prefix
        finally:
            errors = []
            try:
                for name in reversed(tuple(archives)):
                    module_name = f"{prefix}.{name}"
                    if addon_utils.check(module_name)[1]:
                        addon_utils.disable(module_name, default_set=True, handle_error=errors.append)
            finally:
                try:
                    repositories.remove(extension_repository)
                finally:
                    for key in list(sys.modules):
                        if key == prefix or key.startswith(prefix + "."):
                            del sys.modules[key]
            if errors:
                raise RuntimeError("Blender could not disable a release extension") from errors[0]


def exercise_tools(component_prefix):
    """Exercise the same native operations from either installed package layout."""
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
    light = bpy.data.lights.new("Archive Shadow Light", 'AREA')
    light.use_shadow = False
    scene.collection.objects.link(bpy.data.objects.new("Archive Shadow Light", light))
    scene.eevee.use_shadows = False
    assert bpy.ops.alsc.enable_eevee_shadows() == {"FINISHED"}
    assert scene.render.engine == 'BLENDER_EEVEE' and scene.eevee.use_shadows
    assert light.use_shadow and light.use_shadow_jitter
    for name in ("hair_cull_build", "hair_cull_remove", "hair_cull_validate",
                 "hair_cull_add_items", "hair_cull_remove_item", "hair_cull_help"):
        assert getattr(bpy.ops.helix, name).get_rna_type()
    hair = sys.modules[f"{component_prefix}.hair_contact_culler"]
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

    # Sequential evaluation fills Blender's native cache before promotion/reset.
    assert bpy.ops.cloth_manager.bake_from_cache.get_rna_type()
    assert bpy.ops.cloth_manager.reset_bakes.get_rna_type()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2)
    cloth_object = bpy.context.object
    cloth_object.name = "Archive Cloth"
    cloth_modifier = cloth_object.modifiers.new("Archive Cloth Simulation", "CLOTH")
    cloth_modifier.settings.quality = 1
    cache = cloth_modifier.point_cache
    cache.frame_start, cache.frame_end = 1, 4
    cloth_object.cloth_tool_selected = True
    for frame in range(1, 5):
        scene.frame_set(frame)
        cloth_object.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
    assert not cache.is_baked and not cache.is_outdated
    assert bpy.ops.cloth_manager.bake_from_cache() == {"FINISHED"}
    assert cache.is_baked
    assert bpy.ops.cloth_manager.reset_bakes() == {"FINISHED"}
    assert not cache.is_baked and cache.is_outdated
    assert cloth_modifier.settings.quality == 1


def main():
    assert bpy.app.version == (5, 2, 2), bpy.app.version_string
    require_isolated_profile()
    dist = ROOT / "dist"
    for repository_id, package_ids in (
        ("helix_review_standalone", PACKAGES),
        ("helix_review_suite", (BUNDLE_ID,)),
    ):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        archives = release_archives(dist, package_ids)
        with installed_release_repository(
            archives, repository_id=repository_id, enable_on_install=True,
        ) as prefix:
            component_prefix = prefix if package_ids == PACKAGES else f"{prefix}.{BUNDLE_ID}"
            exercise_tools(component_prefix)
    print(f"RELEASE_ARCHIVE_INSTALL_SMOKE_OK: {len(PACKAGES)} standalone extensions and full suite installed separately")


if __name__ == "__main__":
    main()
