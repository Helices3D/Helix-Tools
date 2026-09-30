"""Native hair trimming and workflow regressions in a disposable Blender process."""

import unittest

import bpy
import hair_contact_culler as hair

from _hair_fixtures import evaluated_data, make_garment, make_hair, positions, select_objects


class HairRenderProbe(bpy.types.RenderEngine):
    """Inspect actual render geometry without an expensive or device-specific render."""
    bl_idname = "HELIX_TEST_HAIR_RENDER"
    bl_label = "Helix Test Hair Render"
    bl_use_preview = False
    source = None
    captured = []

    def render(self, depsgraph):
        data = self.source.evaluated_get(depsgraph).data
        self.captured.append((depsgraph.mode, len(data.curves), len(data.points)))
        result = self.begin_result(0, 0, 4, 4)
        self.end_result(result)


class HairCullingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hair.register()

    @classmethod
    def tearDownClass(cls):
        hair.unregister()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def build(self, count=1):
        source, garment = make_hair(count=count), make_garment()
        self.source_baseline = (positions(source.data),
                                [point.value for point in source.data.attributes["radius"].data],
                                source.data)
        select_objects(source, garment)  # A garment may be active; selection order is unrestricted.
        self.assertEqual(bpy.ops.helix.hair_cull_build(), {"FINISHED"})
        meta = hair.find_bundle(source)
        self.assertIsNotNone(meta)
        return source, garment, meta

    def assertPoints(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for point, target in zip(actual, expected):
            for coordinate, value in zip(point, target):
                self.assertAlmostEqual(coordinate, value, places=5)

    def test_root_prefix_source_preservation_and_restore(self):
        source, garment, meta = self.build()
        original, original_radii, source_data = self.source_baseline
        settings = meta.helix_hair_cull
        endpoint = 1.5 - 0.1 - settings.allowance
        data = evaluated_data(source)
        self.assertEqual(len(data.curves), 1)
        self.assertPoints(positions(data), [(0, 0, 0), (1, 1, 0), (endpoint, 2 - endpoint, 0)])
        self.assertAlmostEqual(data.attributes["radius"].data[-1].value, 0.1, places=6)
        self.assertPoints(positions(source.data), original)
        self.assertEqual([p.value for p in source.data.attributes["radius"].data], original_radii)
        self.assertIs(source.data, source_data)

        # Reversing the root keeps only the contiguous portion toward the last point.
        settings.root_mode = "LAST"
        self.assertFalse(settings.valid)
        self.assertEqual(bpy.ops.helix.hair_cull_build(), {"FINISHED"})
        endpoint = 1.5 + 0.1 + settings.allowance
        self.assertPoints(positions(evaluated_data(source)), [(endpoint, 2 - endpoint, 0), (2, 0, 0), (3, 1, 0)])
        prefix, metadata_name, group_name = settings.prefix, meta.name, settings.group.name
        self.assertEqual(bpy.ops.helix.hair_cull_remove(), {"FINISHED"})
        self.assertPoints(positions(evaluated_data(source)), original)
        self.assertEqual([p.value for p in source.data.attributes["radius"].data], original_radii)
        self.assertFalse(any(attr.name.startswith(prefix) for attr in source.data.attributes))
        self.assertNotIn(metadata_name, bpy.data.objects)
        self.assertNotIn(group_name, bpy.data.node_groups)
        self.assertFalse(source.modifiers)
        self.assertFalse(garment.hide_viewport or garment.hide_render)

    def test_original_and_viewport_modes_preserve_full_render_detail(self):
        source, garment, meta = self.build(count=10)
        settings = meta.helix_hair_cull
        camera = bpy.data.objects.new("Test Camera", bpy.data.cameras.new("Test Camera"))
        scene = bpy.context.scene
        scene.collection.objects.link(camera)
        scene.camera = camera
        scene.render.resolution_x = scene.render.resolution_y = 4
        scene.render.resolution_percentage = 100
        original_engine = scene.render.engine
        HairRenderProbe.source = source
        bpy.utils.register_class(HairRenderProbe)
        try:
            scene.render.engine = HairRenderProbe.bl_idname
            for mode in ("FULL", "LOW", "OFF"):
                settings.mode = mode
                points = len(evaluated_data(source).points)
                if mode == "FULL":
                    self.assertEqual(points, 30)
                elif mode == "LOW":
                    self.assertGreater(points, 0)
                    self.assertLess(points, 30)
                else:
                    self.assertEqual(points, 0)
                HairRenderProbe.captured.clear()
                self.assertEqual(bpy.ops.render.render(), {"FINISHED"})
                self.assertEqual(HairRenderProbe.captured, [("RENDER", 10, 30)])
            settings.preview_original = True
            self.assertEqual(settings.mode, "OFF")
            self.assertEqual(len(evaluated_data(source).points), 40)
            HairRenderProbe.captured.clear()
            self.assertEqual(bpy.ops.render.render(), {"FINISHED"})
            self.assertEqual(HairRenderProbe.captured, [("RENDER", 10, 30)])
            settings.items[0].render = False
            HairRenderProbe.captured.clear()
            self.assertEqual(bpy.ops.render.render(), {"FINISHED"})
            self.assertEqual(HairRenderProbe.captured, [("RENDER", 10, 40)])
        finally:
            scene.render.engine = original_engine
            bpy.utils.unregister_class(HairRenderProbe)
            HairRenderProbe.source = None

    def test_garment_list_invalidation_and_rebuild_uses_list(self):
        source, garment, meta = self.build()
        settings = meta.helix_hair_cull
        early = make_garment("Earlier Contact", x=0.5)
        select_objects(source, early)
        self.assertEqual(bpy.ops.helix.hair_cull_add_items(), {"FINISHED"})
        self.assertEqual([item.obj for item in settings.items], [garment, early])
        self.assertFalse(settings.valid)
        self.assertEqual(len(evaluated_data(source).points), 4)
        self.assertEqual(bpy.ops.helix.hair_cull_build(), {"FINISHED"})
        endpoint = 0.5 - 0.1 - settings.allowance
        self.assertPoints(positions(evaluated_data(source)), [(0, 0, 0), (endpoint, endpoint, 0)])
        self.assertEqual(bpy.ops.helix.hair_cull_remove_item(index=1), {"FINISHED"})
        self.assertFalse(settings.valid)
        self.assertEqual(len(evaluated_data(source).points), 4)
        # The removed item remains selected; rebuilding must use the saved list.
        self.assertEqual(bpy.ops.helix.hair_cull_build(), {"FINISHED"})
        self.assertEqual([item.obj for item in settings.items], [garment])
        self.assertEqual(len(evaluated_data(source).points), 3)
        source.data.attributes["position"].data[1].vector = (1, 2, 0)
        source.data.update_tag()
        hair.sync(meta, deep=True)
        self.assertFalse(settings.valid)
        self.assertEqual(len(evaluated_data(source).points), 4)
        self.assertEqual(bpy.ops.helix.hair_cull_build(), {"FINISHED"})
        endpoint = 1.5 - 0.1 - settings.allowance
        self.assertPoints(positions(evaluated_data(source)), [(0, 0, 0), (1, 2, 0), (endpoint, 4 - 2 * endpoint, 0)])
        self.assertFalse(early.hide_viewport or early.hide_render)


if __name__ == "__main__":
    unittest.main()
