"""Exercise source sizes, durable baselines and Eevee controls in real Blender."""

from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

import bpy
import area_light_shadow_control as addon


class LightingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        addon.register()
        addon.register()

    @classmethod
    def tearDownClass(cls):
        addon.unregister()
        addon.unregister()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.settings = self.scene.area_light_shadow_control
        self.included = self.collection("Included", self.scene.collection)
        self.nested = self.collection("Nested", self.included)
        self.excluded = self.collection("Excluded", self.scene.collection)

    def collection(self, name, parent):
        collection = bpy.data.collections.new(name)
        parent.children.link(collection)
        return collection

    def light(self, name, kind='AREA', collection=None, x=2.0, y=4.0, data=None):
        if data is None:
            data = bpy.data.lights.new(name, kind)
            if kind == 'AREA':
                data.shape = 'RECTANGLE'
                data.size, data.size_y = x, y
            elif kind in {'POINT', 'SPOT'}:
                data.shadow_soft_size = x
        obj = bpy.data.objects.new(name, data)
        (collection if collection is not None else self.included).objects.link(obj)
        return obj

    def choose_collection(self):
        self.settings.scope = 'COLLECTIONS'
        self.settings.collections.add().collection = self.included

    def assertNear(self, actual, expected):
        self.assertAlmostEqual(actual, expected, delta=max(1e-10, abs(expected) * 1e-5))

    def test_nested_scope_and_noncompounding_stops(self):
        area = self.light("Area")
        nested = self.light("Nested area", collection=self.nested, x=3, y=7)
        excluded = self.light("Excluded area", collection=self.excluded, x=5, y=8)
        self.choose_collection()
        self.settings.size_reduction = 2
        self.assertNear(area.data.size, 0.5)
        self.assertNear(nested.data.size, 0.75)
        self.assertNear(excluded.data.size, 5)
        self.settings.size_reduction = 3
        self.settings.size_reduction = 1
        self.assertNear(area.data.size, 1)
        self.assertNear(area.data.size_y, 2)

    def test_each_source_type_restores_previous_selector(self):
        area = self.light("Area")
        point = self.light("Point", 'POINT')
        spot = self.light("Spot", 'SPOT', collection=self.nested, x=3)
        sun = self.light("Sun", 'SUN')
        sun.data.angle = 0.021
        spot.data.spot_size, spot.data.spot_blend, spot.data.energy = 0.71, 0.31, 321
        self.settings.size_reduction = 2
        self.assertNear(area.data.size, 0.5)
        self.assertNear(point.data.shadow_soft_size, 2)
        self.settings.size_light_types = 'POINT'
        self.assertNear(area.data.size, 2)
        self.assertNear(self.settings.size_reduction, 0)
        self.settings.size_reduction = 3
        self.assertNear(point.data.shadow_soft_size, 0.25)
        self.settings.size_light_types = 'SPOT'
        self.assertNear(point.data.shadow_soft_size, 2)
        self.settings.size_reduction = 2
        self.assertNear(spot.data.shadow_soft_size, 0.75)
        self.settings.size_light_types = 'ALL3'
        self.assertNear(spot.data.shadow_soft_size, 3)
        self.settings.size_reduction = 1
        self.assertNear(area.data.size, 1)
        self.assertNear(point.data.shadow_soft_size, 1)
        self.assertNear(spot.data.shadow_soft_size, 1.5)
        self.assertNear(spot.data.spot_size, 0.71)
        self.assertNear(spot.data.spot_blend, 0.31)
        self.assertNear(spot.data.energy, 321)
        self.assertNear(sun.data.angle, 0.021)

    def test_positive_floors_preserve_area_aspect_and_tiny_baselines(self):
        area = self.light("Tiny ellipse", x=1e-6, y=4e-6)
        area.data.shape = 'ELLIPSE'
        point = self.light("Tiny point", 'POINT', x=1e-6)
        zero = self.light("Zero spot", 'SPOT', x=0)
        self.settings.size_light_types = 'ALL3'
        self.settings.size_reduction = 20
        self.assertNear(area.data.size, self.settings.minimum_size)
        self.assertNear(area.data.size_y / area.data.size, 4)
        self.assertNear(point.data.shadow_soft_size, self.settings.minimum_size)
        self.assertNear(zero.data.shadow_soft_size, self.settings.minimum_size)
        self.assertNear(addon._baseline(area.data)['x'], 1e-6)
        self.assertNear(addon._baseline(point.data)['radius'], 1e-6)
        self.assertEqual(bpy.ops.alsc.restore_sizes(), {'FINISHED'})
        self.assertNear(area.data.size, 1e-6)
        self.assertNear(point.data.shadow_soft_size, 1e-6)
        self.assertGreater(zero.data.shadow_soft_size, 0)

    def test_square_and_disk_use_single_dimension(self):
        for shape in ('SQUARE', 'DISK'):
            with self.subTest(shape=shape):
                area = self.light(shape, x=8, y=1e-6)
                area.data.shape = shape
                self.settings.size_reduction = 2
                bpy.ops.alsc.refresh_sizes()
                self.assertNear(area.data.size, 2)
                self.assertNear(area.data.size_y, 1e-6)

    def test_collection_membership_refresh_restores_excluded_lights(self):
        area = self.light("Moved area")
        nested = self.light("Nested area", collection=self.nested)
        self.choose_collection()
        self.settings.size_reduction = 2
        self.included.objects.unlink(area)
        self.excluded.objects.link(area)
        self.assertEqual(bpy.ops.alsc.refresh_sizes(), {'FINISHED'})
        self.assertNear(area.data.size, 2)
        self.assertNear(nested.data.size, 0.5)
        self.settings.scope = 'ALL'
        self.assertNear(nested.data.size, 2)
        self.assertNear(self.settings.size_reduction, 0)

    def test_capture_uses_current_sizes_without_moving_them(self):
        area = self.light("Area")
        point = self.light("Point", 'POINT')
        self.settings.size_light_types = 'ALL3'
        self.settings.size_reduction = 2
        self.assertEqual(bpy.ops.alsc.capture_sizes(), {'FINISHED'})
        self.assertNear(area.data.size, 0.5)
        self.assertNear(point.data.shadow_soft_size, 0.5)
        self.assertNear(self.settings.size_reduction, 0)
        self.settings.size_reduction = 1
        self.assertNear(area.data.size, 0.25)
        self.assertNear(point.data.shadow_soft_size, 0.25)

    def test_shared_datablocks_isolate_excluded_aliases_and_keep_included_sharing(self):
        self.settings.size_light_types = 'ALL3'
        self.choose_collection()
        for kind in ('AREA', 'POINT', 'SPOT'):
            with self.subTest(kind=kind):
                inside = self.light(f"{kind} inside", kind, x=6, y=12)
                nested = self.light(f"{kind} nested", collection=self.nested, data=inside.data)
                outside = self.light(f"{kind} outside", collection=self.excluded, data=inside.data)
                self.settings.size_reduction = 2
                bpy.ops.alsc.refresh_sizes()
                self.assertEqual(inside.data, nested.data)
                self.assertNotEqual(inside.data, outside.data)
                field = 'size' if kind == 'AREA' else 'shadow_soft_size'
                self.assertNear(getattr(inside.data, field), 1.5)
                self.assertNear(getattr(outside.data, field), 6)

    def test_other_scene_aliases_are_protected_after_initial_scaling(self):
        area = self.light("Area", x=6, y=12)
        self.settings.size_reduction = 2
        other_scene = bpy.data.scenes.new("Other Scene")
        other = bpy.data.objects.new("Other scene alias", area.data)
        other_scene.collection.objects.link(other)
        bpy.ops.alsc.refresh_sizes()
        self.assertNotEqual(area.data, other.data)
        self.assertNear(area.data.size, 1.5)
        self.assertNear(other.data.size, 6)

    def test_shadow_isolation_preserves_restoration_of_scaled_copy(self):
        area = self.light("Scaled area", x=6, y=12)
        self.choose_collection()
        self.settings.size_reduction = 2
        outside = self.light("New excluded alias", collection=self.excluded, data=area.data)
        self.assertEqual(bpy.ops.alsc.apply_shadows(), {'FINISHED'})
        self.assertNotEqual(area.data, outside.data)
        self.assertNear(area.data.size, 1.5)
        self.assertNear(outside.data.size, 6)
        self.settings.scope = 'ALL'
        self.assertNear(area.data.size, 6)
        self.assertNear(outside.data.size, 6)

    def test_legacy_area_baseline_and_corrupt_metadata(self):
        legacy = self.light("Legacy")
        legacy.data[addon.BASELINE_KEY] = {'x': 8, 'y': 12, 'shape': 'RECTANGLE'}
        invalid = self.light("Invalid")
        invalid.data[addon.BASELINE_KEY] = "not a baseline"
        bad_shape = self.light("Bad shape")
        bad_shape.data[addon.BASELINE_KEY] = {'x': 0, 'y': 4, 'shape': 'BOGUS'}
        self.settings.size_reduction = 2
        self.assertNear(legacy.data.size, 2)
        self.assertNear(legacy.data.size_y, 3)
        self.assertNear(invalid.data.size, 0.5)
        self.assertNear(bad_shape.data.size, 0.5)
        self.assertFalse(self.settings.last_errors)

    def test_changed_area_shape_requires_recapture_before_restore(self):
        area = self.light("Changed area")
        self.settings.size_reduction = 2
        area.data.shape = 'DISK'
        area.data.size = 9
        bpy.ops.alsc.refresh_sizes()
        self.assertNear(area.data.size, 9)
        self.assertIn("capture current sizes again", self.settings.last_errors)
        bpy.ops.alsc.restore_sizes()
        self.assertNear(area.data.size, 9)
        self.assertGreater(self.settings.last_skipped, 0)
        bpy.ops.alsc.capture_sizes()
        self.settings.size_reduction = 1
        self.assertNear(area.data.size, 4.5)

    def test_linked_lights_are_skipped_with_explanation(self):
        with tempfile.TemporaryDirectory(prefix="helix-light-linked-") as temp:
            area = self.light("Library Area", x=9)
            fixture = str(Path(temp) / "linked.blend")
            bpy.data.libraries.write(fixture, {area})
            bpy.data.objects.remove(area, do_unlink=True)
            with bpy.data.libraries.load(fixture, link=True) as (_, result):
                result.objects = ["Library Area"]
            linked = result.objects[0]
            self.included.objects.link(linked)
            self.settings.size_reduction = 2
            self.assertNear(linked.data.size, 9)
            self.assertGreaterEqual(self.settings.last_skipped, 1)
            self.assertIn("linked or read-only light", self.settings.last_errors)

    def test_shadow_presets_all_types_and_casting_controls(self):
        lights = [self.light(kind, kind, collection=self.excluded) for kind in ('AREA', 'POINT', 'SPOT', 'SUN')]
        self.choose_collection()
        for index, obj in enumerate(lights):
            obj.data.use_shadow = index % 2 == 0
            obj.data.use_shadow_jitter = False
        self.settings.shadow_preset = 'CRISP'
        bpy.ops.alsc.apply_shadows(every_scene_light=True)
        for index, obj in enumerate(lights):
            self.assertEqual(obj.data.use_shadow, index % 2 == 0)
            self.assertTrue(obj.data.use_shadow_jitter)
            self.assertNear(obj.data.shadow_filter_radius, 0.5)
            self.assertNear(obj.data.shadow_maximum_resolution, 0.0005)
        self.settings.shadow_filter = 0.8
        self.assertEqual(self.settings.shadow_preset, 'CUSTOM')
        self.settings.absolute_resolution = True
        self.settings.shadow_overblur = 7
        self.settings.shadow_jitter = False
        self.settings.shadow_casting = 'ON'
        self.settings.shadow_scope = 'ALL'
        bpy.ops.alsc.apply_shadows()
        for obj in lights:
            self.assertTrue(obj.data.use_shadow)
            self.assertFalse(obj.data.use_shadow_jitter)
            self.assertNear(obj.data.shadow_filter_radius, 0.8)
            self.assertNear(obj.data.shadow_jitter_overblur, 7)
            if obj.data.type != 'SUN':
                self.assertTrue(obj.data.use_absolute_resolution)
        self.settings.shadow_casting = 'OFF'
        bpy.ops.alsc.apply_shadows()
        self.assertTrue(all(not obj.data.use_shadow for obj in lights))

    def test_shadow_scope_is_independent_of_size_type(self):
        area = self.light("Area")
        point = self.light("Point", 'POINT', collection=self.nested)
        sun = self.light("Sun", 'SUN')
        outside = self.light("Outside", collection=self.excluded)
        for obj in (area, point, sun, outside):
            obj.data.shadow_filter_radius = 3
        self.choose_collection()
        self.assertEqual(self.settings.size_light_types, 'AREA')
        bpy.ops.alsc.apply_shadows()
        for obj in (area, point, sun):
            self.assertNear(obj.data.shadow_filter_radius, 1)
        self.assertNear(outside.data.shadow_filter_radius, 3)

    def test_save_reload_and_reenable_preserve_baselines_without_reapplying(self):
        self.light("Area")
        self.light("Point", 'POINT')
        self.light("Spot", 'SPOT')
        self.settings.size_light_types = 'ALL3'
        self.choose_collection()
        self.settings.size_reduction = 2
        baselines = {name: addon._baseline(bpy.data.objects[name].data) for name in ('Area', 'Point', 'Spot')}
        with tempfile.TemporaryDirectory(prefix="helix-light-roundtrip-") as temp:
            path = str(Path(temp) / "roundtrip.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
            self.scene = bpy.context.scene
            self.settings = self.scene.area_light_shadow_control
            self.assertEqual(self.settings.size_light_types, 'ALL3')
            self.assertNear(self.settings.size_reduction, 2)
            for name, baseline in baselines.items():
                data = bpy.data.objects[name].data
                self.assertEqual(addon._baseline(data), baseline)
                self.assertNear(data.size if data.type == 'AREA' else data.shadow_soft_size, 0.5)
            self.settings.size_reduction = 3
            for name in baselines:
                data = bpy.data.objects[name].data
                self.assertNear(data.size if data.type == 'AREA' else data.shadow_soft_size, 0.25)
            addon.unregister()
            addon.register()
            self.settings = self.scene.area_light_shadow_control
            self.assertNear(self.settings.size_reduction, 3)
            self.settings.size_reduction = 1
            for name in baselines:
                data = bpy.data.objects[name].data
                self.assertNear(data.size if data.type == 'AREA' else data.shadow_soft_size, 1)
            self.settings.scope = 'ALL'
            for name in baselines:
                data = bpy.data.objects[name].data
                self.assertNear(data.size if data.type == 'AREA' else data.shadow_soft_size, 2)

    def test_optional_eevee_scene_quality_preset(self):
        self.scene.render.engine = 'CYCLES'
        self.settings.viewport_jitter = False
        self.assertEqual(bpy.ops.alsc.eevee_scene_quality(), {'FINISHED'})
        self.assertEqual(self.scene.render.engine, 'BLENDER_EEVEE')
        self.assertEqual(self.scene.eevee.taa_render_samples, 128)
        self.assertEqual(self.scene.eevee.taa_samples, 64)
        self.assertEqual(self.scene.eevee.shadow_ray_count, 4)
        self.assertEqual(self.scene.eevee.shadow_step_count, 12)
        self.assertEqual(self.scene.eevee.shadow_pool_size, '2048')
        self.assertFalse(self.scene.eevee.use_shadow_jitter_viewport)
        self.assertTrue(self.scene.render.use_high_quality_normals)

    def test_duplicate_addon_registration_cannot_remove_active_copy(self):
        name = "helix_lighting_duplicate_test"
        spec = importlib.util.spec_from_file_location(
            name, addon.__file__, submodule_search_locations=[str(Path(addon.__file__).parent)]
        )
        duplicate = importlib.util.module_from_spec(spec)
        sys.modules[name] = duplicate
        try:
            spec.loader.exec_module(duplicate)
            with self.assertRaisesRegex(RuntimeError, "Disable the older copy"):
                duplicate.register()
            duplicate.unregister()
            self.assertTrue(hasattr(bpy.types.Scene, addon.SCENE_PROPERTY))
            self.assertTrue(addon.ALSC_Settings.is_registered)
            self.light("Still works")
            self.settings.size_reduction = 2
            self.assertNear(bpy.data.objects["Still works"].data.size, 0.5)
        finally:
            for key in tuple(sys.modules):
                if key == name or key.startswith(name + "."):
                    del sys.modules[key]

    def test_foreign_rna_classes_are_not_replaced_during_registration(self):
        class OTHER_OT_refresh_sizes(bpy.types.Operator):
            bl_idname = 'alsc.refresh_sizes'
            bl_label = "Foreign Light Tool"

            def execute(self, context):
                return {'FINISHED'}

        class OTHER_PT_light_control(bpy.types.Panel):
            bl_idname = "VIEW3D_PT_alsc_main"
            bl_label = "Foreign Light Panel"
            bl_space_type = 'VIEW_3D'
            bl_region_type = 'UI'
            bl_category = "Test"

            def draw(self, context):
                pass

        foreign_group = type("ALSC_CollectionItem", (bpy.types.PropertyGroup,), {})

        def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
            pass

        foreign_list = type("ALSC_UL_collections", (bpy.types.UIList,), {
            "draw_item": draw_item,
        })
        fixtures = (
            (OTHER_OT_refresh_sizes, bpy.types.Operator, "ALSC_OT_refresh_sizes"),
            (OTHER_PT_light_control, bpy.types.Panel, "VIEW3D_PT_alsc_main"),
            (foreign_group, bpy.types.PropertyGroup, "ALSC_CollectionItem"),
            (foreign_list, bpy.types.UIList, "ALSC_UL_collections"),
        )
        addon.unregister()
        try:
            for foreign, base, identifier in fixtures:
                with self.subTest(identifier=identifier):
                    bpy.utils.register_class(foreign)
                    try:
                        with self.assertRaisesRegex(RuntimeError, "Disable the conflicting"):
                            addon.register()
                        self.assertFalse(hasattr(bpy.types.Scene, addon.SCENE_PROPERTY))
                        self.assertIs(base.bl_rna_get_subclass_py(identifier, None), foreign)
                        self.assertTrue(foreign.is_registered)
                        addon.unregister()
                        self.assertIs(base.bl_rna_get_subclass_py(identifier, None), foreign)
                        self.assertTrue(foreign.is_registered)
                        if base is bpy.types.Operator:
                            self.assertEqual(bpy.ops.alsc.refresh_sizes(), {'FINISHED'})
                    finally:
                        if foreign.is_registered:
                            bpy.utils.unregister_class(foreign)
        finally:
            addon.register()


if __name__ == "__main__":
    unittest.main()
