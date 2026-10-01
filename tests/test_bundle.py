"""Exercise the generated full-suite ZIP and switching from standalone tools."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_releases import BUNDLE_ID, PACKAGES, build
from check_release_install import enable_extension, installed_release_repository, release_archives
from _hair_fixtures import evaluated_data, make_garment, make_hair, select_objects
from test_integration import PROPERTY_OWNERS, PUBLIC_OPERATORS, _module_classes, _properties


def _handlers():
    return {name: tuple(callbacks) for name in dir(bpy.app.handlers)
            if isinstance(callbacks := getattr(bpy.app.handlers, name), list)}


def _property_definitions():
    return {owner: dict(owner.__dict__) for owner in PROPERTY_OWNERS}


class FullSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        cls.baseline_properties = _properties()
        cls.directory = tempfile.TemporaryDirectory(prefix="helix-bundle-tests-")
        cls.repository = None
        try:
            dist = Path(cls.directory.name) / "dist"
            cls.releases = build(dist)
            cls.repository = installed_release_repository(
                release_archives(dist, (BUNDLE_ID,)), repository_id="helix_bundle_tests",
            )
            cls.prefix = cls.repository.__enter__()
            cls.bundle = enable_extension(cls.prefix, BUNDLE_ID)
            cls.classes = tuple(cls for module in cls.bundle.modules for cls in _module_classes(module))
            cls.standalones = tuple(importlib.import_module(name) for name in PACKAGES)
            addon_utils.disable(cls.bundle.__name__, default_set=True)
        except Exception:
            if cls.repository is not None:
                cls.repository.__exit__(*sys.exc_info())
            cls.directory.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.bundle.unregister()
            for module in reversed(cls.standalones):
                module.unregister()
        finally:
            try:
                cls.repository.__exit__(None, None, None)
            finally:
                cls.directory.cleanup()

    def setUp(self):
        # Keep the disposable extension repository while replacing scene data.
        scene = bpy.data.scenes.new("Bundle Regression")
        bpy.context.window.scene = scene
        for old in list(bpy.data.scenes):
            if old != scene:
                bpy.data.scenes.remove(old)
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.scene = scene
        self.enabled_standalones = []
        self.before_handlers = _handlers()
        self.assertEqual(_properties(), self.baseline_properties)

    def tearDown(self):
        try:
            if addon_utils.check(self.bundle.__name__)[1]:
                addon_utils.disable(self.bundle.__name__, default_set=True)
            else:
                self.bundle.unregister()
        finally:
            while self.enabled_standalones:
                self.enabled_standalones.pop().unregister()

    def enable_standalones(self):
        for module in self.standalones:
            module.register()
            self.enabled_standalones.append(module)

    def disable_standalones(self):
        while self.enabled_standalones:
            self.enabled_standalones.pop().unregister()

    def activate_bundle(self):
        # Blender drops the wrapper from sys.modules after failed enablement.
        # Use its returned live module for subsequent register/cleanup checks.
        bundle = enable_extension(self.prefix, BUNDLE_ID)
        type(self).bundle = bundle
        type(self).classes = tuple(cls for module in bundle.modules for cls in _module_classes(module))
        return bundle

    def assert_clean(self):
        self.assertEqual(_properties(), self.baseline_properties)
        self.assertEqual(_handlers(), self.before_handlers)
        for cls in self.classes:
            self.assertFalse(cls.is_registered, cls.__name__)
        for module in self.bundle.modules:
            for name in ("_tick", "_deferred_fps_migration"):
                callback = getattr(module, name, None)
                if callback is not None:
                    self.assertFalse(bpy.app.timers.is_registered(callback))
        for identifier in PUBLIC_OPERATORS:
            namespace, name = identifier.split(".", 1)
            with self.assertRaises(KeyError, msg=identifier):
                getattr(getattr(bpy.ops, namespace), name).get_rna_type()

    def test_installed_suite_enables_all_tools_and_cleans_repeated_native_cycles(self):
        self.assertEqual(self.bundle.MODULE_NAMES, PACKAGES)
        installed_root = Path(self.bundle.__file__).resolve().parent
        self.assertEqual(installed_root.name, BUNDLE_ID)
        inventory = json.loads((installed_root / "components.json").read_text())
        self.assertEqual(tuple(item["id"] for item in inventory), PACKAGES)
        releases = {item["id"]: item for item in self.releases}
        for component, item in zip(self.bundle.modules, inventory):
            self.assertEqual(component.__name__.rsplit(".", 1)[-1], item["id"])
            self.assertTrue(Path(component.__file__).resolve().is_relative_to(installed_root))
            self.assertEqual(".".join(map(str, component.bl_info["version"])), item["version"])
            self.assertEqual(item["version"], releases[item["id"]]["version"])
            self.assertEqual(item["standalone_sha256"], releases[item["id"]]["sha256"])
        for cycle in range(2):
            with self.subTest(cycle=cycle):
                enabled = self.activate_bundle()
                self.assertEqual(Path(enabled.__file__).resolve().parent, installed_root)
                self.bundle.register()  # Idempotent when Blender reuses the loaded module.
                for cls in self.classes:
                    self.assertTrue(cls.is_registered, cls.__name__)
                    if issubclass(cls, bpy.types.Panel):
                        self.assertEqual(cls.bl_category, "Helix Tools")
                for identifier in PUBLIC_OPERATORS:
                    namespace, name = identifier.split(".", 1)
                    self.assertIsNotNone(getattr(getattr(bpy.ops, namespace), name).get_rna_type())
                addon_utils.disable(self.bundle.__name__, default_set=True)
                self.bundle.unregister()
                self.assert_clean()

    def test_failure_after_five_components_and_partial_hair_registration_rolls_back(self):
        import _bpy_restrict_state

        hair = self.bundle.hair_contact_culler
        register_class = bpy.utils.register_class
        hair_calls = []

        def fail_second_hair_class(cls):
            if cls.__module__ == hair.__name__:
                self.assertFalse(hasattr(bpy.data, "objects"), "Enablement must use RestrictBlend")
                hair_calls.append(cls)
                if len(hair_calls) == 2:
                    raise RuntimeError("Injected partial hair registration failure")
            return register_class(cls)

        # The wheel may retain a bootstrap alias; use its real RestrictBlend on
        # the same public bpy module as the full executable and all components.
        with mock.patch.object(_bpy_restrict_state, "_bpy", bpy), \
                mock.patch.object(bpy.utils, "register_class", side_effect=fail_second_hair_class):
            with self.assertRaisesRegex(RuntimeError, "could not enable release extension") as failed:
                enable_extension(self.prefix, BUNDLE_ID)
        self.assertIn("Injected partial hair registration failure", str(failed.exception.__cause__))
        self.assertEqual(len(hair_calls), 2)
        self.assertFalse(addon_utils.check(self.bundle.__name__)[1])
        self.bundle.unregister()
        self.assert_clean()
        self.activate_bundle()
        self.assertTrue(all(cls.is_registered for cls in self.classes))

    def test_each_standalone_conflict_preserves_existing_classes_and_property_definitions(self):
        for standalone in self.standalones:
            with self.subTest(standalone=standalone.__name__):
                standalone.register()
                try:
                    existing_properties = _properties()
                    existing_definitions = _property_definitions()
                    existing_handlers = _handlers()
                    original_classes = _module_classes(standalone)
                    with self.assertRaisesRegex(RuntimeError, "Disable their standalone"):
                        self.bundle.register()
                    self.bundle.unregister()
                    self.assertEqual(_properties(), existing_properties)
                    self.assertEqual(_handlers(), existing_handlers)
                    for owner, names in existing_properties.items():
                        for name in names - self.baseline_properties[owner]:
                            self.assertIs(owner.__dict__[name], existing_definitions[owner][name])
                    self.assertTrue(all(cls.is_registered for cls in original_classes))
                    self.assertFalse(any(cls.is_registered for cls in self.classes))
                finally:
                    standalone.unregister()
                self.assert_clean()

    def test_standalone_enable_attempts_preserve_an_already_enabled_suite(self):
        self.activate_bundle()
        definitions = _property_definitions()
        registered_properties = _properties()
        registered_handlers = _handlers()
        for standalone in self.standalones:
            with self.subTest(standalone=standalone.__name__):
                with self.assertRaises(RuntimeError):
                    standalone.register()
                standalone.unregister()
                self.assertEqual(_properties(), registered_properties)
                self.assertEqual(_handlers(), registered_handlers)
                for owner, names in registered_properties.items():
                    for name in names - self.baseline_properties[owner]:
                        self.assertIs(owner.__dict__[name], definitions[owner][name])
                self.assertTrue(all(cls.is_registered for cls in self.classes))

    def test_cleanup_continues_after_component_failure_and_can_be_retried(self):
        self.bundle.register()
        cloth = self.bundle.cloth_cache_manager
        with mock.patch.object(cloth, "unregister", side_effect=RuntimeError("Injected cleanup failure")):
            with self.assertRaisesRegex(RuntimeError, "cleanup is incomplete"):
                self.bundle.unregister()
            for module in self.bundle.modules:
                self.assertEqual(any(cls.is_registered for cls in _module_classes(module)), module is cloth)
            with self.assertRaisesRegex(RuntimeError, "unfinished cleanup"):
                self.bundle.register()
        self.bundle.unregister()
        self.assert_clean()

    def test_saved_standalone_scene_reopens_with_bundle_and_retains_owned_data(self):
        self.enable_standalones()
        scene = self.scene
        scene.frame_start = scene.frame_end = 1
        scene.render.fps = 30
        scene.jbt_offset, scene.jbt_seconds, scene.jbt_frames = 145, 2, 5
        scene.jbt_use_scene_fps = False
        scene.jbt_fps_override = 29.97
        scene.camera_cull_settings.tolerance = 0.0
        scene.camera_cull_settings.substeps = 3
        bpy.ops.object.camera_add()
        scene.camera = bpy.context.object
        bpy.ops.mesh.primitive_cube_add(size=1, location=(100, 0, -5))
        outside = bpy.context.object
        outside.name = "Bundle Outside"
        original_collection_names = {collection.name for collection in outside.users_collection}
        self.assertEqual(bpy.ops.object.camera_cull_timeline(), {"FINISHED"})
        self.assertIsNotNone(scene.camera_cull_settings.collection)

        bpy.ops.object.empty_add(location=(2, 3, 4))
        source = bpy.context.object
        source.name = "Bundle Anchor Source"
        scene.helix_smart_empty_settings.empty_size = 0.75
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {"FINISHED"})
        anchor = bpy.context.object
        anchor.name = "Bundle Anchor"
        self.assertTrue(anchor.helix_smart_empty.is_tracked)

        light = bpy.data.lights.new("Bundle Light", "AREA")
        light.shape = "RECTANGLE"
        light.size, light.size_y = 2, 4
        scene.collection.objects.link(bpy.data.objects.new("Bundle Light", light))
        scene.area_light_shadow_control.size_reduction = 1.0
        lighting = next(module for module in self.standalones if module.__name__ == "area_light_shadow_control")
        baseline_key = lighting.BASELINE_KEY
        render_key = lighting.RENDER_BASELINE_KEY
        self.assertIn(baseline_key, light)
        scene.render.engine = "CYCLES"
        self.assertEqual(bpy.ops.alsc.eevee_scene_quality(), {"FINISHED"})
        self.assertIn(render_key, scene)

        hair = next(module for module in self.standalones if module.__name__ == "hair_contact_culler")
        groom, garment = make_hair("Bundle Hair"), make_garment("Bundle Garment")
        select_objects(groom, garment)
        metadata = hair.create_bundle(groom, [garment])
        metadata.name = "Bundle Hair Settings"
        hair.compile_bundle(metadata)
        metadata.helix_hair_cull.mode = "LOW"
        metadata.helix_hair_cull.low_percent = 37
        garment.cloth_tool_selected = False
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2)
        cloth = bpy.context.object
        cloth.name = "Bundle Cloth"
        cloth.modifiers.new("Bundle Cloth Simulation", "CLOTH")
        cloth.cloth_tool_selected = True

        with tempfile.TemporaryDirectory(prefix="helix-bundle-scene-") as directory:
            path = str(Path(directory) / "standalone-scene.blend")
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {"FINISHED"})
            self.disable_standalones()
            self.activate_bundle()
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {"FINISHED"})

        scene = bpy.context.scene
        self.assertEqual(scene.jbt_offset, 145)
        self.assertAlmostEqual(scene.jbt_fps_override, 29.97, places=4)
        self.assertEqual(bpy.ops.jbt.jump_to_time(), {"FINISHED"})
        self.assertEqual(scene.frame_current, 210)
        self.assertAlmostEqual(scene.helix_smart_empty_settings.empty_size, 0.75)
        anchor = bpy.data.objects["Bundle Anchor"]
        self.assertTrue(anchor.helix_smart_empty.is_tracked)
        self.assertEqual(anchor.helix_smart_empty.source_object, bpy.data.objects["Bundle Anchor Source"])
        self.assertEqual(anchor.helix_smart_empty.owner_scene, scene)
        self.assertAlmostEqual(scene.camera_cull_settings.tolerance, 0.0)
        self.assertEqual(scene.camera_cull_settings.substeps, 3)
        self.assertEqual(scene.camera_cull_settings.collection.get(self.bundle.camera_timeline_culler.OWNER), scene)
        self.assertEqual(bpy.ops.object.camera_cull_restore(), {"FINISHED"})
        self.assertEqual({collection.name for collection in bpy.data.objects["Bundle Outside"].users_collection},
                         original_collection_names)
        light = bpy.data.lights["Bundle Light"]
        self.assertIn(baseline_key, light)
        self.assertAlmostEqual(light.size, 1.0)
        self.assertAlmostEqual(scene.area_light_shadow_control.size_reduction, 1.0)
        self.assertEqual(bpy.ops.alsc.restore_sizes(), {"FINISHED"})
        self.assertAlmostEqual(light.size, 2.0)
        self.assertAlmostEqual(light.size_y, 4.0)
        self.assertIn(render_key, scene)
        self.assertEqual(bpy.ops.alsc.restore_render_settings(), {"FINISHED"})
        self.assertEqual(scene.render.engine, "CYCLES")
        settings = bpy.data.objects["Bundle Hair Settings"].helix_hair_cull
        self.assertEqual(settings.source, bpy.data.objects["Bundle Hair"])
        self.assertEqual(settings.items[0].obj, bpy.data.objects["Bundle Garment"])
        self.assertEqual(settings.mode, "LOW")
        self.assertEqual(settings.low_percent, 37)
        self.bundle.hair_contact_culler.sync(settings.id_data, deep=True)
        self.assertTrue(settings.valid, settings.status)
        settings.mode = "FULL"
        self.assertEqual(len(evaluated_data(settings.source).points), 3)
        self.assertEqual(len(settings.source.data.points), 4)
        self.assertTrue(bpy.data.objects["Bundle Cloth"].cloth_tool_selected)
        self.assertFalse(bpy.data.objects["Bundle Garment"].cloth_tool_selected)
        self.assertEqual(bpy.data.objects["Bundle Cloth"].modifiers[0].type, "CLOTH")


if __name__ == "__main__":
    unittest.main()
