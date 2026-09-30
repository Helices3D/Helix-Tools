# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression checks for elapsed-time mapping using the official bpy runtime."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy


PACKAGE = Path(__file__).resolve().parents[1] / "addons" / "jump_by_time"
sys.path.insert(0, str(PACKAGE.parent))
import jump_by_time as timeline


class TimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owns_registration = not hasattr(bpy.types.Scene, "jbt_input_mode")
        if cls.owns_registration:
            timeline.register()

    @classmethod
    def tearDownClass(cls):
        if cls.owns_registration:
            timeline.unregister()

    def setUp(self):
        self.scene = bpy.data.scenes.new("Helix Jump By Time Test")
        self.scene.frame_start = 10
        self.scene.frame_end = 120
        self.scene.jbt_offset = 0

    def tearDown(self):
        bpy.data.scenes.remove(self.scene)

    def operator(self, operator):
        with bpy.context.temp_override(scene=self.scene):
            return operator()

    def test_fractional_rate_and_frame_overflow_keep_every_frame(self):
        fps = 30 / 1.001
        self.assertEqual(timeline.compute_target_frame(30, 1, 30, fps), 90)
        self.assertEqual(timeline.compute_target_frame(30, 10, 299, fps), 629)
        self.assertEqual(timeline.compute_target_frame(30, 1, -30, fps), 30)
        self.scene.render.fps = 30
        self.scene.render.fps_base = 1.001
        self.assertAlmostEqual(timeline.effective_fps(self.scene), fps, places=5)

    def test_timestamp_accepts_editor_timestamps_and_rejects_invalid_fields(self):
        self.assertAlmostEqual(timeline.parse_timestamp("01:02:03.456"), 3723.456)
        self.assertAlmostEqual(timeline.parse_timestamp("02:03.456"), 123.456)
        self.assertAlmostEqual(timeline.parse_timestamp("123.456"), 123.456)
        self.assertAlmostEqual(timeline.parse_timestamp("-00:01.250"), -1.25)
        for invalid in ("", "1:60", "1:60:00", "00:00:01:12", "nan", "1e9", "1.5:02"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                timeline.parse_timestamp(invalid)

    def test_timestamp_and_fractional_override_jump_to_exact_result(self):
        self.scene.jbt_use_scene_fps = False
        self.scene.jbt_fps_override = 29.97
        self.scene.jbt_input_mode = "TIMESTAMP"
        self.scene.jbt_timestamp = "00:00:10.500"
        self.scene.jbt_offset = 145
        self.assertEqual(self.operator(bpy.ops.jbt.jump_to_time), {"FINISHED"})
        self.assertEqual(self.scene.frame_current, 460)

    def test_outside_range_policy_is_explicit(self):
        self.scene.jbt_seconds = 0
        self.scene.jbt_frames = 2
        self.scene.jbt_range_policy = "ALLOW"
        self.assertEqual(self.operator(bpy.ops.jbt.jump_to_time), {"FINISHED"})
        self.assertEqual(self.scene.frame_current, 2)
        self.scene.jbt_range_policy = "CLAMP"
        self.assertEqual(self.operator(bpy.ops.jbt.jump_to_time), {"FINISHED"})
        self.assertEqual(self.scene.frame_current, 10)
        self.scene.jbt_frames = 200
        self.assertEqual(self.operator(bpy.ops.jbt.jump_to_time), {"FINISHED"})
        self.assertEqual(self.scene.frame_current, 120)
        self.scene.jbt_range_policy = "REJECT"
        # ERROR reports become RuntimeError through bpy.ops; the frame must stay put.
        with self.assertRaises(RuntimeError):
            self.operator(bpy.ops.jbt.jump_to_time)
        self.assertEqual(self.scene.frame_current, 120)

    def test_negative_timestamp_can_jump_before_zero(self):
        self.scene.jbt_input_mode = "TIMESTAMP"
        self.scene.jbt_timestamp = "-00:00:02.000"
        self.scene.jbt_use_scene_fps = False
        self.scene.jbt_fps_override = 24
        self.assertEqual(self.operator(bpy.ops.jbt.jump_to_time), {"FINISHED"})
        self.assertEqual(self.scene.frame_current, -48)

    def test_alignment_maps_exact_current_frame_even_at_half_frame(self):
        self.scene.jbt_use_scene_fps = False
        self.scene.jbt_fps_override = 25
        self.scene.jbt_input_mode = "TIMESTAMP"
        self.scene.jbt_timestamp = "00:00:00.020"
        self.scene.frame_set(0)
        self.assertEqual(self.operator(bpy.ops.jbt.align_offset), {"FINISHED"})
        self.assertEqual(self.scene.jbt_offset, -1)
        self.assertEqual(timeline.target_frame(self.scene), 0)
        self.scene.jbt_timestamp = "00:01:02.400"
        self.scene.frame_set(1900)
        self.assertEqual(self.operator(bpy.ops.jbt.align_offset), {"FINISHED"})
        self.assertEqual(self.scene.jbt_offset, 340)
        self.assertEqual(timeline.target_frame(self.scene), 1900)

    def test_legacy_integer_fps_values_migrate_without_changing_settings(self):
        self.scene["jbt_fps_override"] = 48
        self.scene.jbt_offset = 321
        self.scene.jbt_seconds = 12
        timeline._migrate_fps_override()
        self.assertIsInstance(self.scene["jbt_fps_override"], float)
        self.assertEqual(self.scene.jbt_fps_override, 48.0)
        self.assertEqual(self.scene.jbt_offset, 321)
        self.assertEqual(self.scene.jbt_seconds, 12)
        self.scene.jbt_fps_override = 23.976
        timeline._migrate_fps_override()
        self.assertAlmostEqual(self.scene.jbt_fps_override, 23.976, places=5)

    def test_invalid_time_or_unsupported_frame_does_not_move_playhead(self):
        self.scene.frame_set(55)
        self.scene.jbt_input_mode = "TIMESTAMP"
        self.scene.jbt_timestamp = "bad timestamp"
        with self.assertRaises(RuntimeError):
            self.operator(bpy.ops.jbt.jump_to_time)
        self.assertEqual(self.scene.frame_current, 55)
        self.scene.jbt_timestamp = "999999999"
        with self.assertRaises(RuntimeError):
            self.operator(bpy.ops.jbt.jump_to_time)
        self.assertEqual(self.scene.frame_current, 55)

    def test_current_timestamp_formats_boundaries_and_negative_offsets(self):
        self.assertEqual(timeline.format_timestamp(3599.9996), "01:00:00.000")
        self.assertEqual(timeline.format_timestamp(-1.25), "-00:00:01.250")

    def test_collision_leaves_older_addon_operator_and_scene_schema_intact(self):
        timeline.unregister()

        class LegacyJump(bpy.types.Operator):
            bl_idname = "jbt.jump_to_time"
            bl_label = "Original Jump By Time"

            def execute(self, context):
                return {"FINISHED"}

        try:
            bpy.utils.register_class(LegacyJump)
            bpy.types.Scene.jbt_offset = bpy.props.IntProperty(default=99)
            with self.assertRaisesRegex(RuntimeError, "Disable the older or duplicate"):
                timeline.register()
            self.assertTrue(LegacyJump.is_registered)
            self.assertEqual(bpy.types.Scene.bl_rna.properties["jbt_offset"].default, 99)
            self.assertFalse(hasattr(bpy.types.Scene, "jbt_timestamp"))
            # A failed enable followed by disable must also preserve the old copy.
            timeline.unregister()
            self.assertTrue(LegacyJump.is_registered)
            self.assertTrue(hasattr(bpy.types.Scene, "jbt_offset"))
        finally:
            if hasattr(bpy.types.Scene, "jbt_offset"):
                del bpy.types.Scene.jbt_offset
            if LegacyJump.is_registered:
                bpy.utils.unregister_class(LegacyJump)
            timeline.register()

    def test_partial_registration_rolls_back_and_can_be_enabled_again(self):
        timeline.unregister()
        original_register = bpy.utils.register_class
        calls = 0

        def fail_on_second_class(cls):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Injected registration failure")
            original_register(cls)

        try:
            with patch.object(bpy.utils, "register_class", side_effect=fail_on_second_class):
                with self.assertRaisesRegex(RuntimeError, "Injected registration failure"):
                    timeline.register()
            self.assertFalse(timeline.JBT_OT_jump.is_registered)
            self.assertFalse(hasattr(bpy.types.Scene, "jbt_offset"))
            self.assertNotIn(timeline._migrate_fps_override, bpy.app.handlers.load_post)
            timeline.unregister()
        finally:
            timeline.register()


if __name__ == "__main__":
    unittest.main()
