# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender image, scene-reference and reversible resolution regressions."""

from array import array
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

import bpy
import texture_resolution as addon
from texture_resolution._images import create_half_image, supported_image
from texture_resolution._scan import scan_scene


class TextureResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import _bpy_restrict_state

        cls.restrict_state = _bpy_restrict_state
        cls.original_restrict_bpy = _bpy_restrict_state._bpy
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
        self.directory = tempfile.TemporaryDirectory(prefix="helix-textures-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.scene = bpy.context.scene
        self.settings = getattr(self.scene, addon.SCENE_PROPERTY)
        self.settings.threshold = 8
        self.settings.output_directory = str(self.path / "halves")

    def image(self, name="Source", size=(16, 8), *, floating=False,
              color=(0.2, 0.4, 0.8, 0.5), space="Non-Color", alpha="STRAIGHT"):
        image = bpy.data.images.new(name, width=size[0], height=size[1],
                                    alpha=True, float_buffer=floating)
        image.colorspace_settings.name = space
        image.alpha_mode = alpha
        image.pixels.foreach_set(array("f", color) * (size[0] * size[1]))
        image.update()
        return image

    def pixels(self, image):
        values = array("f", [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        return values

    def image_state(self, image):
        return (image.name, image.source, image.filepath, tuple(image.size),
                image.colorspace_settings.name, image.alpha_mode, image.is_float,
                image.is_dirty, image.packed_file.data if image.packed_file else None,
                self.pixels(image).tobytes())

    def mesh(self, name="Model", scene=None):
        obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        (scene or self.scene).collection.objects.link(obj)
        return obj

    def material_node(self, image, *, obj=None, material=None):
        obj = obj or self.mesh()
        material = material or bpy.data.materials.new(obj.name + " Material")
        material.use_nodes = True
        obj.data.materials.append(material)
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        return obj, material, node

    def setup(self):
        result = addon.setup_scene(bpy.context)
        self.assertEqual(result.failed, 0, result)
        return result

    def switch(self, mode):
        result = addon.switch_scene(bpy.context, mode)
        self.assertEqual(result.failed, 0, result)
        return result

    def test_default_threshold_uses_either_dimension_and_includes_boundary(self):
        self.settings.property_unset("threshold")
        self.assertEqual(self.settings.threshold, 4000)
        # Very narrow textures exercise the real 4000 px boundary cheaply.
        qualifies = self.image("4000 wide", (4000, 1))
        below = self.image("3999 wide", (3999, 1))
        _, _, node = self.material_node(qualifies)
        _, _, small_node = self.material_node(below)
        self.setup()
        self.assertEqual(tuple(node.image.size), (2000, 1))
        self.assertEqual(small_node.image, below)
        self.assertEqual(len(self.settings.records), 1)

    def test_setup_then_switch_round_trip_preserves_original_and_scene_state(self):
        original = self.image()
        obj, _, node = self.material_node(original)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        self.scene.frame_set(37, subframe=0.25)
        before = self.image_state(original)
        selected = list(bpy.context.selected_objects)
        self.setup()
        alternative = node.image
        self.assertNotEqual(alternative, original)
        self.assertEqual(tuple(alternative.size), (8, 4))
        self.assertTrue(Path(bpy.path.abspath(alternative.filepath)).is_file())
        self.assertEqual(self.image_state(original), before)
        self.assertEqual(list(bpy.context.selected_objects), selected)
        self.assertEqual(bpy.context.active_object, obj)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (37, 0.25))
        self.switch("ORIGINAL")
        self.assertEqual(node.image, original)
        self.switch("HALF")
        self.assertEqual(node.image, alternative)
        self.assertEqual(self.image_state(original), before)

    def test_public_setup_and_resolution_buttons_use_the_same_reversible_workflow(self):
        original = self.image()
        _, _, node = self.material_node(original)
        self.assertEqual(bpy.ops.helix_textures.setup(), {"FINISHED"})
        alternative = node.image
        self.assertNotEqual(alternative, original)
        self.assertEqual(bpy.ops.helix_textures.switch_resolution(mode="ORIGINAL"), {"FINISHED"})
        self.assertEqual(node.image, original)
        self.assertEqual(bpy.ops.helix_textures.switch_resolution(mode="HALF"), {"FINISHED"})
        self.assertEqual(node.image, alternative)

    def test_one_alternative_per_image_and_repeat_setup_never_halves_a_half(self):
        original = self.image()
        _, _, first = self.material_node(original)
        _, _, second = self.material_node(original)
        self.setup()
        alternative = first.image
        files = set((self.path / "halves").iterdir())
        self.assertEqual(second.image, alternative)
        self.assertEqual(len(self.settings.records), 1)
        self.setup()
        self.assertEqual(first.image, alternative)
        self.assertEqual(second.image, alternative)
        self.assertEqual(tuple(first.image.size), (8, 4))
        self.assertEqual(set((self.path / "halves").iterdir()), files)
        self.assertEqual(len(self.settings.records), 1)

    def test_unreferenced_and_other_scene_only_images_are_not_downscaled(self):
        used = self.image("Current")
        unrelated = self.image("Unreferenced")
        foreign = self.image("Other scene only")
        _, _, local_node = self.material_node(used)
        foreign_scene = bpy.data.scenes.new("Other scene")
        _, _, foreign_node = self.material_node(foreign, obj=self.mesh("Foreign", foreign_scene))
        self.setup()
        self.assertNotEqual(local_node.image, used)
        self.assertEqual(foreign_node.image, foreign)
        self.assertEqual(tuple(unrelated.size), (16, 8))
        self.assertEqual(len(self.settings.records), 1)

    def test_hidden_scene_objects_are_included(self):
        original = self.image()
        obj, _, node = self.material_node(original)
        obj.hide_viewport = True
        obj.hide_render = True
        self.setup()
        self.assertNotEqual(node.image, original)
        self.assertTrue(obj.hide_viewport)
        self.assertTrue(obj.hide_render)
        self.switch("ORIGINAL")
        self.assertEqual(node.image, original)

    def test_linked_material_reference_is_skipped_without_modifying_its_library(self):
        original = self.image()
        obj, material, _ = self.material_node(original)
        library_path = self.path / "library.blend"
        bpy.data.libraries.write(str(library_path), {material}, fake_user=True)
        library_bytes = library_path.read_bytes()
        bpy.data.objects.remove(obj, do_unlink=True)
        with bpy.data.libraries.load(str(library_path), link=True) as (source, target):
            target.materials = [material.name]
        linked = target.materials[0]
        self.mesh("Linked material model").data.materials.append(linked)
        node = next(node for node in linked.node_tree.nodes if node.type == "TEX_IMAGE")
        linked_source = node.image
        result = self.setup()
        self.assertEqual(result.processed, 0)
        self.assertGreater(result.skipped, 0)
        self.assertEqual(node.image, linked_source)
        self.assertEqual(len(self.settings.records), 0)
        self.assertEqual(library_path.read_bytes(), library_bytes)

    def test_same_image_in_separate_scene_slots_does_not_change_other_scene(self):
        original = self.image()
        _, _, local_node = self.material_node(original)
        other_scene = bpy.data.scenes.new("Other scene")
        _, _, other_node = self.material_node(original, obj=self.mesh("Foreign", other_scene))
        self.setup()
        self.assertNotEqual(local_node.image, original)
        self.assertEqual(other_node.image, original)
        self.switch("ORIGINAL")
        self.assertEqual(local_node.image, original)
        self.assertEqual(other_node.image, original)

    def test_shared_material_requires_opt_in(self):
        original = self.image()
        _, material, node = self.material_node(original)
        other_scene = bpy.data.scenes.new("Other scene")
        foreign = self.mesh("Foreign", other_scene)
        foreign.data.materials.append(material)
        self.assertTrue(any(slot.shared for slot in scan_scene(self.scene).slots))
        result = self.setup()
        self.assertEqual(node.image, original)
        self.assertEqual(len(self.settings.records), 0)
        self.assertGreater(result.skipped, 0)
        self.settings.include_shared = True
        self.setup()
        self.assertNotEqual(node.image, original)
        self.switch("ORIGINAL")
        self.assertEqual(node.image, original)

    def test_nested_material_group_world_and_compositor_switch_together(self):
        original = self.image()
        _, material, direct_node = self.material_node(original)
        group = bpy.data.node_groups.new("Nested textures", "ShaderNodeTree")
        group_node = group.nodes.new("ShaderNodeTexImage")
        group_node.image = original
        for _ in range(2):
            material.node_tree.nodes.new("ShaderNodeGroup").node_tree = group
        world = bpy.data.worlds.new("World")
        world.use_nodes = True
        self.scene.world = world
        world_node = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
        world_node.image = original
        compositor = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
        self.scene.compositing_node_group = compositor
        comp_node = compositor.nodes.new("CompositorNodeImage")
        comp_node.image = original
        nodes = (direct_node, group_node, world_node, comp_node)
        self.setup()
        alternative = direct_node.image
        self.assertEqual({node.image for node in nodes}, {alternative})
        self.assertEqual(len(self.settings.records), 1)
        self.switch("ORIGINAL")
        self.assertEqual({node.image for node in nodes}, {original})

    def test_geometry_modifier_image_input_and_texture_modifier_are_updated(self):
        original = self.image()
        obj = self.mesh()
        group = bpy.data.node_groups.new("Geometry", "GeometryNodeTree")
        socket = group.interface.new_socket(name="Image", in_out="INPUT",
                                            socket_type="NodeSocketImage")
        group.nodes.new("NodeGroupInput")
        group.nodes.new("NodeGroupOutput")
        modifier = obj.modifiers.new("Geometry", "NODES")
        modifier.node_group = group
        image_input = getattr(modifier.properties.inputs, socket.identifier)
        image_input.value = original
        texture = bpy.data.textures.new("Displacement", "IMAGE")
        texture.image = original
        displace = obj.modifiers.new("Displace", "DISPLACE")
        displace.texture = texture
        self.setup()
        self.assertNotEqual(image_input.value, original)
        self.assertEqual(texture.image, image_input.value)
        self.switch("ORIGINAL")
        self.assertEqual(image_input.value, original)
        self.assertEqual(texture.image, original)

    def test_camera_background_and_image_empty_are_reversible(self):
        original = self.image()
        camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        self.scene.collection.objects.link(camera)
        background = camera.data.background_images.new()
        background.image = original
        empty = bpy.data.objects.new("Reference", None)
        self.scene.collection.objects.link(empty)
        empty.empty_display_type = "IMAGE"
        empty.data = original
        self.setup()
        self.assertNotEqual(background.image, original)
        self.assertEqual(empty.data, background.image)
        self.switch("ORIGINAL")
        self.assertEqual(background.image, original)
        self.assertEqual(empty.data, original)

    def test_manual_rewire_and_renamed_nodes_are_respected(self):
        original = self.image()
        unrelated = self.image("User replacement")
        _, _, first = self.material_node(original)
        _, _, second = self.material_node(original)
        self.setup()
        alternative = second.image
        first.image = unrelated
        second.name = "Renamed after setup"
        self.switch("ORIGINAL")
        self.assertEqual(first.image, unrelated)
        self.assertEqual(second.image, original)
        self.switch("HALF")
        self.assertEqual(first.image, unrelated)
        self.assertEqual(second.image, alternative)

    def test_changed_original_pixels_regenerate_without_downscaling_the_old_variant(self):
        original = self.image()
        _, _, node = self.material_node(original)
        self.setup()
        first_variant = node.image
        original.pixels.foreach_set(array("f", (0.7, 0.1, 0.3, 1.0)) * 128)
        original.update()
        before = self.image_state(original)
        self.setup()
        self.assertNotEqual(node.image, first_variant)
        self.assertEqual(tuple(node.image.size), (8, 4))
        self.assertEqual(len(self.settings.records), 1)
        for actual, expected in zip(self.pixels(node.image)[:4], self.pixels(original)[:4]):
            self.assertAlmostEqual(actual, expected, delta=1 / 255 + 1e-6)
        self.assertEqual(self.image_state(original), before)
        self.switch("ORIGINAL")
        self.assertEqual(node.image, original)

    def test_refresh_preserves_unsaved_paint_on_the_previous_alternative(self):
        original = self.image()
        _, _, node = self.material_node(original)
        self.setup()
        previous = node.image
        previous_name = previous.name
        previous.pixels.foreach_set(array("f", (0.9, 0.05, 0.2, 1.0)) * 32)
        previous.update()
        self.assertTrue(previous.is_dirty)
        painted_pixels = self.pixels(previous).tobytes()
        original.pixels.foreach_set(array("f", (0.05, 0.8, 0.3, 1.0)) * 128)
        original.update()
        original_state = self.image_state(original)
        self.setup()
        self.assertEqual(bpy.data.images.get(previous_name), previous)
        self.assertTrue(previous.use_fake_user)
        self.assertEqual(self.pixels(previous).tobytes(), painted_pixels)
        self.assertNotEqual(node.image, previous)
        self.assertEqual(tuple(node.image.size), (8, 4))
        self.assertEqual(self.image_state(original), original_state)
        for actual, expected in zip(self.pixels(node.image)[:4], self.pixels(original)[:4]):
            self.assertAlmostEqual(actual, expected, delta=1 / 255 + 1e-6)
        self.assertEqual(self.settings.records[0].alternative, node.image)
        self.switch("ORIGINAL")
        self.assertEqual(node.image, original)
        self.assertEqual(self.pixels(previous).tobytes(), painted_pixels)

    def test_missing_alternative_file_cannot_replace_a_valid_original(self):
        original = self.image()
        _, _, node = self.material_node(original)
        self.setup()
        alternative = node.image
        self.switch("ORIGINAL")
        Path(bpy.path.abspath(alternative.filepath)).unlink()
        result = addon.switch_scene(bpy.context, "HALF")
        self.assertEqual(result.failed, 1)
        self.assertEqual(node.image, original)
        self.assertEqual(len(self.settings.records), 1)
        self.setup()
        self.assertNotEqual(node.image, original)
        self.assertEqual(tuple(node.image.size), (8, 4))
        self.assertTrue(Path(bpy.path.abspath(node.image.filepath)).is_file())

    def test_deleted_original_is_reported_without_losing_the_active_alternative(self):
        original = self.image()
        _, _, node = self.material_node(original)
        self.setup()
        alternative = node.image
        bpy.data.images.remove(original, do_unlink=True)
        result = addon.switch_scene(bpy.context, "ORIGINAL")
        self.assertEqual(result.failed, 1)
        self.assertEqual(node.image, alternative)
        self.assertTrue(Path(bpy.path.abspath(alternative.filepath)).is_file())

    def test_memory_estimates_account_for_pixels_precision_and_each_mip_level(self):
        source = self.image()
        estimate = addon.estimate_image_memory(source)
        half = addon.estimate_image_memory(source, width=8, height=4)
        self.assertEqual(estimate.ram_bytes, 16 * 8 * 4)
        self.assertEqual(half.ram_bytes, 8 * 4 * 4)
        self.assertEqual(estimate.vram_bytes, (128 + 32 + 8 + 2 + 1) * 4)
        self.assertEqual(half.vram_bytes, (32 + 8 + 2 + 1) * 4)
        self.assertEqual(half.ram_bytes, estimate.ram_bytes // 4)
        without_mips = addon.estimate_image_memory(source, include_mipmaps=False)
        self.assertEqual(without_mips.vram_bytes, 128 * 4)
        floating = self.image("Float", floating=True)
        floating.use_half_precision = True
        estimate = addon.estimate_image_memory(floating)
        self.assertEqual(estimate.ram_bytes, 128 * 4 * 4)
        self.assertEqual(estimate.vram_bytes, (128 + 32 + 8 + 2 + 1) * 4 * 2)
        floating.use_half_precision = False
        self.assertEqual(addon.estimate_image_memory(floating).vram_bytes,
                         (128 + 32 + 8 + 2 + 1) * 4 * 4)

    def test_failed_output_is_reported_without_changes_or_partial_tracking(self):
        original = self.image()
        _, _, node = self.material_node(original)
        before = self.image_state(original)
        images_before = set(bpy.data.images)
        bad_directory = self.path / "file-instead-of-directory"
        bad_directory.write_bytes(b"Do not overwrite")
        self.settings.output_directory = str(bad_directory)
        result = addon.setup_scene(bpy.context)
        self.assertEqual(result.failed, 1)
        self.assertEqual(node.image, original)
        self.assertEqual(len(self.settings.records), 0)
        self.assertEqual(set(bpy.data.images), images_before)
        self.assertEqual(self.image_state(original), before)
        self.assertEqual(bad_directory.read_bytes(), b"Do not overwrite")

    def test_failed_image_does_not_prevent_the_remaining_batch_from_finishing(self):
        from texture_resolution import _core

        broken, valid = self.image("Broken"), self.image("Valid")
        _, _, broken_node = self.material_node(broken)
        _, _, valid_node = self.material_node(valid)
        create = _core.create_half_image

        def fail_one(image, directory):
            if image == broken:
                raise OSError("Injected single-image failure")
            return create(image, directory)

        with mock.patch.object(_core, "create_half_image", side_effect=fail_one):
            result = addon.setup_scene(bpy.context)
        self.assertEqual((result.processed, result.failed), (1, 1))
        self.assertEqual(broken_node.image, broken)
        self.assertNotEqual(valid_node.image, valid)
        self.assertEqual(len(self.settings.records), 1)
        self.switch("ORIGINAL")
        self.assertEqual(valid_node.image, valid)

    def test_cancelled_setup_retains_completed_work_and_leaves_the_rest_untouched(self):
        original_nodes = []
        for name in ("First", "Second"):
            image = self.image(name)
            _, _, node = self.material_node(image)
            original_nodes.append((image, node))
        iterator = addon.iter_setup(bpy.context)
        progress, total, _ = next(iterator)
        self.assertEqual((progress, total), (1, 2))
        iterator.close()
        self.assertEqual(len(self.settings.records), 1)
        self.assertEqual(sum(node.image != image for image, node in original_nodes), 1)
        self.assertIn("stopped", self.settings.last_report.lower())
        self.switch("ORIGINAL")
        self.assertTrue(all(node.image == image for image, node in original_nodes))
        self.setup()
        self.assertEqual(len(self.settings.records), 2)
        self.assertTrue(all(node.image != image for image, node in original_nodes))

    def test_manual_rewire_between_modal_steps_is_not_overwritten(self):
        original_nodes = []
        for name in ("First", "Second"):
            image = self.image(name)
            _, _, node = self.material_node(image)
            original_nodes.append((image, node))
        replacement = self.image("User replacement")
        iterator = addon.iter_setup(bpy.context)
        next(iterator)
        untouched = next(node for image, node in original_nodes if node.image == image)
        untouched.image = replacement
        list(iterator)
        self.assertEqual(untouched.image, replacement)
        self.switch("ORIGINAL")
        self.assertEqual(untouched.image, replacement)

    def test_existing_half_in_another_scene_tracks_the_real_original_without_quartering(self):
        original = self.image()
        _, _, first_node = self.material_node(original)
        self.setup()
        alternative = first_node.image
        other_scene = bpy.data.scenes.new("Another scene")
        _, _, second_node = self.material_node(alternative, obj=self.mesh("Other model", other_scene))
        other_settings = getattr(other_scene, addon.SCENE_PROPERTY)
        other_settings.threshold = 8
        other_settings.output_directory = str(self.path / "other-output")
        with bpy.context.temp_override(scene=other_scene):
            result = addon.setup_scene(bpy.context)
            self.assertEqual(result.failed, 0)
            self.assertEqual(second_node.image, alternative)
            self.assertEqual(tuple(second_node.image.size), (8, 4))
            self.assertEqual(len(other_settings.records), 1)
            self.assertEqual(other_settings.records[0].original, original)
            self.assertEqual(other_settings.records[0].alternative, alternative)
            result = addon.switch_scene(bpy.context, "ORIGINAL")
            self.assertEqual(result.failed, 0)
        self.assertEqual(first_node.image, alternative)
        self.assertEqual(second_node.image, original)
        self.assertFalse((self.path / "other-output").exists())

    def test_memory_summary_uses_the_original_snapshot_without_reloading_its_pixels(self):
        from texture_resolution import _core

        image = self.image()
        image.file_format = "PNG"
        image.filepath_raw = str(self.path / "snapshot-source.png")
        image.save()
        original = bpy.data.images.load(image.filepath, check_existing=False)
        original.colorspace_settings.name = "Non-Color"
        _, _, node = self.material_node(original)
        self.setup()
        baseline = addon.memory_estimates(self.scene)
        original.buffers_free()
        Path(original.filepath).unlink()
        estimate = _core.estimate_image_memory

        def only_current_image(image, *args, **kwargs):
            self.assertNotEqual(image, original, "The summary should use the tracked source snapshot")
            return estimate(image, *args, **kwargs)

        with mock.patch.object(_core, "estimate_image_memory", side_effect=only_current_image):
            totals = addon.memory_estimates(self.scene)
        self.assertEqual(totals, baseline)
        self.assertNotEqual(node.image, original)
        self.assertFalse(original.has_data)

    def test_memory_totals_deduplicate_images_and_count_mixed_resolution_usage(self):
        original = self.image()
        _, _, first = self.material_node(original)
        _, _, second = self.material_node(original)
        self.setup()
        half = first.image
        full_estimate = addon.estimate_image_memory(original)
        half_estimate = addon.estimate_image_memory(half)
        totals = addon.memory_estimates(self.scene)
        self.assertEqual(totals["count"], 1)
        self.assertEqual(totals["current_mode"], "HALF")
        self.assertEqual(totals["original_ram"], full_estimate.ram_bytes)
        self.assertEqual(totals["current_ram"], half_estimate.ram_bytes)
        self.assertEqual(totals["current_vram"], half_estimate.vram_bytes)
        second.image = original
        totals = addon.memory_estimates(self.scene)
        self.assertEqual(totals["count"], 1)
        self.assertEqual(totals["current_mode"], "MIXED")
        self.assertEqual(totals["current_ram"], full_estimate.ram_bytes + half_estimate.ram_bytes)
        self.assertEqual(totals["current_vram"], full_estimate.vram_bytes + half_estimate.vram_bytes)

    def test_tracking_survives_save_reopen_and_can_restore_original(self):
        original = self.image("Saved Original")
        _, material, node = self.material_node(original)
        node.name = "Tracked Node"
        material_name = material.name
        self.setup()
        alternative_name = node.image.name
        blend = self.path / "tracked.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        self.scene = bpy.context.scene
        self.settings = getattr(self.scene, addon.SCENE_PROPERTY)
        node = bpy.data.materials[material_name].node_tree.nodes["Tracked Node"]
        self.assertEqual(node.image.name, alternative_name)
        self.assertEqual(len(self.settings.records), 1)
        self.switch("ORIGINAL")
        self.assertEqual(node.image.name, "Saved Original")
        self.assertEqual(tuple(node.image.size), (16, 8))
        self.switch("HALF")
        self.assertEqual(node.image.name, alternative_name)

    def test_generated_live_pixels_survive_native_half_file_round_trip(self):
        for floating in (False, True):
            with self.subTest(floating=floating):
                source = self.image(f"Generated {floating}", floating=floating)
                before = self.image_state(source)
                alternative = create_half_image(source, self.path / str(floating))
                self.assertEqual(tuple(alternative.size), (8, 4))
                self.assertEqual(alternative.is_float, floating)
                tolerance = 1e-6 if floating else 1 / 255 + 1e-6
                for actual, expected in zip(self.pixels(alternative)[:4], self.pixels(source)[:4]):
                    self.assertAlmostEqual(actual, expected, delta=tolerance)
                self.assertEqual(self.image_state(source), before)
                self.assertTrue(Path(alternative.filepath).is_file())

    def test_dirty_file_uses_live_pixels_without_overwriting_source_bytes(self):
        source = self.image()
        source.file_format = "PNG"
        source.filepath_raw = str(self.path / "original.png")
        source.save()
        source_path = Path(source.filepath)
        source = bpy.data.images.load(str(source_path), check_existing=False)
        source.colorspace_settings.name = "Non-Color"
        self.assertEqual(source.source, "FILE")
        source_bytes = source_path.read_bytes()
        source.pixels.foreach_set(array("f", (0.7, 0.1, 0.3, 1.0)) * 128)
        source.update()
        self.assertTrue(source.is_dirty)
        before = self.image_state(source)
        alternative = create_half_image(source, self.path / "half")
        self.assertEqual(source_path.read_bytes(), source_bytes)
        self.assertEqual(self.image_state(source), before)
        for actual, expected in zip(self.pixels(alternative)[:4], self.pixels(source)[:4]):
            self.assertAlmostEqual(actual, expected, delta=1 / 255 + 1e-6)
        self.assertNotEqual(Path(alternative.filepath), source_path)

    def test_packed_image_preserves_packed_bytes_and_supports_missing_source_file(self):
        source = self.image()
        source.file_format = "PNG"
        source.filepath_raw = str(self.path / "packed-source.png")
        source.save()
        source.pack()
        source_bytes = source.packed_file.data
        Path(source.filepath).unlink()
        before = self.image_state(source)
        alternative = create_half_image(source, self.path / "half")
        self.assertEqual(tuple(alternative.size), (8, 4))
        self.assertEqual(source.packed_file.data, source_bytes)
        self.assertEqual(self.image_state(source), before)

    def test_native_resampling_preserves_distinct_texture_regions(self):
        source = self.image("Quadrants", (4, 4), floating=True)
        colors = ((1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0),
                  (0.0, 0.0, 1.0, 1.0), (0.7, 0.4, 0.2, 0.8))
        pixels = array("f")
        for y in range(4):
            for x in range(4):
                pixels.extend(colors[(y // 2) * 2 + x // 2])
        source.pixels.foreach_set(pixels)
        before = self.image_state(source)
        alternative = create_half_image(source, self.path / "half")
        self.assertEqual(tuple(alternative.size), (2, 2))
        for actual, expected in zip(self.pixels(alternative), sum(colors, ())):
            self.assertAlmostEqual(actual, expected, delta=0.01)
        self.assertEqual(self.image_state(source), before)

    def test_high_bit_depth_color_is_not_converted_twice_or_reduced_to_eight_bits(self):
        def chunk(kind, payload):
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

        row = b"\0" + struct.pack(">HHHH", 13107, 26214, 39321, 52428) * 4
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 16, 6, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(row * 4)) + chunk(b"IEND", b""))
        path = self.path / "sixteen-bit.png"
        path.write_bytes(png)
        for space in ("sRGB", "Non-Color"):
            with self.subTest(space=space):
                source = bpy.data.images.load(str(path), check_existing=False)
                source.colorspace_settings.name = space
                source.alpha_mode = "STRAIGHT"
                self.assertTrue(source.is_float)
                before = self.image_state(source)
                alternative = create_half_image(source, self.path / "half")
                self.assertTrue(alternative.is_float)
                self.assertEqual(alternative.alpha_mode, "PREMUL")
                self.assertEqual(alternative.colorspace_settings.is_data, space == "Non-Color")
                for actual, expected in zip(self.pixels(alternative)[:4], self.pixels(source)[:4]):
                    self.assertAlmostEqual(actual, expected, delta=1e-6)
                self.assertEqual(self.image_state(source), before)
        self.assertEqual(path.read_bytes(), png)

    def test_odd_dimensions_and_single_pixel_axis_remain_nonzero(self):
        for original_size, expected in (((11, 9), (5, 4)), ((9, 1), (4, 1))):
            with self.subTest(size=original_size):
                source = self.image(str(original_size), original_size)
                alternative = create_half_image(source, self.path / "half")
                self.assertEqual(tuple(alternative.size), expected)
                self.assertEqual(tuple(source.size), original_size)

    def test_hdr_values_and_channel_packed_alpha_keep_precision(self):
        color = (7.123456, 0.000321, 2.654321, 0.431234)
        source = self.image("HDR", floating=True, color=color, alpha="CHANNEL_PACKED")
        before = self.image_state(source)
        alternative = create_half_image(source, self.path / "half")
        self.assertTrue(alternative.is_float)
        self.assertEqual(alternative.file_format, "OPEN_EXR")
        self.assertEqual(alternative.alpha_mode, "CHANNEL_PACKED")
        self.assertTrue(alternative.colorspace_settings.is_data)
        for actual, expected in zip(self.pixels(alternative)[:4], self.pixels(source)[:4]):
            self.assertAlmostEqual(actual, expected, delta=1e-6)
        self.assertEqual(self.image_state(source), before)

    def test_unsupported_multiframe_and_missing_image_are_skipped(self):
        original = self.image()
        original.source = "SEQUENCE"
        supported, reason = supported_image(original)
        self.assertFalse(supported)
        self.assertIn("sequence", reason.lower())
        _, _, node = self.material_node(original)
        result = self.setup()
        self.assertEqual(result.processed, 0)
        self.assertGreater(result.skipped, 0)
        self.assertEqual(node.image, original)
        self.assertEqual(len(self.settings.records), 0)
        missing = bpy.data.images.new("Missing", width=1, height=1)
        missing.source = "FILE"
        missing.filepath = str(self.path / "does-not-exist.png")
        missing.reload()
        supported, reason = supported_image(missing)
        self.assertFalse(supported)
        self.assertTrue(reason)

    def test_failed_output_validation_removes_only_its_temporary_outputs(self):
        source = self.image()
        protected = self.path / "existing.png"
        protected.write_bytes(b"User data")
        before = set(bpy.data.images)
        original_stat = Path.stat

        def fail_written_output(path, *args, **kwargs):
            if path.suffix == ".png" and path != protected:
                raise RuntimeError("Injected output validation failure")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", fail_written_output):
            with self.assertRaisesRegex(RuntimeError, "Injected output validation failure"):
                create_half_image(source, self.path)
        self.assertEqual(set(bpy.data.images), before)
        self.assertEqual(list(self.path.iterdir()), [protected])
        self.assertEqual(protected.read_bytes(), b"User data")

    def test_disabling_during_setup_releases_modal_resources_and_keeps_completed_images(self):
        original_nodes = []
        for name in ("First", "Second"):
            image = self.image(name)
            _, _, node = self.material_node(image)
            original_nodes.append((image, node))
        iterator = addon.iter_setup(bpy.context)
        next(iterator)
        timer = object()
        wm = mock.Mock()
        owner = SimpleNamespace(
            _timer=timer, _wm=wm, _iterator=iterator,
            _scene_pointer=self.scene.as_pointer(), _cleaned=False, _cancelled=False,
        )
        cleanup = addon.HELIX_TEXTURES_OT_setup._cleanup
        owner._cleanup = lambda context: cleanup(owner, context)
        addon._ACTIVE_OPERATORS.append(owner)
        addon._RUNNING_SCENES.add(owner._scene_pointer)
        try:
            addon.unregister()
            self.assertTrue(owner._cancelled)
            self.assertTrue(owner._cleaned)
            self.assertIsNone(owner._timer)
            self.assertIsNone(owner._iterator)
            self.assertFalse(addon._ACTIVE_OPERATORS)
            self.assertFalse(addon._RUNNING_SCENES)
            wm.event_timer_remove.assert_called_once_with(timer)
            wm.progress_end.assert_called_once_with()
            owner._cleanup(bpy.context)
            wm.progress_end.assert_called_once_with()
            self.assertEqual(sum(node.image != image for image, node in original_nodes), 1)
        finally:
            addon.register()
        self.settings = getattr(self.scene, addon.SCENE_PROPERTY)
        self.assertEqual(len(self.settings.records), 1)
        self.switch("ORIGINAL")
        self.assertTrue(all(node.image == image for image, node in original_nodes))

    def test_partial_registration_failure_cleans_up_and_allows_retry(self):
        addon.unregister()
        register_class = bpy.utils.register_class
        calls = 0

        def fail_second(cls):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Injected registration failure")
            return register_class(cls)

        try:
            with mock.patch.object(bpy.utils, "register_class", side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "Injected registration failure"):
                    addon.register()
            self.assertFalse(hasattr(bpy.types.Scene, addon.SCENE_PROPERTY))
            addon.unregister()
        finally:
            addon.register()

    def test_registration_collision_does_not_delete_another_addons_property(self):
        addon.unregister()
        setattr(bpy.types.Scene, addon.SCENE_PROPERTY, bpy.props.BoolProperty(default=True))
        try:
            with self.assertRaises(RuntimeError):
                addon.register()
            addon.unregister()
            prop = bpy.types.Scene.bl_rna.properties[addon.SCENE_PROPERTY]
            self.assertEqual(prop.type, "BOOLEAN")
            self.assertTrue(prop.default)
        finally:
            delattr(bpy.types.Scene, addon.SCENE_PROPERTY)
            addon.register()

    def test_restricted_registration_and_repeat_disable_leave_no_scene_property(self):
        from _bpy_restrict_state import RestrictBlend

        addon.unregister()
        try:
            with RestrictBlend():
                self.assertFalse(hasattr(bpy.data, "scenes"))
                addon.register()
                self.assertTrue(hasattr(bpy.types.Scene, addon.SCENE_PROPERTY))
                addon.unregister()
            addon.unregister()
            self.assertFalse(hasattr(bpy.types.Scene, addon.SCENE_PROPERTY))
        finally:
            addon.register()


if __name__ == "__main__":
    unittest.main()
