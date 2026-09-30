"""Camera culling regressions using Blender's real dependency graph."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import bpy
from bpy_extras.object_utils import world_to_camera_view

import camera_timeline_culler as culler


class CameraCullingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        culler.register()

    @classmethod
    def tearDownClass(cls):
        culler.unregister()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.scene.frame_start, self.scene.frame_end = 1, 5
        self.scene.render.resolution_x = self.scene.render.resolution_y = 1000
        bpy.ops.object.camera_add(location=(0, 0, 0))
        self.camera = bpy.context.object
        self.scene.camera = self.camera

    def cube(self, name, x=0, z=-5, size=1):
        bpy.ops.mesh.primitive_cube_add(size=size, location=(x, 0, z))
        obj = bpy.context.object
        obj.name = name
        return obj

    def scan(self):
        self.assertEqual(bpy.ops.object.camera_cull_timeline(), {"FINISHED"})
        return culler._scene_group(self.scene)

    def test_animated_camera_object_and_comparison_restore(self):
        self.camera.location.x = 0
        self.camera.keyframe_insert(data_path="location", frame=1)
        self.camera.location.x = 10
        self.camera.keyframe_insert(data_path="location", frame=5)
        early, late = self.cube("Early", 0), self.cube("Late", 10)
        never, moving = self.cube("Never", 20), self.cube("Moving", 30)
        for frame, x in ((1, 30), (3, 5), (5, 30)):
            moving.location.x = x
            moving.keyframe_insert(data_path="location", frame=frame)
        self.scene.frame_set(2, subframe=0.25)
        group = self.scan()
        self.assertEqual(set(group.objects), {never})
        self.assertTrue(group.hide_viewport and group.hide_render)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (2, 0.25))
        self.assertEqual(bpy.ops.object.camera_cull_toggle(), {"FINISHED"})
        self.assertFalse(group.hide_viewport or group.hide_render)
        self.assertEqual(bpy.ops.object.camera_cull_toggle(), {"FINISHED"})
        self.assertTrue(group.hide_viewport and group.hide_render)
        self.assertEqual(bpy.ops.object.camera_cull_restore(), {"FINISHED"})
        self.assertIsNone(culler._scene_group(self.scene))
        self.assertEqual(set(self.scene.objects), {self.camera, early, late, never, moving})
        self.assertNotIn(culler.TAG, never)

    def test_margin_recalculation_and_orthographic_bounds(self):
        never, edge = self.cube("Never", 20), self.cube("Edge", -2.5)
        self.scene.camera_cull_settings.tolerance = 0
        self.assertIn(edge, self.scan().objects.values())
        self.scene.camera_cull_settings.tolerance = 10
        group = self.scan()
        self.assertNotIn(edge, group.objects.values())
        self.assertIn(never, group.objects.values())
        bpy.ops.object.camera_cull_restore()
        self.camera.data.type, self.camera.data.ortho_scale = "ORTHO", 4
        self.scene.camera_cull_settings.tolerance = 0
        large = self.cube("Large intersecting box", 0, size=20)
        behind = self.cube("Behind", 0, z=5)
        group = self.scan()
        self.assertNotIn(large, group.objects.values())
        self.assertIn(behind, group.objects.values())

    def test_hidden_flags_memberships_and_renamed_collections(self):
        obj = self.cube("Never", 20)
        original = bpy.data.collections.new("Original")
        extra = bpy.data.collections.new("Extra")
        self.scene.collection.children.link(original)
        self.scene.collection.children.link(extra)
        culler._set_memberships(obj, (original, extra))
        obj.hide_render = True
        group = self.scan()
        self.assertEqual(tuple(obj.users_collection), (group,))
        self.assertTrue(obj.hide_render)
        original.name = "Renamed original"
        self.assertEqual(bpy.ops.object.camera_cull_restore(), {"FINISHED"})
        self.assertEqual(set(obj.users_collection), {original, extra})
        self.assertFalse(obj.hide_viewport)
        self.assertTrue(obj.hide_render)
        hidden = self.cube("Viewport hidden", 40)
        hidden.hide_viewport = True
        group = self.scan()
        self.assertNotIn(hidden, group.objects.values())
        self.assertTrue(hidden.hide_viewport)

    def test_shared_object_and_scene_owned_results(self):
        obj = self.cube("Shared outside", 20)
        unique = self.cube("Unique outside", 25)
        other = bpy.data.scenes.new("Other scene")
        other.collection.objects.link(obj)
        group_a = self.scan()
        self.assertEqual(set(group_a.objects), {unique})
        self.assertIn(obj, other.objects.values())
        other.camera = self.camera
        other.collection.objects.link(self.camera)
        other.frame_start = other.frame_end = 1
        bpy.context.window.scene = other
        bpy.ops.mesh.primitive_cube_add(location=(30, 0, -5))
        other_unique = bpy.context.object
        self.assertEqual(bpy.ops.object.camera_cull_timeline(), {"FINISHED"})
        group_b = culler._scene_group(other)
        self.assertIsNot(group_a, group_b)
        self.assertEqual(set(group_b.objects), {other_unique})
        self.assertEqual(bpy.ops.object.camera_cull_toggle(), {"FINISHED"})
        self.assertTrue(group_a.hide_viewport)
        self.assertFalse(group_b.hide_viewport)
        bpy.ops.object.camera_cull_restore()
        self.assertTrue(group_a.hide_render)
        bpy.context.window.scene = self.scene
        bpy.ops.object.camera_cull_restore()
        self.assertIn(obj, other.objects.values())
        self.assertIn(unique, self.scene.objects.values())

    def test_shared_result_collection_is_protected(self):
        obj = self.cube("Never", 30)
        group = self.scan()
        other = bpy.data.scenes.new("Another scene")
        other.collection.children.link(group)
        with self.assertRaisesRegex(RuntimeError, "shared with another scene"):
            bpy.ops.object.camera_cull_toggle()
        self.assertTrue(group.hide_render and group.hide_viewport)
        self.assertEqual(tuple(obj.users_collection), (group,))

    def test_render_only_modifier_and_nested_instances_retained(self):
        render_only = self.cube("Render only geometry", 30)
        modifier = render_only.modifiers.new("Render subdivision", "SUBSURF")
        modifier.show_viewport = False
        modifier.show_render = True
        nested_obj = self.cube("Nested instances", 40)
        outer = bpy.data.node_groups.new("Outer geometry", "GeometryNodeTree")
        inner = bpy.data.node_groups.new("Inner geometry", "GeometryNodeTree")
        outer.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        inner.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        inner.nodes.new("GeometryNodeInstanceOnPoints")
        node = outer.nodes.new("GeometryNodeGroup")
        node.node_tree = inner
        outer.nodes.new("NodeGroupOutput")
        inner.nodes.new("NodeGroupOutput")
        nested_obj.modifiers.new("Nodes", "NODES").node_group = outer
        instance = self.cube("Instance source", 50)
        instance.instance_type = "VERTS"
        known_outside = self.cube("Known outside", 60)
        group = self.scan()
        self.assertEqual(set(group.objects), {known_outside})
        self.assertTrue(culler._has_unbounded_instances(nested_obj))

    def test_collection_instance_sources_keep_original_membership(self):
        source = self.cube("Outside source", 100)
        collection = bpy.data.collections.new("Instance source")
        self.scene.collection.children.link(collection)
        culler._set_memberships(source, (collection,))
        instance = bpy.data.objects.new("Visible instance", None)
        instance.instance_type = "COLLECTION"
        instance.instance_collection = collection
        instance.location = (-100, 0, 0)
        self.scene.collection.objects.link(instance)
        never = self.cube("Never visible", 50)
        group = self.scan()
        self.assertEqual(set(group.objects), {never})
        self.assertEqual(tuple(source.users_collection), (collection,))
        instances = [entry.matrix_world.copy() for entry in bpy.context.evaluated_depsgraph_get().object_instances
                     if entry.is_instance and entry.object.original == source]
        self.assertEqual(len(instances), 1)
        self.assertAlmostEqual(instances[0].translation.x, 0)

    def test_nested_geometry_nodes_source_is_not_moved(self):
        source = self.cube("Node source", 100)
        collection = bpy.data.collections.new("Node source collection")
        self.scene.collection.children.link(collection)
        culler._set_memberships(source, (collection,))
        driven = self.cube("Node output", 0)
        direct_source = self.cube("Object Info source", 200)
        outer = bpy.data.node_groups.new("Outer geometry", "GeometryNodeTree")
        inner = bpy.data.node_groups.new("Nested sources", "GeometryNodeTree")
        outer.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        inner.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        collection_node = inner.nodes.new("GeometryNodeCollectionInfo")
        collection_node.inputs["Collection"].default_value = collection
        inner.nodes.new("GeometryNodeObjectInfo").inputs["Object"].default_value = direct_source
        inner_output = inner.nodes.new("NodeGroupOutput")
        inner.links.new(collection_node.outputs["Instances"], inner_output.inputs["Geometry"])
        nested_node = outer.nodes.new("GeometryNodeGroup")
        nested_node.node_tree = inner
        outer_output = outer.nodes.new("NodeGroupOutput")
        outer.links.new(nested_node.outputs["Geometry"], outer_output.inputs["Geometry"])
        driven.modifiers.new("Nodes", "NODES").node_group = outer
        group = self.scan()
        self.assertNotIn(source, group.objects.values())
        self.assertNotIn(direct_source, group.objects.values())
        self.assertEqual(tuple(source.users_collection), (collection,))

    def test_legacy_results_migrate_without_losing_original_membership(self):
        obj = self.cube("Legacy source", 100)
        original = bpy.data.collections.new("Legacy original")
        self.scene.collection.children.link(original)
        group = bpy.data.collections.new(culler.COLLECTION_NAME)
        self.scene.collection.children.link(group)
        culler._set_memberships(obj, (group,))
        obj[culler.TAG] = self.camera.name
        obj[culler.PREV_COLLECTIONS] = '["Legacy original"]'
        obj[culler.PREV_VIEWPORT] = False
        obj[culler.PREV_RENDER] = False
        group.hide_render = group.hide_viewport = True
        self.assertEqual(self.scan(), group)
        self.assertEqual(group[culler.OWNER], self.scene)
        original.name = "Renamed legacy original"
        bpy.ops.object.camera_cull_restore()
        self.assertEqual(tuple(obj.users_collection), (original,))

    def test_boolean_collection_sources_are_not_moved(self):
        source = self.cube("Boolean source", 100)
        collection = bpy.data.collections.new("Boolean operands")
        self.scene.collection.children.link(collection)
        culler._set_memberships(source, (collection,))
        target = self.cube("Visible Boolean result", 0)
        modifier = target.modifiers.new("Collection difference", "BOOLEAN")
        modifier.operand_type = "COLLECTION"
        modifier.collection = collection
        group = self.scan()
        self.assertNotIn(source, group.objects.values())
        self.assertEqual(tuple(source.users_collection), (collection,))

    def test_camera_scale_matches_blender_projection_and_clipping(self):
        self.camera.location = (0, 0, 10)
        self.camera.data.clip_end = 20
        obj = self.cube("Visible with scaled camera", 2, z=0)
        for scale in ((0.1, 1, 1), (0.1, 0.1, 0.1), (10, 2, 4)):
            self.camera.scale = scale
            self.scene.frame_set(1)
            point = world_to_camera_view(self.scene, self.camera, obj.matrix_world.translation)
            self.assertTrue(0 < point.x < 1 and 0 < point.y < 1)
            self.assertLess(point.z, self.camera.data.clip_end)
            self.assertNotIn(obj, self.scan().objects.values())

    def test_failed_apply_rolls_back_tags_memberships_and_collection(self):
        obj = self.cube("Outside", 40)
        self.scene.frame_set(2, subframe=0.5)
        before = culler._snapshot(obj)
        original_set_memberships = culler._set_memberships

        def fail_cull(target, collections):
            if len(collections) == 1 and collections[0].name.startswith(culler.COLLECTION_NAME):
                raise ValueError("Injected collection failure")
            return original_set_memberships(target, collections)

        with mock.patch.object(culler, "_set_memberships", side_effect=fail_cull):
            with self.assertRaisesRegex(RuntimeError, "Injected collection failure"):
                bpy.ops.object.camera_cull_timeline()
        self.assertEqual(culler._snapshot(obj), before)
        self.assertIsNone(culler._scene_group(self.scene))
        self.assertNotIn(culler.COLLECTION_NAME, bpy.data.collections)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (2, 0.5))

    def test_camera_cuts_preview_range_and_panorama(self):
        early, late = self.cube("Early", 0), self.cube("Late", 20)
        bpy.ops.object.camera_add(location=(20, 0, 0))
        cut_camera = bpy.context.object
        for frame, camera in ((1, self.camera), (4, cut_camera)):
            self.scene.timeline_markers.new(f"Camera at {frame}", frame=frame).camera = camera
        self.scene.frame_set(2, subframe=0.5)
        original_camera = self.scene.camera
        self.assertEqual(set(self.scan().objects), set())
        self.assertEqual(self.scene.camera, original_camera)
        self.scene.camera_cull_settings.scope = "PREVIEW"
        self.scene.frame_preview_start, self.scene.frame_preview_end = 1, 3
        group = self.scan()
        self.assertEqual(set(group.objects), {late})
        self.assertEqual(self.scene["camera_cull_last_range"], "1-3")
        self.camera.data.type = "PANO"
        self.assertEqual(set(self.scan().objects), set())
        self.assertIn(early, self.scene.objects.values())

    def test_failed_scan_restores_existing_results_and_exact_frame(self):
        obj = self.cube("Outside", 20)
        group = self.scan()
        bpy.ops.object.camera_cull_toggle()
        self.scene.frame_set(3, subframe=0.75)
        obj.hide_render = False
        obj.keyframe_insert(data_path="hide_render", frame=1)
        obj.keyframe_insert(data_path="hide_render", frame=5)
        obj.hide_render = True  # A manual override must survive frame reevaluation.
        before = culler._snapshot(obj)
        with mock.patch.object(culler, "_intersects_camera", side_effect=ValueError("Injected bounds failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected bounds failure"):
                bpy.ops.object.camera_cull_timeline()
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (3, 0.75))
        self.assertEqual(culler._snapshot(obj), before)
        self.assertFalse(group.hide_viewport or group.hide_render)

    def test_save_reopen_restore_and_deleted_collection_fallback(self):
        obj = self.cube("Outside", 20)
        original = bpy.data.collections.new("Original")
        self.scene.collection.children.link(original)
        culler._set_memberships(obj, (original,))
        self.scan()
        self.scene.name = "Renamed scene"
        original.name = "Renamed original"
        with tempfile.TemporaryDirectory(prefix="helix-cull-") as directory:
            path = str(Path(directory) / "culling.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.scene = bpy.context.scene
            obj = bpy.data.objects["Outside"]
            group = culler._scene_group(self.scene)
            self.assertIsNotNone(group)
            self.assertEqual(group[culler.OWNER], self.scene)
            bpy.data.collections.remove(bpy.data.collections["Renamed original"])
            self.assertEqual(bpy.ops.object.camera_cull_restore(), {"FINISHED"})
            self.assertIn(obj, self.scene.collection.objects.values())
            self.assertNotIn(culler.TAG, obj)

    def test_unknown_bounds_and_singular_camera_are_retained(self):
        outside = self.cube("Outside", 30)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        limits = culler._camera_limits(self.camera.evaluated_get(depsgraph), self.scene, 0)
        class UnknownBounds:
            modifiers, particle_systems, instance_type, type = [], [], "NONE", "MESH"
            def evaluated_get(self, graph):
                return type("Unknown", (), {"bound_box": [(-1, -1, -1)] * 8})()
        self.assertTrue(culler._intersects_camera(UnknownBounds(), depsgraph, limits))
        self.camera.scale = (0, 0, 0)
        self.assertNotIn(outside, self.scan().objects.values())


if __name__ == "__main__":
    unittest.main()
