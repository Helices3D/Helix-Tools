"""Regression coverage for baked world transforms and persistent rig management."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy
from mathutils import Matrix

import smart_empty


class SmartEmptyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smart_empty.register()

    @classmethod
    def tearDownClass(cls):
        smart_empty.unregister()

    def setUp(self):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.scene = bpy.context.scene
        for layer in list(self.scene.view_layers):
            if layer != bpy.context.view_layer:
                self.scene.view_layers.remove(layer)
        settings = self.scene.helix_smart_empty_settings
        for name in (
            "empty_size", "display_type", "name_suffix", "show_name", "show_in_front",
            "use_local_orientation", "bone_point", "rig", "list_scope",
            "anchor_index",
        ):
            settings.property_unset(name)
        self.scene.transform_orientation_slots[0].type = 'GLOBAL'

    def make_object(self, name):
        obj = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(obj)
        return obj

    def activate(self, obj):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for selected in bpy.context.selected_objects:
            selected.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.context.view_layer.update()

    def make_rig(self, name="Rig"):
        rig = bpy.data.objects.new(name, bpy.data.armatures.new(f"{name} Data"))
        self.scene.collection.objects.link(rig)
        self.activate(rig)
        bpy.ops.object.mode_set(mode='EDIT')
        bone = rig.data.edit_bones.new("Jaw")
        bone.head = (1.0, 2.0, 3.0)
        bone.tail = (1.0, 3.2, 3.0)
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.data.bones.active = rig.data.bones["Jaw"]
        rig.pose.bones["Jaw"].select = True
        rig.pose.bones["Jaw"].rotation_mode = 'XYZ'
        rig.pose.bones["Jaw"].rotation_euler = (0.4, -0.25, 0.5)
        return rig

    def add_bone_anchor(self, rig):
        self.activate(rig)
        bpy.ops.object.mode_set(mode='POSE')
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
        return bpy.context.active_object

    def assertMatrixNear(self, actual, expected):
        for row in range(4):
            for column in range(4):
                self.assertAlmostEqual(actual[row][column], expected[row][column], places=5)

    def test_evaluated_parented_pose_world_transform_is_independent(self):
        parent = self.make_object("Rig Parent")
        parent.location = (3.1, -2.7, 1.4)
        parent.rotation_euler = (0.2, -0.4, 0.6)
        parent.scale = (1.4, 0.8, 1.1)
        rig = self.make_rig()
        rig.parent = parent
        rig.location = (-0.6, 1.3, 0.2)
        rig.rotation_euler = (0.3, 0.15, -0.2)
        target = self.make_object("Animated Target")
        target.location = (-1.0, 5.0, 2.0)
        constraint = rig.pose.bones["Jaw"].constraints.new('COPY_LOCATION')
        constraint.target = target
        constraint.target_space = 'WORLD'
        constraint.owner_space = 'WORLD'
        self.activate(rig)
        bpy.ops.object.mode_set(mode='POSE')
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        source_matrix = evaluated.matrix_world @ evaluated.pose.bones["Jaw"].matrix
        location, rotation, _scale = source_matrix.decompose()
        expected = Matrix.LocRotScale(location, rotation, None)

        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
        anchor = bpy.context.active_object
        self.assertMatrixNear(anchor.matrix_world, expected)
        self.assertIsNone(anchor.parent)
        self.assertEqual(len(anchor.constraints), 0)
        self.assertEqual(bpy.context.mode, 'OBJECT')
        self.assertEqual(list(bpy.context.selected_objects), [anchor])
        self.assertEqual(self.scene.transform_orientation_slots[0].type, 'LOCAL')
        for component in anchor.scale:
            self.assertAlmostEqual(component, 1.0, places=6)
        rig.location.x += 8.0
        target.location.y -= 3.0
        bpy.context.view_layer.update()
        self.assertMatrixNear(anchor.matrix_world, expected)

    def test_tail_position_and_object_display_options(self):
        rig = self.make_rig()
        settings = self.scene.helix_smart_empty_settings
        settings.bone_point = 'TAIL'
        settings.empty_size = 0.35
        settings.display_type = 'ARROWS'
        settings.name_suffix = " Aim"
        settings.show_name = False
        settings.show_in_front = False
        settings.use_local_orientation = False
        self.activate(rig)
        bpy.ops.object.mode_set(mode='POSE')
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        tail = evaluated.matrix_world @ evaluated.pose.bones["Jaw"].tail
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
        anchor = bpy.context.active_object
        self.assertLess((anchor.location - tail).length, 0.00001)
        self.assertEqual(anchor.name, "Jaw Aim")
        self.assertEqual(anchor.empty_display_type, 'ARROWS')
        self.assertAlmostEqual(anchor.empty_display_size, 0.35)
        self.assertFalse(anchor.show_name)
        self.assertFalse(anchor.show_in_front)
        self.assertEqual(self.scene.transform_orientation_slots[0].type, 'GLOBAL')

    def test_scaled_parented_object_anchors_without_inherited_scale(self):
        parent = self.make_object("Parent")
        parent.rotation_euler = (0.4, 0.3, -0.2)
        parent.scale = (2.0, 3.0, 1.0)
        source = self.make_object("Source")
        source.parent = parent
        source.location = (1.0, 2.0, 3.0)
        source.rotation_euler = (0.0, 0.2, 0.1)
        self.activate(source)
        expected_source = source.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
        location, rotation, _scale = expected_source.decompose()
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
        anchor = bpy.context.active_object
        self.assertMatrixNear(anchor.matrix_world, Matrix.LocRotScale(location, rotation, None))
        self.assertEqual(anchor.helix_smart_empty.source_object, source)
        self.assertIsNone(anchor.helix_smart_empty.rig)
        self.assertEqual(self.scene.helix_smart_empty_settings.list_scope, 'ALL')

    def test_hidden_active_collection_keeps_new_anchor_visible(self):
        source = self.make_object("Visible Source")
        hidden_collection = bpy.data.collections.new("Hidden Active Collection")
        self.scene.collection.children.link(hidden_collection)
        bpy.context.view_layer.update()
        hidden_layer = bpy.context.view_layer.layer_collection.children[hidden_collection.name]
        hidden_layer.hide_viewport = True
        self.activate(source)
        bpy.context.view_layer.active_layer_collection = hidden_layer
        try:
            self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
            anchor = bpy.context.active_object
            self.assertTrue(anchor.visible_get())
            self.assertIn(self.scene.collection, anchor.users_collection)
        finally:
            bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
            bpy.data.collections.remove(hidden_collection)

    def test_rig_and_bone_renames_persist_across_save_reload(self):
        rig = self.make_rig()
        anchor = self.add_bone_anchor(rig)
        identifier = anchor.helix_smart_empty.bone_id
        self.assertTrue(identifier)
        rig.name = "Renamed Rig"
        rig.data.bones["Jaw"].name = "Renamed Jaw"
        anchor.name = "Renamed Anchor"
        self.assertEqual(smart_empty.source_bone(anchor.helix_smart_empty).name, "Renamed Jaw")
        with tempfile.TemporaryDirectory(prefix="helix-smart-empty-") as directory:
            path = str(Path(directory) / "anchors.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        self.scene = bpy.context.scene
        restored = bpy.data.objects["Renamed Anchor"].helix_smart_empty
        self.assertEqual(restored.rig, bpy.data.objects["Renamed Rig"])
        self.assertEqual(restored.source_object, restored.rig)
        self.assertEqual(restored.owner_scene, self.scene)
        self.assertEqual(restored.bone_id, identifier)
        self.assertEqual(smart_empty.source_bone(restored).name, "Renamed Jaw")

    def test_rig_hide_restore_preserves_manual_visibility_and_render_flags(self):
        rig = self.make_rig("Rig A")
        visible = self.add_bone_anchor(rig)
        hidden_before = self.add_bone_anchor(rig)
        hidden_before.hide_set(True)
        visible.hide_render = True
        other_rig = self.make_rig("Rig B")
        other_anchor = self.add_bone_anchor(other_rig)
        regular = self.make_object("Untracked Empty")
        settings = self.scene.helix_smart_empty_settings
        settings.rig = rig
        self.assertEqual(bpy.ops.object.smart_empty_rig_visibility(action='HIDE'), {'FINISHED'})
        self.assertTrue(visible.hide_get())
        self.assertTrue(hidden_before.hide_get())
        self.assertFalse(other_anchor.hide_get())
        self.assertFalse(regular.hide_get())
        # A second Hide must not replace the original visible state with hidden.
        bpy.ops.object.smart_empty_rig_visibility(action='HIDE')
        bpy.ops.object.smart_empty_rig_visibility(action='RESTORE')
        self.assertFalse(visible.hide_get())
        self.assertTrue(hidden_before.hide_get())
        self.assertTrue(visible.hide_render)
        self.assertFalse(hidden_before.hide_render)
        self.assertFalse(other_anchor.hide_get())
        bpy.ops.object.smart_empty_rig_visibility(action='RESTORE')
        self.assertTrue(hidden_before.hide_get())

    def test_visibility_is_per_view_layer_and_survives_layer_rename(self):
        rig = self.make_rig()
        anchor = self.add_bone_anchor(rig)
        first = bpy.context.view_layer
        second = self.scene.view_layers.new("Second Layer")
        smart_empty.set_rig_visibility(bpy.context, rig, True)
        self.assertTrue(anchor.hide_get(view_layer=first))
        self.assertFalse(anchor.hide_get(view_layer=second))
        second_context = SimpleNamespace(scene=self.scene, view_layer=second)
        smart_empty.set_rig_visibility(second_context, rig, True)
        smart_empty.set_rig_visibility(second_context, rig, False)
        self.assertTrue(anchor.hide_get(view_layer=first))
        self.assertFalse(anchor.hide_get(view_layer=second))
        first.name = "Renamed Layer"
        smart_empty.set_rig_visibility(bpy.context, rig, False)
        self.assertFalse(anchor.hide_get(view_layer=first))

    def test_copied_view_layer_id_does_not_alias_visibility_records(self):
        rig = self.make_rig()
        anchor = self.add_bone_anchor(rig)
        first = bpy.context.view_layer
        smart_empty.set_rig_visibility(bpy.context, rig, True)
        original_id = first[smart_empty._VIEW_LAYER_ID_KEY]
        second = self.scene.view_layers.new("Copied Layer")
        second[smart_empty._VIEW_LAYER_ID_KEY] = original_id
        second_context = SimpleNamespace(scene=self.scene, view_layer=second)
        smart_empty.set_rig_visibility(second_context, rig, True)
        self.assertEqual(first[smart_empty._VIEW_LAYER_ID_KEY], original_id)
        self.assertNotEqual(second[smart_empty._VIEW_LAYER_ID_KEY], original_id)
        self.assertEqual(len(anchor.helix_smart_empty.visibility), 2)
        smart_empty.set_rig_visibility(second_context, rig, False)
        self.assertFalse(anchor.hide_get(view_layer=second))
        self.assertTrue(anchor.hide_get(view_layer=first))
        smart_empty.set_rig_visibility(bpy.context, rig, False)
        self.assertFalse(anchor.hide_get(view_layer=first))

    def test_linked_library_helpers_are_skipped_without_changing_visibility(self):
        rig = self.make_rig("Library Rig")
        anchor = self.add_bone_anchor(rig)
        anchor.name = "Library Anchor"
        with tempfile.TemporaryDirectory(prefix="helix-linked-empty-") as directory:
            library_path = str(Path(directory) / "library.blend")
            bpy.data.libraries.write(library_path, {rig, anchor})
            bpy.data.objects.remove(anchor, do_unlink=True)
            bpy.data.objects.remove(rig, do_unlink=True)
            with bpy.data.libraries.load(library_path, link=True) as (_source, destination):
                destination.objects = ["Library Rig", "Library Anchor"]
            linked_rig, linked_anchor = destination.objects
            self.scene.collection.objects.link(linked_rig)
            self.scene.collection.objects.link(linked_anchor)
            bpy.context.view_layer.update()
            self.assertFalse(linked_anchor.is_editable)
            self.assertEqual(smart_empty.set_rig_visibility(bpy.context, linked_rig, True), (0, 1))
            self.assertFalse(linked_anchor.hide_get())
            self.assertEqual(len(linked_anchor.helix_smart_empty.visibility), 0)
            # Newly created local anchors for that linked rig are still managed.
            self.activate(linked_rig)
            self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
            local_anchor = bpy.context.active_object
            self.assertTrue(local_anchor.is_editable)
            self.assertEqual(smart_empty.set_rig_visibility(bpy.context, linked_rig, True), (1, 1))
            self.assertTrue(local_anchor.hide_get())
            self.assertFalse(linked_anchor.hide_get())
            smart_empty.set_rig_visibility(bpy.context, linked_rig, False)
            self.assertFalse(local_anchor.hide_get())

    def test_linked_armature_data_uses_persistent_bone_name_fallback(self):
        rig = self.make_rig("Library Data Rig")
        armature = rig.data
        data_name = armature.name
        with tempfile.TemporaryDirectory(prefix="helix-linked-bone-") as directory:
            library_path = str(Path(directory) / "bones.blend")
            local_path = str(Path(directory) / "local.blend")
            bpy.data.libraries.write(library_path, {armature})
            bpy.data.objects.remove(rig, do_unlink=True)
            bpy.data.armatures.remove(armature)
            with bpy.data.libraries.load(library_path, link=True) as (_source, destination):
                destination.armatures = [data_name]
            linked_armature = destination.armatures[0]
            self.assertFalse(linked_armature.is_editable)
            local_rig = bpy.data.objects.new("Local Rig With Linked Bones", linked_armature)
            self.scene.collection.objects.link(local_rig)
            self.activate(local_rig)
            linked_armature.bones.active = linked_armature.bones["Jaw"]
            local_rig.pose.bones["Jaw"].select = True
            bpy.ops.object.mode_set(mode='POSE')
            self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
            anchor = bpy.context.active_object
            anchor.name = "Linked Bone Anchor"
            self.assertEqual(anchor.helix_smart_empty.bone_id, "")
            self.assertNotIn(smart_empty._BONE_ID_KEY, linked_armature.bones["Jaw"])
            bpy.ops.wm.save_as_mainfile(filepath=local_path)
            bpy.ops.wm.open_mainfile(filepath=local_path)
            self.scene = bpy.context.scene
            restored = bpy.data.objects["Linked Bone Anchor"].helix_smart_empty
            self.assertEqual(restored.bone_id, "")
            self.assertEqual(smart_empty.source_bone(restored).name, "Jaw")
            self.assertEqual(restored.rig.name, "Local Rig With Linked Bones")

    def test_creation_failure_removes_empty_and_restores_pose_selection(self):
        rig = self.make_rig()
        self.activate(rig)
        bpy.ops.object.mode_set(mode='POSE')
        before_objects = set(bpy.data.objects)
        original_link = smart_empty._link_anchor

        def fail_after_link(context, empty):
            original_link(context, empty)
            raise RuntimeError("Simulated collection failure")

        with mock.patch.object(smart_empty, "_link_anchor", side_effect=fail_after_link):
            self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'CANCELLED'})
        self.assertEqual(set(bpy.data.objects), before_objects)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(bpy.context.active_object, rig)
        self.assertEqual(list(bpy.context.selected_objects), [rig])
        self.assertEqual(self.scene.transform_orientation_slots[0].type, 'GLOBAL')
        self.assertIsNone(self.scene.helix_smart_empty_settings.rig)

    def test_missing_bone_and_rig_leave_safe_tracked_anchor(self):
        rig = self.make_rig()
        anchor = self.add_bone_anchor(rig)
        self.activate(rig)
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones.remove(rig.data.edit_bones["Jaw"])
        bpy.ops.object.mode_set(mode='OBJECT')
        self.assertIsNone(smart_empty.source_bone(anchor.helix_smart_empty))
        self.assertIn("Missing bone", smart_empty._source_description(anchor.helix_smart_empty))
        bpy.data.objects.remove(rig, do_unlink=True)
        self.assertIsNone(anchor.helix_smart_empty.rig)
        self.assertIn("Missing rig", smart_empty._source_description(anchor.helix_smart_empty))
        self.assertIn(anchor, smart_empty.tracked_empties(self.scene))

    def test_select_reveals_anchor_and_leaves_pose_mode(self):
        rig = self.make_rig()
        anchor = self.add_bone_anchor(rig)
        anchor.hide_set(True)
        self.activate(rig)
        bpy.ops.object.mode_set(mode='POSE')
        self.assertEqual(bpy.ops.object.smart_empty_select(object_name=anchor.name), {'FINISHED'})
        self.assertEqual(bpy.context.mode, 'OBJECT')
        self.assertEqual(bpy.context.active_object, anchor)
        self.assertFalse(anchor.hide_get())

    def filtered_anchors(self, query="", *, inverted=False, alphabetical=False):
        # Blender 5.2.2's native list accepts FILTER_ITEM directly and gives
        # NEVER_SHOW precedence. Inversion is applied by filter_items itself.
        ui_list = SimpleNamespace(
            filter_name=query, use_filter_invert=inverted,
            use_filter_sort_alpha=alphabetical,
            bitflag_filter_item=1 << 30, bitflag_item_never_show=1 << 16,
        )
        before = (
            self.scene.helix_smart_empty_settings.anchor_index,
            bpy.context.active_object, tuple(bpy.context.selected_objects),
            tuple((obj, obj.hide_viewport, obj.hide_render, obj.hide_get(),
                   tuple(tuple(row) for row in obj.matrix_world)) for obj in self.scene.objects),
        )
        flags, order = smart_empty.SMART_EMPTY_UL_anchors.filter_items(
            ui_list, bpy.context, self.scene, "objects",
        )
        self.assertEqual(len(flags), len(self.scene.objects))
        visible = [obj for obj, flag in zip(self.scene.objects, flags)
                   if not flag & ui_list.bitflag_item_never_show and flag & ui_list.bitflag_filter_item]
        if order:
            self.assertEqual(sorted(order), list(range(len(self.scene.objects))))
            positions = {obj: index for index, obj in enumerate(self.scene.objects)}
            visible.sort(key=lambda obj: order[positions[obj]])
        after = (
            self.scene.helix_smart_empty_settings.anchor_index,
            bpy.context.active_object, tuple(bpy.context.selected_objects),
            tuple((obj, obj.hide_viewport, obj.hide_render, obj.hide_get(),
                   tuple(tuple(row) for row in obj.matrix_world)) for obj in self.scene.objects),
        )
        self.assertEqual(after, before, "Filtering must not change selection, transforms, or visibility")
        return visible

    def test_anchor_browser_search_inversion_and_scope_never_expose_untracked_objects(self):
        first_rig = self.make_rig("First Rig")
        zeta = self.add_bone_anchor(first_rig)
        zeta.name = "Zeta Anchor"
        alpha = self.add_bone_anchor(first_rig)
        alpha.name = "alpha Anchor"
        second_rig = self.make_rig("Second Rig")
        other = self.add_bone_anchor(second_rig)
        other.name = "Zeta Other Rig"
        untracked = self.make_object("Zeta Untracked Empty")
        settings = self.scene.helix_smart_empty_settings
        settings.rig, settings.list_scope = first_rig, "RIG"
        self.assertEqual(self.filtered_anchors("  zETA  "), [zeta])
        self.assertEqual(self.filtered_anchors("zeta", inverted=True), [alpha])
        self.assertEqual(set(self.filtered_anchors(inverted=True)), {zeta, alpha})
        first_rig.name = "Renamed First Rig"
        self.assertEqual(set(self.filtered_anchors("renamed first rig")), {zeta, alpha})
        self.assertEqual(set(self.filtered_anchors("JAW")), {zeta, alpha})
        self.assertEqual(self.filtered_anchors(alphabetical=True), [alpha, zeta])
        settings.list_scope = "ALL"
        self.assertEqual(set(self.filtered_anchors("zeta")), {zeta, other})
        self.assertEqual(self.filtered_anchors("zeta", inverted=True), [alpha])
        self.assertNotIn(untracked, self.filtered_anchors())
        settings.list_scope, settings.rig = "RIG", None
        self.assertFalse(self.filtered_anchors())
        self.assertFalse(self.filtered_anchors("zeta", inverted=True))

    def test_new_anchor_browser_highlight_follows_creation_without_automatic_selection_on_browse(self):
        source = self.make_object("Object Source")
        self.activate(source)
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {"FINISHED"})
        anchor = bpy.context.active_object
        settings = self.scene.helix_smart_empty_settings
        self.assertEqual(self.scene.objects[settings.anchor_index], anchor)
        self.assertEqual(self.filtered_anchors("object source"), [anchor])
        selected, active = tuple(bpy.context.selected_objects), bpy.context.active_object
        settings.anchor_index = tuple(self.scene.objects).index(source)
        self.assertEqual(tuple(bpy.context.selected_objects), selected)
        self.assertEqual(bpy.context.active_object, active)
        self.assertEqual(self.filtered_anchors("object source"), [anchor])

    def test_registration_cycles_and_legacy_collision_are_safe(self):
        for _ in range(3):
            smart_empty.unregister()
            smart_empty.unregister()
            smart_empty.register()
            smart_empty.register()
        smart_empty.unregister()

        class LEGACY_OT_add(bpy.types.Operator):
            bl_idname = "object.add_smart_empty_baked"
            bl_label = "Legacy Smart Empty"

            def execute(self, context):
                return {'FINISHED'}

        bpy.utils.register_class(LEGACY_OT_add)
        try:
            with self.assertRaisesRegex(RuntimeError, "Disable the older"):
                smart_empty.register()
            self.assertFalse(hasattr(bpy.types.Scene, "helix_smart_empty_settings"))
            self.assertIs(
                bpy.types.Operator.bl_rna_get_subclass_py("OBJECT_OT_add_smart_empty_baked"),
                LEGACY_OT_add,
            )
            smart_empty.unregister()
            self.assertIs(
                bpy.types.Operator.bl_rna_get_subclass_py("OBJECT_OT_add_smart_empty_baked"),
                LEGACY_OT_add,
            )
        finally:
            bpy.utils.unregister_class(LEGACY_OT_add)
            smart_empty.register()

    def test_partial_registration_failure_rolls_back(self):
        smart_empty.unregister()
        original_register = bpy.utils.register_class

        def register_with_failure(cls):
            if cls is smart_empty.SMART_EMPTY_OT_add_baked:
                raise RuntimeError("Simulated registration failure")
            original_register(cls)

        try:
            with mock.patch.object(bpy.utils, "register_class", side_effect=register_with_failure):
                with self.assertRaisesRegex(RuntimeError, "Simulated registration failure"):
                    smart_empty.register()
            self.assertFalse(hasattr(bpy.types.Object, "helix_smart_empty"))
            self.assertFalse(smart_empty._registered_classes)
            self.assertIsNone(
                bpy.types.PropertyGroup.bl_rna_get_subclass_py("SMART_EMPTY_PG_metadata")
            )
        finally:
            smart_empty.register()
