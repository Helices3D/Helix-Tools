"""Cross-add-on checks run against the supported Blender runtime.

The test runner adds the standalone packages under ``addons`` to ``sys.path``.
Use a separate Blender process: saving and reopening a scene replaces its data.
"""

import importlib
import tempfile
import unittest
from pathlib import Path

import bpy

from _hair_fixtures import make_garment, make_hair


MODULE_NAMES = (
    "jump_by_time",
    "smart_empty",
    "camera_timeline_culler",
    "area_light_shadow_control",
    "hair_contact_culler",
)

# Preserve existing operator names and verify the added hair tool's public API.
PUBLIC_OPERATORS = (
    "jbt.jump_to_time",
    "object.add_smart_empty_baked",
    "object.camera_cull_timeline",
    "object.camera_cull_toggle",
    "object.camera_cull_restore",
    "alsc.collection_add",
    "alsc.collection_remove",
    "alsc.capture_sizes",
    "alsc.restore_sizes",
    "alsc.refresh_sizes",
    "alsc.apply_shadows",
    "alsc.eevee_scene_quality",
    "alsc.status_details",
    "helix.hair_cull_build",
    "helix.hair_cull_remove",
    "helix.hair_cull_validate",
    "helix.hair_cull_add_items",
    "helix.hair_cull_remove_item",
    "helix.hair_cull_help",
)

PROPERTY_OWNERS = (
    bpy.types.Scene,
    bpy.types.Object,
    bpy.types.Armature,
    bpy.types.Collection,
    bpy.types.WindowManager,
    bpy.types.Light,
)


def _properties():
    return {
        owner: frozenset(owner.bl_rna.properties.keys())
        for owner in PROPERTY_OWNERS
    }


def _module_classes(module):
    """Include every locally declared Blender class, regardless of tuple name."""
    bases = (bpy.types.Operator, bpy.types.Panel, bpy.types.PropertyGroup,
             bpy.types.UIList, bpy.types.AddonPreferences)
    return tuple(
        cls for cls in vars(module).values()
        if isinstance(cls, type)
        and cls.__module__ == module.__name__
        and issubclass(cls, bases)
    )


class StandaloneIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline_properties = _properties()
        cls.modules = tuple(importlib.import_module(name) for name in MODULE_NAMES)
        cls.classes = tuple(
            registered_class
            for module in cls.modules
            for registered_class in _module_classes(module)
        )
        cls.enabled = []
        try:
            cls._enable()
        except Exception:
            cls._disable()
            raise

    @classmethod
    def _enable(cls):
        for module in cls.modules:
            module.register()
            cls.enabled.append(module)

    @classmethod
    def _disable(cls):
        while cls.enabled:
            cls.enabled.pop().unregister()

    @classmethod
    def tearDownClass(cls):
        cls._disable()

    def test_supported_version(self):
        self.assertEqual(bpy.app.version, (5, 2, 2))

    def test_public_operator_names_work_together(self):
        for identifier in PUBLIC_OPERATORS:
            namespace, name = identifier.split(".", 1)
            operator = getattr(getattr(bpy.ops, namespace), name)
            with self.subTest(operator=identifier):
                self.assertIsNotNone(operator.get_rna_type())

    def test_viewport_panels_share_a_clear_sidebar(self):
        for module in self.modules:
            panels = tuple(
                cls for cls in _module_classes(module)
                if issubclass(cls, bpy.types.Panel)
                and getattr(cls, "bl_space_type", None) == "VIEW_3D"
            )
            with self.subTest(addon=module.__name__):
                self.assertTrue(panels, "Each add-on should expose a viewport panel")
            for panel in panels:
                with self.subTest(panel=panel.__name__):
                    self.assertEqual(panel.bl_category, "Helix Tools")
                    self.assertEqual(panel.bl_region_type, "UI")
                    self.assertTrue(panel.bl_label.strip())
                    self.assertTrue(panel.is_registered)

    def test_disable_enable_cycles_do_not_leave_registered_state(self):
        registered_properties = _properties()
        self.assertNotEqual(registered_properties, self.baseline_properties)
        for cycle in range(2):
            with self.subTest(cycle=cycle):
                self._disable()
                try:
                    self.assertEqual(_properties(), self.baseline_properties)
                    for cls in self.classes:
                        self.assertFalse(cls.is_registered, cls.__name__)
                    for identifier in PUBLIC_OPERATORS:
                        namespace, name = identifier.split(".", 1)
                        operator = getattr(getattr(bpy.ops, namespace), name)
                        with self.assertRaises(KeyError, msg=identifier):
                            operator.get_rna_type()
                    for name in dir(bpy.app.handlers):
                        callbacks = getattr(bpy.app.handlers, name)
                        if isinstance(callbacks, list):
                            for callback in callbacks:
                                self.assertNotIn(
                                    getattr(callback, "__module__", "").split(".")[0],
                                    MODULE_NAMES,
                                    f"Handler leaked after disabling: {name}",
                                )
                    for module in self.modules:
                        tick = getattr(module, "_tick", None)
                        if tick is not None:
                            self.assertFalse(bpy.app.timers.is_registered(tick))
                finally:
                    self._enable()
                self.assertEqual(_properties(), registered_properties)
                for cls in self.classes:
                    self.assertTrue(cls.is_registered, cls.__name__)

    def test_all_settings_survive_a_blend_save_and_reopen(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.fps = 30
        scene.render.fps_base = 1.0
        scene.jbt_offset = 17
        scene.jbt_seconds = 2
        scene.jbt_frames = 5
        scene.jbt_input_mode = "SECONDS_FRAMES"
        scene.jbt_use_scene_fps = True
        scene.jbt_fps_override = 29.97
        scene.jbt_timestamp = "00:02:03.456"
        scene.helix_smart_empty_settings.empty_size = 0.75
        scene.helix_smart_empty_settings.use_local_orientation = False
        scene.camera_cull_settings.tolerance = 7.5
        scene.camera_cull_settings.substeps = 3
        scene.camera_cull_settings.scope = "PREVIEW"
        scene.area_light_shadow_control.size_light_types = "SPOT"
        scene.area_light_shadow_control.scope = "COLLECTIONS"
        # Changing the light scope restores its previous targets and resets
        # their reduction. Choose the target scope before setting a reduction.
        scene.area_light_shadow_control.size_reduction = 0.25

        # Save a real tracked target and ensure object references restore as
        # Blender pointers, together with the independent scene settings.
        bpy.ops.object.empty_add(type="PLAIN_AXES")
        target = bpy.context.active_object
        target.name = "Integration Target"
        target.location = (2.0, 3.0, 4.0)
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {"FINISHED"})
        empty = bpy.context.active_object
        empty.name = "Integration Smart Empty"
        self.assertTrue(empty.helix_smart_empty.is_tracked)
        self.assertEqual(empty.helix_smart_empty.source_object, target)

        hair = self.modules[-1]
        source = make_hair("Integration Hair")
        garment = make_garment("Integration Garment")
        metadata = hair.create_bundle(source, [garment])
        metadata.name = "Integration Hair Settings"
        settings = metadata.helix_hair_cull
        settings.root_mode = "LAST"
        hair.compile_bundle(metadata)
        settings.mode = "LOW"
        settings.low_percent = 37
        settings.preview_original = True
        settings.items[0].render = False

        with tempfile.TemporaryDirectory(prefix="helix-integration-") as directory:
            path = str(Path(directory) / "all-addons.blend")
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {"FINISHED"})
            scene.jbt_offset = -99
            scene.helix_smart_empty_settings.empty_size = 2.0
            scene.camera_cull_settings.tolerance = 99.0
            scene.area_light_shadow_control.size_reduction = 0.0
            settings.mode = "OFF"
            settings.preview_original = False
            settings.low_percent = 1
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {"FINISHED"})

        scene = bpy.context.scene
        self.assertEqual(scene.jbt_offset, 17)
        self.assertEqual(scene.jbt_seconds, 2)
        self.assertEqual(scene.jbt_frames, 5)
        self.assertEqual(scene.jbt_input_mode, "SECONDS_FRAMES")
        self.assertTrue(scene.jbt_use_scene_fps)
        self.assertAlmostEqual(scene.jbt_fps_override, 29.97, places=4)
        self.assertEqual(scene.jbt_timestamp, "00:02:03.456")
        self.assertAlmostEqual(scene.helix_smart_empty_settings.empty_size, 0.75)
        self.assertFalse(scene.helix_smart_empty_settings.use_local_orientation)
        self.assertAlmostEqual(scene.camera_cull_settings.tolerance, 7.5)
        self.assertEqual(scene.camera_cull_settings.substeps, 3)
        self.assertEqual(scene.camera_cull_settings.scope, "PREVIEW")
        self.assertEqual(scene.area_light_shadow_control.size_light_types, "SPOT")
        self.assertAlmostEqual(scene.area_light_shadow_control.size_reduction, 0.25)
        self.assertEqual(scene.area_light_shadow_control.scope, "COLLECTIONS")
        empty = bpy.data.objects["Integration Smart Empty"]
        self.assertTrue(empty.helix_smart_empty.is_tracked)
        self.assertEqual(empty.helix_smart_empty.source_object,
                         bpy.data.objects["Integration Target"])
        self.assertEqual(empty.helix_smart_empty.owner_scene, scene)
        self.assertEqual(bpy.ops.jbt.jump_to_time(), {"FINISHED"})
        self.assertEqual(scene.frame_current, 82)
        metadata = bpy.data.objects["Integration Hair Settings"]
        settings = metadata.helix_hair_cull
        self.assertEqual(settings.source, bpy.data.objects["Integration Hair"])
        self.assertEqual(settings.compiled_source, settings.source)
        self.assertEqual(settings.compiled_data, settings.source.data)
        self.assertEqual(settings.items[0].obj, bpy.data.objects["Integration Garment"])
        self.assertEqual(settings.compiled_items[0].obj, settings.items[0].obj)
        self.assertEqual(settings.root_mode, "LAST")
        self.assertEqual(settings.mode, "LOW")
        self.assertEqual(settings.low_percent, 37)
        self.assertTrue(settings.preview_original)
        self.assertFalse(settings.items[0].render)
        hair.sync(metadata, deep=True)
        self.assertTrue(settings.valid, settings.status)


if __name__ == "__main__":
    unittest.main()
