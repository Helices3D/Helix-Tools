"""Cloth cache lifecycle regressions using Blender 5.2.2's actual point cache."""

from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy
import cloth_cache_manager as addon


class ClothCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import _bpy_restrict_state

        cls.restrict_state = _bpy_restrict_state
        cls.original_restrict_bpy = _bpy_restrict_state._bpy
        # The bpy wheel can retain a bootstrap alias distinct from the public
        # module. Use the same real RestrictBlend as Blender extension enabling.
        _bpy_restrict_state._bpy = bpy
        addon.register()

    @classmethod
    def tearDownClass(cls):
        try:
            addon.unregister()
        finally:
            cls.restrict_state._bpy = cls.original_restrict_bpy

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.scene.frame_start, self.scene.frame_end = 1, 4

    def cloth(self, name="Cloth", modifiers=1):
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2)
        obj = bpy.context.object
        obj.name = name
        cloth_modifiers = []
        for index in range(modifiers):
            modifier = obj.modifiers.new(f"Cloth {index + 1}", "CLOTH")
            modifier.settings.quality = 1
            modifier.point_cache.frame_start = 1
            modifier.point_cache.frame_end = 4
            cloth_modifiers.append(modifier)
        return obj, cloth_modifiers

    def simulate(self, *objects, frames=range(1, 5)):
        for frame in frames:
            self.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            for obj in objects:
                obj.evaluated_get(depsgraph).to_mesh_clear()
        for obj in objects:
            for modifier in obj.modifiers:
                if modifier.type == 'CLOTH':
                    self.assertGreater(addon.cached_frame_count(modifier.point_cache), 0)
                    self.assertFalse(modifier.point_cache.is_outdated)

    def state(self):
        return {
            "active": bpy.context.view_layer.objects.active,
            "selected": tuple((obj, obj.select_get()) for obj in self.scene.objects),
            "mode": bpy.context.mode,
            "frame": (self.scene.frame_current, self.scene.frame_subframe),
            "objects": tuple(
                (obj, obj.hide_select, obj.hide_viewport, obj.hide_render, obj.hide_get())
                for obj in self.scene.objects
            ),
            "cloth": tuple(
                (obj, modifier, modifier.settings.quality,
                 modifier.show_viewport, modifier.show_render,
                 modifier.point_cache.frame_start, modifier.point_cache.frame_end)
                for obj in self.scene.objects for modifier in obj.modifiers
                if modifier.type == 'CLOTH'
            ),
        }

    def test_checked_filter_includes_every_checked_cloth_object(self):
        checked, modifiers = self.cloth("Checked")
        second, second_modifiers = self.cloth("Also checked")
        unchecked, _ = self.cloth("Unchecked")
        unchecked.cloth_tool_selected = False
        empty = bpy.data.objects.new("No cloth", None)
        self.scene.collection.objects.link(empty)
        targets, skipped = addon.cloth_targets(bpy.context)
        self.assertEqual(set(targets), {
            *((checked, modifier) for modifier in modifiers),
            *((second, modifier) for modifier in second_modifiers),
        })
        self.assertFalse(skipped)
        self.assertTrue(checked.cloth_tool_selected)

    def test_real_current_cache_promotion_and_repeat_preserve_scene_state(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        bystander = bpy.data.objects.new("Selected bystander", None)
        self.scene.collection.objects.link(bystander)
        obj.select_set(False)
        bystander.select_set(True)
        bpy.context.view_layer.objects.active = bystander
        self.scene.frame_set(3, subframe=0.25)
        obj.hide_select = True
        obj.hide_render = True
        modifier.show_render = False
        before = self.state()
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual((result.processed, result.failed), (1, 0), result.details)
        self.assertTrue(modifier.point_cache.is_baked)
        self.assertEqual(self.state(), before)
        with mock.patch.object(addon, "_run_cache_operator") as operation:
            result = addon.process_caches(bpy.context, 'BAKE')
        operation.assert_not_called()
        self.assertEqual((result.processed, result.unchanged, result.failed), (0, 1, 0))
        self.assertEqual(self.state(), before)

    def test_empty_cache_is_never_reported_as_a_successful_bake(self):
        _, (modifier,) = self.cloth()
        self.assertEqual(addon.cached_frame_count(modifier.point_cache), 0)
        with mock.patch.object(addon, "_run_cache_operator") as operation:
            result = addon.process_caches(bpy.context, 'BAKE')
        operation.assert_not_called()
        self.assertEqual((result.processed, result.skipped, result.failed), (0, 1, 0))
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertEqual(bpy.ops.cloth_manager.bake_from_cache(), {'CANCELLED'})
        self.assertFalse(modifier.point_cache.is_baked)

    def test_real_batch_promotes_all_objects_and_reset_invalidates_their_memory(self):
        first, first_modifiers = self.cloth("First")
        second, second_modifiers = self.cloth("Second")
        modifiers = first_modifiers + second_modifiers
        self.simulate(first, second)
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual((result.processed, result.failed), (2, 0), result.details)
        self.assertTrue(all(modifier.point_cache.is_baked for modifier in modifiers))
        before = self.state()
        result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (2, 0), result.details)
        self.assertTrue(all(not modifier.point_cache.is_baked for modifier in modifiers))
        self.assertTrue(all(modifier.point_cache.is_outdated for modifier in modifiers))
        self.assertEqual(self.state(), before)
        with mock.patch.object(addon, "_run_cache_operator") as operation:
            result = addon.process_caches(bpy.context, 'BAKE')
        operation.assert_not_called()
        self.assertEqual((result.processed, result.skipped, result.failed), (0, 2, 0))

    def test_reset_of_unbaked_memory_invalidates_it_without_changing_quality(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        before = self.state()
        result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (1, 0), result.details)
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertTrue(modifier.point_cache.is_outdated)
        self.assertEqual(self.state(), before)

    def test_reset_invalidates_same_object_physics_but_preserves_unchecked_objects(self):
        obj, (modifier,) = self.cloth('Mixed physics')
        softbody = obj.modifiers.new('Soft body', 'SOFT_BODY')
        softbody.settings.use_goal = False
        soft_cache = softbody.point_cache
        soft_cache.frame_start, soft_cache.frame_end = 1, 4
        unchecked, (unchecked_modifier,) = self.cloth('Unchecked')
        unchecked.cloth_tool_selected = False
        self.simulate(obj, unchecked)
        self.assertGreater(addon.cached_frame_count(soft_cache), 0)
        with bpy.context.temp_override(id=obj, object=obj, active_object=obj, point_cache=soft_cache):
            self.assertEqual(bpy.ops.ptcache.bake_from_cache(), {'FINISHED'})
        self.assertTrue(soft_cache.is_baked)
        self.assertFalse(soft_cache.is_outdated)
        self.assertFalse(unchecked_modifier.point_cache.is_outdated)
        before = self.state()
        result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (1, 0), result.details)
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertTrue(modifier.point_cache.is_outdated)
        self.assertTrue(soft_cache.is_baked, 'Reset must not free the other physics bake')
        self.assertTrue(soft_cache.is_outdated, 'Blender invalidates other physics on the same object')
        self.assertFalse(unchecked_modifier.point_cache.is_baked)
        self.assertFalse(unchecked_modifier.point_cache.is_outdated)
        self.assertEqual(self.state(), before)

    def test_partial_existing_cache_is_promoted_without_simulating_missing_frames(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj, frames=(1, 2))
        cache = modifier.point_cache
        self.assertEqual(addon.cached_frame_count(cache), 2)
        before = self.state()
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual((result.processed, result.failed), (1, 0), result.details)
        self.assertTrue(cache.is_baked)
        self.assertEqual(addon.cached_frame_count(cache), 2)
        self.assertEqual(self.state(), before)

    def test_localized_cache_summary_with_no_space_still_has_a_frame_count(self):
        # These are Blender's native summaries, not numeric fields that the
        # PointCache RNA exposes. Japanese puts no space after the frame count.
        for summary in ("4フレームがメモリ上(3 KiB)", "4 帧数在内存里 (3 KiB)",
                        "4 frames en mémoire (3 KiB)"):
            with self.subTest(summary=summary):
                self.assertEqual(addon.cached_frame_count(SimpleNamespace(info=summary)), 4)

    def test_real_cache_promotion_with_available_interface_language_preserves_preferences(self):
        prefs = bpy.context.preferences.view
        original_language = prefs.language
        original_translate = prefs.use_translate_interface
        try:
            try:
                prefs.language = 'ja_JP'
            except TypeError:
                # The official bpy wheel ships no locale catalogs and only
                # permits DEFAULT. The full executable exercises Japanese;
                # the separate summary regression covers its native format in
                # both runtimes without requiring missing wheel resources.
                prefs.language = 'DEFAULT'
            prefs.use_translate_interface = True
            obj, (modifier,) = self.cloth()
            self.simulate(obj)
            self.assertEqual(addon.cached_frame_count(modifier.point_cache), 4)
            result = addon.process_caches(bpy.context, 'BAKE')
            self.assertEqual((result.processed, result.failed), (1, 0), result.details)
            self.assertTrue(modifier.point_cache.is_baked)
        finally:
            prefs.language = original_language
            prefs.use_translate_interface = original_translate

    def test_reset_of_eye_hidden_object_preserves_its_visibility_and_selection(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual(result.processed, 1, result.details)
        obj.hide_set(True)
        obj.hide_select = True
        before = self.state()
        result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (1, 0), result.details)
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertTrue(modifier.point_cache.is_outdated)
        self.assertEqual(self.state(), before)

    def test_reset_of_disabled_modifier_preserves_its_visibility(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual(result.processed, 1, result.details)
        modifier.show_viewport = False
        modifier.show_render = False
        before = self.state()
        result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (1, 0), result.details)
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertTrue(modifier.point_cache.is_outdated)
        self.assertEqual(self.state(), before)

    def test_reset_of_globally_hidden_object_is_skipped_before_freeing_the_bake(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual(result.processed, 1, result.details)
        obj.hide_viewport = True
        bpy.context.view_layer.update()
        before = self.state()
        with mock.patch.object(addon, '_run_cache_operator') as operation:
            result = addon.process_caches(bpy.context, 'RESET')
        operation.assert_not_called()
        self.assertEqual((result.processed, result.skipped, result.failed), (0, 1, 0))
        self.assertTrue(modifier.point_cache.is_baked)
        self.assertEqual(self.state(), before)

    def test_reset_preserves_quality_at_rna_minimum_and_maximum(self):
        prop = bpy.types.ClothSettings.bl_rna.properties['quality']
        for value in (prop.hard_min, prop.hard_max):
            with self.subTest(quality=value):
                bpy.ops.wm.read_factory_settings(use_empty=True)
                self.scene = bpy.context.scene
                obj, (modifier,) = self.cloth()
                self.simulate(obj)
                # The RNA hard maximum is far beyond useful solver quality.
                # Keep cached data but disable simulation before assigning it.
                modifier.show_viewport = False
                modifier.settings.quality = value
                result = addon.process_caches(bpy.context, 'RESET')
                self.assertEqual(result.failed, 0, result.details)
                self.assertEqual(modifier.settings.quality, value)
                self.assertTrue(modifier.point_cache.is_outdated)

    def test_reset_empty_cache_is_unchanged_without_native_operation(self):
        _, (modifier,) = self.cloth()
        with mock.patch.object(addon, "_run_cache_operator") as operation:
            result = addon.process_caches(bpy.context, 'RESET')
        operation.assert_not_called()
        self.assertEqual((result.processed, result.unchanged, result.failed), (0, 1, 0))
        self.assertEqual(modifier.settings.quality, 1)

    def test_cancelled_cache_operation_does_not_count_success_and_batch_continues(self):
        first, (first_mod,) = self.cloth("First")
        second, (second_mod,) = self.cloth("Second")
        self.simulate(first, second)
        before = self.state()
        native = addon._run_cache_operator

        def cancelled(context, obj, modifier, action):
            if obj == first:
                return {'CANCELLED'}
            return native(context, obj, modifier, action)

        with mock.patch.object(addon, "_run_cache_operator", side_effect=cancelled):
            result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual((result.processed, result.failed), (1, 1), result.details)
        self.assertFalse(first_mod.point_cache.is_baked)
        self.assertTrue(second_mod.point_cache.is_baked)
        self.assertEqual(self.state(), before)

    def test_exception_in_one_cache_preserves_context_and_other_cache_completes(self):
        first, (first_mod,) = self.cloth("First")
        second, (second_mod,) = self.cloth("Second")
        self.simulate(first, second)
        self.scene.frame_set(2, subframe=0.5)
        first.select_set(False)
        second.select_set(True)
        bpy.context.view_layer.objects.active = second
        first.hide_select = True
        before = self.state()
        native = addon._run_cache_operator

        def fail_first(context, obj, modifier, action):
            if obj == first:
                raise RuntimeError("Injected unavailable cache")
            return native(context, obj, modifier, action)

        with mock.patch.object(addon, "_run_cache_operator", side_effect=fail_first):
            result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual((result.processed, result.failed), (1, 1), result.details)
        self.assertTrue(any("First" in detail and "Injected unavailable cache" in detail
                            for detail in result.details), result.details)
        self.assertFalse(first_mod.point_cache.is_baked)
        self.assertTrue(second_mod.point_cache.is_baked)
        self.assertEqual(self.state(), before)

    def test_excluded_view_layer_object_is_skipped(self):
        obj, _ = self.cloth()
        collection = bpy.data.collections.new("Excluded cloth")
        self.scene.collection.children.link(collection)
        for original in tuple(obj.users_collection):
            original.objects.unlink(obj)
        collection.objects.link(obj)
        bpy.context.view_layer.layer_collection.children[collection.name].exclude = True
        bpy.context.view_layer.update()
        targets, skipped = addon.cloth_targets(bpy.context)
        self.assertFalse(targets)
        self.assertEqual(len(skipped), 1, skipped)
        result = addon.process_caches(bpy.context, 'BAKE')
        self.assertEqual((result.processed, result.skipped, result.failed), (0, 1, 0))

    def test_linked_read_only_cloth_is_skipped(self):
        obj, _ = self.cloth("Library cloth")
        with tempfile.TemporaryDirectory() as directory:
            library_path = Path(directory) / "cloth-library.blend"
            bpy.data.libraries.write(str(library_path), {obj})
            bpy.data.objects.remove(obj, do_unlink=True)
            with bpy.data.libraries.load(str(library_path), link=True) as (available, loaded):
                loaded.objects = ["Library cloth"]
            linked = loaded.objects[0]
            self.scene.collection.objects.link(linked)
            self.assertFalse(linked.is_editable)
            local = linked.copy()
            local.name = 'Local object using linked mesh'
            self.scene.collection.objects.link(local)
            self.assertTrue(local.is_editable)
            self.assertFalse(local.data.is_editable)
            targets, skipped = addon.cloth_targets(bpy.context)
            self.assertFalse(targets)
            self.assertEqual(len(skipped), 2, skipped)
            result = addon.process_caches(bpy.context, 'RESET')
            self.assertEqual((result.processed, result.skipped, result.failed), (0, 2, 0))

    def test_real_public_operators_promote_and_reset_the_existing_cache(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        self.assertEqual(bpy.ops.cloth_manager.bake_from_cache(), {'FINISHED'})
        self.assertTrue(modifier.point_cache.is_baked)
        self.assertEqual(bpy.ops.cloth_manager.reset_bakes(), {'FINISHED'})
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertTrue(modifier.point_cache.is_outdated)
        self.assertEqual(modifier.settings.quality, 1)

    def test_external_disk_cache_is_skipped_without_modifying_its_files(self):
        obj, (modifier,) = self.cloth()
        cache = modifier.point_cache
        with tempfile.TemporaryDirectory() as directory:
            scene_path = Path(directory) / 'external-cache.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
            cache.use_disk_cache = True
            cache.name = 'cloth_regression'
            self.simulate(obj)
            result = addon.process_caches(bpy.context, 'BAKE')
            self.assertEqual(result.processed, 1, result.details)
            self.assertTrue(cache.is_baked)
            files = list(Path(directory).rglob('*.bphys'))
            self.assertTrue(files, 'Fixture must create actual Blender disk cache files')
            original_bytes = {path: path.read_bytes() for path in files}
            cache.use_external = True
            before = self.state()
            with mock.patch.object(addon, '_run_cache_operator') as operation:
                for action in ('BAKE', 'RESET'):
                    with self.subTest(action=action):
                        result = addon.process_caches(bpy.context, action)
                        self.assertEqual((result.processed, result.skipped, result.failed), (0, 1, 0))
                        self.assertTrue(cache.is_baked)
            operation.assert_not_called()
            self.assertTrue(cache.use_external)
            self.assertEqual(self.state(), before)
            self.assertEqual({path: path.read_bytes() for path in files}, original_bytes)

    def test_edit_mode_is_rejected_without_changing_mode_selection_or_frame(self):
        obj, (modifier,) = self.cloth()
        bpy.ops.object.mode_set(mode='EDIT')
        before = self.state()
        try:
            with self.assertRaisesRegex(ValueError, "Object Mode"):
                addon.process_caches(bpy.context, 'BAKE')
            self.assertEqual(self.state(), before)
            self.assertFalse(modifier.point_cache.is_baked)
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')

    def test_invalid_action_or_missing_scene_view_layer_fails_before_native_calls(self):
        self.cloth()
        with mock.patch.object(addon, "_run_cache_operator") as operation:
            for action in ('UNKNOWN', '', None):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    addon.process_caches(bpy.context, action)
            for scene, view_layer in ((None, bpy.context.view_layer), (self.scene, None)):
                context = SimpleNamespace(scene=scene, view_layer=view_layer, mode='OBJECT')
                with self.subTest(scene=scene, view_layer=view_layer), self.assertRaises(ValueError):
                    addon.process_caches(context, 'BAKE')
        operation.assert_not_called()

    def test_checked_object_choices_survive_save_and_reopen(self):
        self.cloth("Checked")
        unchecked, _ = self.cloth("Unchecked")
        unchecked.cloth_tool_selected = False
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "cloth-choices.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.assertTrue(bpy.data.objects['Checked'].cloth_tool_selected)
            self.assertFalse(bpy.data.objects['Unchecked'].cloth_tool_selected)
            targets, _ = addon.cloth_targets(bpy.context)
            self.assertEqual({obj.name for obj, _ in targets}, {'Checked'})

    def test_registration_is_repeatable_and_uses_shared_panel_category(self):
        addon.register()
        self.assertTrue(addon.CLOTH_OT_BakeFromCache.is_registered)
        self.assertEqual(addon.VIEW3D_PT_ClothManager.bl_category, "Helix Tools")
        self.assertEqual(bpy.types.Object.bl_rna.properties['cloth_tool_selected'].default, True)
        addon.unregister()
        addon.unregister()
        self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_selected'))
        addon.register()

    def test_partial_registration_rolls_back_and_can_be_enabled_again(self):
        addon.unregister()
        original = bpy.utils.register_class
        calls = 0

        def fail_second(cls):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Injected registration failure")
            original(cls)

        try:
            with mock.patch.object(bpy.utils, 'register_class', side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "Injected registration failure"):
                    addon.register()
            self.assertFalse(addon.CLOTH_OT_BakeFromCache.is_registered)
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_selected'))
            addon.unregister()
        finally:
            addon.register()

    def test_older_operator_and_object_property_are_preserved_after_collision(self):
        addon.unregister()

        class OriginalClothBake(bpy.types.Operator):
            bl_idname = 'cloth_manager.bake_from_cache'
            bl_label = 'Original Cloth Manager'

            def execute(self, context):
                return {'FINISHED'}

        try:
            bpy.utils.register_class(OriginalClothBake)
            bpy.types.Object.cloth_tool_selected = bpy.props.BoolProperty(default=False)
            with self.assertRaisesRegex(RuntimeError, "Disable the older or duplicate"):
                addon.register()
            addon.unregister()
            self.assertTrue(OriginalClothBake.is_registered)
            self.assertFalse(bpy.types.Object.bl_rna.properties['cloth_tool_selected'].default)
            self.assertFalse(addon.CLOTH_OT_ResetBakes.is_registered)
        finally:
            if hasattr(bpy.types.Object, 'cloth_tool_selected'):
                del bpy.types.Object.cloth_tool_selected
            if OriginalClothBake.is_registered:
                bpy.utils.unregister_class(OriginalClothBake)
            addon.register()

    def test_unregister_preserves_an_object_property_replaced_by_another_addon(self):
        del bpy.types.Object.cloth_tool_selected
        bpy.types.Object.cloth_tool_selected = bpy.props.BoolProperty(default=False)
        try:
            addon.unregister()
            self.assertTrue(hasattr(bpy.types.Object, 'cloth_tool_selected'))
            self.assertFalse(bpy.types.Object.bl_rna.properties['cloth_tool_selected'].default)
            self.assertFalse(addon.CLOTH_OT_BakeFromCache.is_registered)
        finally:
            if hasattr(bpy.types.Object, 'cloth_tool_selected'):
                del bpy.types.Object.cloth_tool_selected
            addon.register()

    def test_real_restricted_registration_does_not_access_scene_objects(self):
        from _bpy_restrict_state import RestrictBlend

        addon.unregister()
        try:
            with RestrictBlend():
                self.assertFalse(hasattr(bpy.data, 'objects'))
                addon.register()
                self.assertTrue(addon.CLOTH_OT_BakeFromCache.is_registered)
                addon.unregister()
        finally:
            addon.register()

    def test_actual_addon_utils_enable_and_disable_succeed(self):
        import addon_utils

        addon.unregister()
        errors = []
        addon.__time__ = os.path.getmtime(addon.__file__)
        try:
            enabled = addon_utils.enable(
                addon.__name__, default_set=False, persistent=False, handle_error=errors.append,
            )
            self.assertIs(enabled, addon, errors)
            self.assertFalse(errors)
            self.assertTrue(addon_utils.check(addon.__name__)[1])
            self.assertTrue(hasattr(bpy.types.Object, 'cloth_tool_selected'))
        finally:
            addon_utils.disable(addon.__name__, default_set=False)
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_selected'))
            addon.register()


if __name__ == '__main__':
    unittest.main()
