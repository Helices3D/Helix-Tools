"""Cloth cache lifecycle regressions using Blender 5.2.2's actual point cache."""

from pathlib import Path
from contextlib import contextmanager
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

    def model(self, name):
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        self.scene.collection.objects.link(obj)
        return obj

    def rig(self, name):
        obj = bpy.data.objects.new(name, bpy.data.armatures.new(name))
        self.scene.collection.objects.link(obj)
        return obj

    @contextmanager
    def cloth_preferences(self):
        preferences = bpy.context.preferences
        existing = preferences.addons.get(addon.__name__)
        self.assertIsNone(existing, 'Fixture needs a separate preferences entry')
        dirty = preferences.is_dirty
        entry = preferences.addons.new()
        entry.module = addon.__name__
        try:
            self.assertIsNotNone(entry.preferences)
            yield entry.preferences
        finally:
            preferences.addons.remove(entry)
            preferences.is_dirty = dirty

    def test_groups_follow_deformation_targets_through_a_chain_of_cages(self):
        from cloth_cache_manager._groups import cloth_groups

        inner, _ = self.cloth('Inner cage')
        outer, _ = self.cloth('Outer cage')
        unrelated, _ = self.cloth('Another cage')
        outer.modifiers.new('Follow inner cage', 'SURFACE_DEFORM').target = inner
        model = self.model('Character mesh')
        model.modifiers.new('Follow outer cage', 'MESH_DEFORM').object = outer
        other_model = self.model('Other mesh')
        other_model.modifiers.new('Follow another cage', 'SURFACE_DEFORM').target = unrelated
        outer.cloth_tool_selected = False
        before = self.state()
        included = {obj: obj.cloth_tool_selected for obj in (inner, outer, unrelated)}
        groups = cloth_groups(self.scene)
        by_model = {group.model: set(group.objects) for group in groups}
        self.assertEqual(by_model, {model: {inner, outer}, other_model: {unrelated}})
        self.assertEqual(self.state(), before)
        self.assertEqual({obj: obj.cloth_tool_selected for obj in included}, included)

    def test_groups_combine_affected_meshes_and_parented_cages_under_their_rig(self):
        from cloth_cache_manager._groups import cloth_groups

        first, _ = self.cloth('First cage')
        second, _ = self.cloth('Second cage')
        parented, _ = self.cloth('Parented cage')
        rigged, _ = self.cloth('Rigged cage')
        first_rig, second_rig = self.rig('Character rig'), self.rig('Other rig')
        first_model, second_model = self.model('Body'), self.model('Clothing')
        first_model.modifiers.new('Cage deformation', 'MESH_DEFORM').object = first
        second_model.modifiers.new('Cage deformation', 'SURFACE_DEFORM').target = second
        for model in (first_model, second_model):
            model.modifiers.new('Character armature', 'ARMATURE').object = first_rig
        parented.parent = first_rig
        rigged.modifiers.new('Other armature', 'ARMATURE').object = second_rig
        before = self.state()
        groups = cloth_groups(self.scene)
        by_model = {group.model: set(group.objects) for group in groups}
        self.assertEqual(by_model, {first_rig: {first, second, parented}, second_rig: {rigged}})
        self.assertEqual(self.state(), before)

    def test_shared_model_sets_stay_distinct_and_empty_parent_does_not_guess_a_model(self):
        from cloth_cache_manager._groups import cloth_groups

        shared_ab, _ = self.cloth('Shared A B')
        shared_ac, _ = self.cloth('Shared A C')
        unrelated, _ = self.cloth('Unrelated')
        organizational_empty = bpy.data.objects.new('All scene objects', None)
        self.scene.collection.objects.link(organizational_empty)
        unrelated.parent = organizational_empty
        models = [self.model(name) for name in ('Model A', 'Model B', 'Model C')]
        for index, cage in ((0, shared_ab), (1, shared_ab), (0, shared_ac), (2, shared_ac)):
            models[index].modifiers.new('Cage deformation', 'MESH_DEFORM').object = cage
        groups = cloth_groups(self.scene)
        shared = [group for group in groups if group.models]
        self.assertEqual(len(shared), 2)
        self.assertNotEqual(shared[0].key, shared[1].key)
        self.assertEqual({frozenset(group.models): set(group.objects) for group in shared}, {
            frozenset(models[:2]): {shared_ab},
            frozenset((models[0], models[2])): {shared_ac},
        })
        ungrouped = [group for group in groups if group.model is None and not group.models]
        self.assertEqual(len(ungrouped), 1)
        self.assertEqual(set(ungrouped[0].objects), {unrelated})
        all_cages = [obj for group in groups for obj in group.objects]
        self.assertEqual(len(all_cages), len(set(all_cages)))

    def test_manual_group_assignment_overrides_detection_and_invalid_assignments_fall_back(self):
        from cloth_cache_manager._groups import cloth_groups

        cage, _ = self.cloth()
        model = self.model('Detected model')
        rig = self.rig('Detected rig')
        model.modifiers.new('Cage deformation', 'MESH_DEFORM').object = cage
        model.modifiers.new('Rig', 'ARMATURE').object = rig
        explicit_empty = bpy.data.objects.new('Custom group', None)
        self.scene.collection.objects.link(explicit_empty)
        before = self.state()
        for explicit in (explicit_empty, model):
            with self.subTest(explicit=explicit.name):
                cage.cloth_tool_model = explicit
                groups = cloth_groups(self.scene)
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0].model, explicit)
                self.assertEqual(set(groups[0].objects), {cage})
                self.assertEqual(self.state(), before)
        foreign_scene = bpy.data.scenes.new('Other scene')
        foreign = bpy.data.objects.new('Foreign model', None)
        foreign_scene.collection.objects.link(foreign)
        for invalid in (cage, foreign):
            with self.subTest(invalid=invalid.name):
                cage.cloth_tool_model = invalid
                groups = cloth_groups(self.scene)
                self.assertEqual(groups[0].model, rig)
        cage.cloth_tool_model = explicit_empty
        bpy.data.objects.remove(explicit_empty, do_unlink=True)
        self.assertIsNone(cage.cloth_tool_model)
        self.assertEqual(cloth_groups(self.scene)[0].model, rig)

    def test_reset_confirmation_preferences_default_to_warning_and_can_be_reenabled(self):
        from cloth_cache_manager import _settings

        with self.cloth_preferences() as preferences:
            self.assertTrue(preferences.cloth_confirm_resets)
            self.assertTrue(_settings.should_confirm_reset(bpy.context))
            self.assertEqual(_settings.get_preferences(bpy.context), preferences)
            bpy.context.preferences.is_dirty = False
            self.assertTrue(_settings.set_confirmation_enabled(bpy.context, False))
            self.assertFalse(preferences.cloth_confirm_resets)
            self.assertFalse(_settings.should_confirm_reset(bpy.context))
            self.assertTrue(bpy.context.preferences.is_dirty)
            self.assertTrue(_settings.set_confirmation_enabled(bpy.context, True))
            self.assertTrue(_settings.should_confirm_reset(bpy.context))
        self.assertIsNone(_settings.get_preferences(bpy.context))
        self.assertTrue(_settings.should_confirm_reset(bpy.context))
        self.assertFalse(_settings.set_confirmation_enabled(bpy.context, False))

    def test_confirmation_preferences_resolve_standalone_and_suite_roots(self):
        from cloth_cache_manager import _settings

        for package, expected in (
            ('cloth_cache_manager', 'cloth_cache_manager'),
            ('bl_ext.user_default.cloth_cache_manager', 'bl_ext.user_default.cloth_cache_manager'),
            ('helix_tools.cloth_cache_manager', 'helix_tools'),
            ('bl_ext.user_default.helix_tools.cloth_cache_manager', 'bl_ext.user_default.helix_tools'),
        ):
            with self.subTest(package=package):
                self.assertEqual(_settings.preferences_package(package), expected)

    def test_reset_invoke_prompts_by_default_and_executes_when_warning_is_disabled(self):
        dialog = mock.Mock(return_value={'RUNNING_MODAL'})
        context = SimpleNamespace(
            preferences=bpy.context.preferences,
            window_manager=SimpleNamespace(invoke_props_dialog=dialog),
        )
        operator = SimpleNamespace(dont_show_again=True, execute=mock.Mock(return_value={'FINISHED'}))
        with self.cloth_preferences() as preferences:
            self.assertEqual(addon.CLOTH_OT_ResetBakes.invoke(operator, context, None), {'RUNNING_MODAL'})
            self.assertFalse(operator.dont_show_again)
            dialog.assert_called_once()
            operator.execute.assert_not_called()
            self.assertTrue(preferences.cloth_confirm_resets)
            preferences.cloth_confirm_resets = False
            dialog.reset_mock()
            self.assertEqual(addon.CLOTH_OT_ResetBakes.invoke(operator, context, None), {'FINISHED'})
            dialog.assert_not_called()
            operator.execute.assert_called_once_with(context)
            preferences.cloth_confirm_resets = True
            self.assertEqual(addon.CLOTH_OT_ResetBakes.invoke(operator, context, None), {'RUNNING_MODAL'})
            dialog.assert_called_once()

    def test_dont_show_again_applies_after_success_and_not_after_a_cancelled_reset(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        self.assertEqual(addon.process_caches(bpy.context, 'BAKE').processed, 1)
        before = self.state()
        with self.cloth_preferences() as preferences:
            with mock.patch.object(addon, "_run_cache_operator", return_value={'CANCELLED'}):
                result = bpy.ops.cloth_manager.reset_bakes(dont_show_again=True)
            self.assertEqual(result, {'CANCELLED'})
            self.assertTrue(preferences.cloth_confirm_resets)
            self.assertTrue(modifier.point_cache.is_baked)
            self.assertEqual(self.state(), before)
            result = bpy.ops.cloth_manager.reset_bakes(dont_show_again=True)
            self.assertEqual(result, {'FINISHED'})
            self.assertFalse(preferences.cloth_confirm_resets)
            self.assertFalse(modifier.point_cache.is_baked)
            self.assertEqual(self.state(), before)

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

    def test_check_all_changes_only_cloth_inclusion_not_selection_or_physics(self):
        first, _ = self.cloth("First")
        second, _ = self.cloth("Second")
        second.cloth_tool_selected = False
        bystander = bpy.data.objects.new("Selected bystander without cloth", None)
        self.scene.collection.objects.link(bystander)
        bystander.cloth_tool_selected = False
        first.select_set(False)
        second.select_set(False)
        bystander.select_set(True)
        bpy.context.view_layer.objects.active = bystander
        first.hide_select = True
        self.scene.frame_set(3, subframe=0.25)
        before = self.state()
        for include in (True, False, False, True):
            with self.subTest(include=include):
                self.assertEqual(bpy.ops.cloth_manager.set_included(include=include), {"FINISHED"})
                self.assertEqual(first.cloth_tool_selected, include)
                self.assertEqual(second.cloth_tool_selected, include)
                self.assertFalse(bystander.cloth_tool_selected)
                self.assertEqual(self.state(), before)

    def test_uncheck_all_preserves_excluded_cloth_and_other_scenes(self):
        accessible, _ = self.cloth("Accessible")
        excluded, _ = self.cloth("Excluded")
        collection = bpy.data.collections.new("Excluded physics")
        self.scene.collection.children.link(collection)
        for original in tuple(excluded.users_collection):
            original.objects.unlink(excluded)
        collection.objects.link(excluded)
        bpy.context.view_layer.layer_collection.children[collection.name].exclude = True
        other, _ = self.cloth("Other scene cloth")
        other_scene = bpy.data.scenes.new("Other scene")
        for original in tuple(other.users_collection):
            original.objects.unlink(other)
        other_scene.collection.objects.link(other)
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.cloth_manager.set_included(include=False), {"FINISHED"})
        self.assertFalse(accessible.cloth_tool_selected)
        self.assertTrue(excluded.cloth_tool_selected)
        self.assertTrue(other.cloth_tool_selected)
        collection_layer = bpy.context.view_layer.layer_collection.children[collection.name]
        self.assertTrue(collection_layer.exclude)

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

    def test_reset_at_cache_start_reports_success_when_blender_empties_the_cache(self):
        # At its start frame, Blender immediately clears the invalidated
        # cache instead of leaving the is_outdated flag set. Both are valid
        # reset outcomes; no timer or scene-frame change is needed.
        for start in (0, 1, 3):
            for baked in (False, True):
                with self.subTest(start=start, baked=baked):
                    bpy.ops.wm.read_factory_settings(use_empty=True)
                    self.scene = bpy.context.scene
                    self.scene.frame_start, self.scene.frame_end = start, start + 5
                    obj, (modifier,) = self.cloth()
                    cache = modifier.point_cache
                    cache.frame_start, cache.frame_end = start, start + 5
                    self.simulate(obj, frames=range(start, start + 5))
                    if baked:
                        promoted = addon.process_caches(bpy.context, 'BAKE')
                        self.assertEqual((promoted.processed, promoted.failed), (1, 0), promoted.details)
                    self.scene.frame_set(start)
                    self.assertGreater(addon.cached_frame_count(cache), 0)
                    self.assertFalse(cache.is_outdated)
                    self.assertEqual(cache.is_baked, baked)
                    bystander = bpy.data.objects.new("Selected bystander", None)
                    self.scene.collection.objects.link(bystander)
                    obj.select_set(False)
                    bystander.select_set(True)
                    bpy.context.view_layer.objects.active = bystander
                    obj.hide_select = True
                    before = self.state()
                    result = addon.process_caches(bpy.context, 'RESET')
                    self.assertEqual((result.processed, result.unchanged, result.failed), (1, 0, 0), result.details)
                    self.assertFalse(cache.is_baked)
                    self.assertEqual(addon.cached_frame_count(cache), 0)
                    self.assertFalse(cache.is_outdated)
                    self.assertEqual(self.state(), before)
                    with mock.patch.object(addon, "_run_cache_operator") as operation:
                        repeated = addon.process_caches(bpy.context, 'RESET')
                    operation.assert_not_called()
                    self.assertEqual((repeated.processed, repeated.unchanged, repeated.failed), (0, 1, 0))

    def test_reset_at_cache_start_reports_info_instead_of_a_false_failure(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        self.assertEqual(addon.process_caches(bpy.context, 'BAKE').processed, 1)
        self.scene.frame_set(modifier.point_cache.frame_start)
        before = self.state()
        reporter = SimpleNamespace(report=mock.Mock())
        self.assertEqual(addon._report_batch(reporter, bpy.context, 'RESET'), {'FINISHED'})
        self.assertFalse(modifier.point_cache.is_baked)
        self.assertEqual(addon.cached_frame_count(modifier.point_cache), 0)
        self.assertEqual(self.state(), before)
        self.assertEqual(reporter.report.call_count, 1)
        severity, message = reporter.report.call_args.args
        self.assertEqual(severity, {'INFO'})
        self.assertIn("Reset 1 cloth caches", message)
        self.assertIn("0 failed", message)

    def test_reset_of_disk_cache_at_start_accepts_the_immediately_empty_cache(self):
        for baked in (False, True):
            with self.subTest(baked=baked), tempfile.TemporaryDirectory() as directory:
                bpy.ops.wm.read_factory_settings(use_empty=True)
                self.scene = bpy.context.scene
                self.scene.frame_start, self.scene.frame_end = 1, 4
                obj, (modifier,) = self.cloth()
                cache = modifier.point_cache
                bpy.ops.wm.save_as_mainfile(filepath=str(Path(directory) / 'reset-disk-cache.blend'))
                cache.use_disk_cache = True
                cache.name = 'reset_regression'
                self.simulate(obj)
                if baked:
                    promoted = addon.process_caches(bpy.context, 'BAKE')
                    self.assertEqual((promoted.processed, promoted.failed), (1, 0), promoted.details)
                self.assertTrue(list(Path(directory).rglob('*.bphys')),
                                'Fixture must exercise actual disk cache files')
                self.scene.frame_set(cache.frame_start)
                self.assertGreater(addon.cached_frame_count(cache), 0)
                before = self.state()
                result = addon.process_caches(bpy.context, 'RESET')
                self.assertEqual((result.processed, result.failed), (1, 0), result.details)
                self.assertFalse(cache.is_baked)
                self.assertFalse(cache.is_outdated)
                self.assertEqual(addon.cached_frame_count(cache), 0)
                self.assertEqual(self.state(), before)
                promoted = addon.process_caches(bpy.context, 'BAKE')
                self.assertEqual((promoted.processed, promoted.skipped, promoted.failed), (0, 1, 0), promoted.details)

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

    def test_cancelled_reset_keeps_its_bake_and_other_cache_is_reset(self):
        first, (first_mod,) = self.cloth("First")
        second, (second_mod,) = self.cloth("Second")
        self.simulate(first, second)
        self.assertEqual(addon.process_caches(bpy.context, 'BAKE').processed, 2)
        first.select_set(False)
        second.select_set(True)
        bpy.context.view_layer.objects.active = second
        first.hide_select = True
        bpy.context.view_layer.update()
        first_outdated = first_mod.point_cache.is_outdated
        before = self.state()
        native = addon._run_cache_operator

        def cancelled(context, obj, modifier, action):
            if obj == first:
                return {'CANCELLED'}
            return native(context, obj, modifier, action)

        with mock.patch.object(addon, "_run_cache_operator", side_effect=cancelled):
            result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (1, 1), result.details)
        self.assertTrue(any("First" in detail and "Blender cancelled" in detail
                            for detail in result.details), result.details)
        self.assertTrue(first_mod.point_cache.is_baked)
        self.assertEqual(first_mod.point_cache.is_outdated, first_outdated)
        self.assertFalse(second_mod.point_cache.is_baked)
        self.assertTrue(second_mod.point_cache.is_outdated)
        self.assertEqual(self.state(), before)

    def test_reset_exception_preserves_its_bake_and_other_cache_is_reset(self):
        first, (first_mod,) = self.cloth("First")
        second, (second_mod,) = self.cloth("Second")
        self.simulate(first, second)
        self.assertEqual(addon.process_caches(bpy.context, 'BAKE').processed, 2)
        before = self.state()
        native = addon._run_cache_operator

        def fail_first(context, obj, modifier, action):
            if obj == first:
                raise RuntimeError("Injected unavailable cache")
            return native(context, obj, modifier, action)

        with mock.patch.object(addon, "_run_cache_operator", side_effect=fail_first):
            result = addon.process_caches(bpy.context, 'RESET')
        self.assertEqual((result.processed, result.failed), (1, 1), result.details)
        self.assertTrue(any("First" in detail and "Injected unavailable cache" in detail
                            for detail in result.details), result.details)
        self.assertTrue(first_mod.point_cache.is_baked)
        self.assertFalse(first_mod.point_cache.is_outdated)
        self.assertFalse(second_mod.point_cache.is_baked)
        self.assertTrue(second_mod.point_cache.is_outdated)
        self.assertEqual(self.state(), before)

    def test_finished_reset_that_does_not_free_the_bake_is_reported_as_failed(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        self.assertEqual(addon.process_caches(bpy.context, 'BAKE').processed, 1)
        before = self.state()
        reporter = SimpleNamespace(report=mock.Mock())
        with mock.patch.object(addon, "_run_cache_operator", return_value={'FINISHED'}):
            result = addon._report_batch(reporter, bpy.context, 'RESET')
        self.assertEqual(result, {'CANCELLED'})
        self.assertTrue(modifier.point_cache.is_baked)
        self.assertFalse(modifier.point_cache.is_outdated)
        self.assertEqual(self.state(), before)
        messages = [call.args[1] for call in reporter.report.call_args_list]
        self.assertTrue(any("Blender did not free this bake" in text for text in messages), messages)
        self.assertTrue(any("0 cloth caches" in text and "1 failed" in text for text in messages), messages)

    def test_reset_still_fails_if_old_unbaked_frames_remain_valid(self):
        obj, (modifier,) = self.cloth()
        self.simulate(obj)
        cache = modifier.point_cache
        before = self.state()
        # Keep actual native cache data and free_bake(), but suppress the
        # dependency-graph refresh to represent an unapplied invalidation.
        # Merely receiving FINISHED must not make reusable old frames a success.
        layer = SimpleNamespace(objects=bpy.context.view_layer.objects, update=mock.Mock())
        context = SimpleNamespace(
            scene=self.scene, view_layer=layer, mode='OBJECT',
            evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get,
            temp_override=bpy.context.temp_override,
        )
        try:
            result = addon.process_caches(context, 'RESET')
            self.assertEqual((result.processed, result.failed), (0, 1), result.details)
            self.assertGreater(addon.cached_frame_count(cache), 0)
            self.assertFalse(cache.is_baked)
            self.assertFalse(cache.is_outdated)
            self.assertEqual(self.state(), before)
            self.assertTrue(any("did not invalidate" in text for text in result.details), result.details)
        finally:
            bpy.context.view_layer.update()

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
            self.assertEqual(bpy.ops.cloth_manager.set_included(include=False), {"CANCELLED"})
            self.assertTrue(linked.cloth_tool_selected)
            self.assertTrue(local.cloth_tool_selected)
            editable, _ = self.cloth("Editable cloth")
            self.assertEqual(bpy.ops.cloth_manager.set_included(include=False), {"FINISHED"})
            self.assertFalse(editable.cloth_tool_selected)
            self.assertTrue(linked.cloth_tool_selected)
            self.assertTrue(local.cloth_tool_selected)

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
        checked, _ = self.cloth("Checked")
        unchecked, _ = self.cloth("Unchecked")
        unchecked.cloth_tool_selected = False
        model = self.model("Assigned model")
        checked.cloth_tool_model = model
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "cloth-choices.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.assertTrue(bpy.data.objects['Checked'].cloth_tool_selected)
            self.assertFalse(bpy.data.objects['Unchecked'].cloth_tool_selected)
            self.assertEqual(bpy.data.objects['Checked'].cloth_tool_model,
                             bpy.data.objects['Assigned model'])
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
        self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_model'))
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
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_model'))
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

    def test_core_registration_failure_rolls_back_both_object_properties(self):
        addon.unregister()
        native = bpy.utils.register_class

        def fail_reset_class(cls):
            if cls is addon.CLOTH_OT_ResetBakes:
                raise RuntimeError("Injected Cloth class failure")
            native(cls)

        try:
            with mock.patch.object(bpy.utils, 'register_class', side_effect=fail_reset_class):
                with self.assertRaisesRegex(RuntimeError, "Injected Cloth class failure"):
                    addon.register()
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_selected'))
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_model'))
            self.assertTrue(all(not cls.is_registered for cls in addon.CLASSES))
            self.assertTrue(all(not cls.is_registered for cls in addon._UPDATER.classes))
            addon.unregister()
        finally:
            addon.register()

    def test_existing_model_group_property_is_preserved_after_registration_collision(self):
        addon.unregister()
        try:
            bpy.types.Object.cloth_tool_model = bpy.props.PointerProperty(
                type=bpy.types.Object, name="Other add-on model",
            )
            with self.assertRaisesRegex(RuntimeError, "Disable the older or duplicate"):
                addon.register()
            addon.unregister()
            self.assertTrue(hasattr(bpy.types.Object, 'cloth_tool_model'))
            self.assertEqual(bpy.types.Object.bl_rna.properties['cloth_tool_model'].name,
                             "Other add-on model")
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_selected'))
            self.assertFalse(addon.CLOTH_OT_ResetBakes.is_registered)
        finally:
            if hasattr(bpy.types.Object, 'cloth_tool_model'):
                del bpy.types.Object.cloth_tool_model
            addon.register()

    def test_unregister_preserves_a_model_group_property_replaced_by_another_addon(self):
        cage, _ = self.cloth()
        model = self.model("Foreign group model")
        del bpy.types.Object.cloth_tool_model
        bpy.types.Object.cloth_tool_model = bpy.props.PointerProperty(
            type=bpy.types.Object, name="Other add-on model",
        )
        cage.cloth_tool_model = model
        try:
            addon.unregister()
            self.assertTrue(hasattr(bpy.types.Object, 'cloth_tool_model'))
            self.assertEqual(cage.cloth_tool_model, model)
            self.assertEqual(bpy.types.Object.bl_rna.properties['cloth_tool_model'].name,
                             "Other add-on model")
            self.assertFalse(hasattr(bpy.types.Object, 'cloth_tool_selected'))
            self.assertFalse(addon.CLOTH_OT_ResetBakes.is_registered)
        finally:
            if hasattr(bpy.types.Object, 'cloth_tool_model'):
                del bpy.types.Object.cloth_tool_model
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
