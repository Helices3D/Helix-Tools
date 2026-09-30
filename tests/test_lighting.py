"""Exercise source sizes, durable baselines and Eevee controls in real Blender."""

from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

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
        self.assertEqual(addon.ALSC_OT_scene_quality.bl_label, "Switch to Suggested Render Preset")
        self.assertIn('UNDO', addon.ALSC_OT_scene_quality.bl_options)

    def test_render_preset_restores_first_baseline_after_repeated_apply_and_reload(self):
        self.scene.render.engine = 'CYCLES'
        self.scene.eevee.taa_render_samples = 37
        original = addon._render_current(self.scene)
        self.scene.render.resolution_x = 1337
        bpy.ops.alsc.eevee_scene_quality()
        self.settings.viewport_jitter = False
        bpy.ops.alsc.eevee_scene_quality()
        self.assertEqual(addon._render_snapshot(self.scene), original)
        self.scene.render.resolution_x = 1777
        with tempfile.TemporaryDirectory(prefix="helix-render-roundtrip-") as temp:
            path = str(Path(temp) / "render-preset.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
            self.scene = bpy.context.scene
            self.settings = self.scene.area_light_shadow_control
            self.assertEqual(addon._render_snapshot(self.scene), original)
            self.assertEqual(bpy.ops.alsc.restore_render_settings(), {'FINISHED'})
            self.assertEqual(addon._render_current(self.scene), original)
            self.assertEqual(self.scene.render.resolution_x, 1777)
            self.assertNotIn(addon.RENDER_BASELINE_KEY, self.scene)

    def test_render_preset_failure_rolls_back_without_creating_snapshot(self):
        self.scene.render.engine = 'CYCLES'
        original = addon._render_current(self.scene)
        apply_values = addon._set_render_values
        calls = []

        def partial_failure(scene, values):
            calls.append(values)
            if len(calls) == 1:
                scene.render.engine = 'BLENDER_EEVEE'
                raise ValueError("simulated failure after engine change")
            return apply_values(scene, values)

        with mock.patch.object(addon, '_set_render_values', side_effect=partial_failure):
            with self.assertRaisesRegex(ValueError, "simulated failure"):
                addon._apply_render_preset(self.scene)
        self.assertEqual(addon._render_current(self.scene), original)
        self.assertNotIn(addon.RENDER_BASELINE_KEY, self.scene)

    def test_render_preset_corrupt_snapshot_is_preserved_without_changes(self):
        self.scene[addon.RENDER_BASELINE_KEY] = {'render.engine': 'CYCLES'}
        original = addon._render_current(self.scene)
        with self.assertRaisesRegex(ValueError, "snapshot is invalid"):
            addon._apply_render_preset(self.scene)
        self.assertEqual(addon._render_current(self.scene), original)
        self.assertEqual(dict(self.scene[addon.RENDER_BASELINE_KEY]), {'render.engine': 'CYCLES'})

    def test_render_restore_failure_keeps_preset_and_snapshot(self):
        self.scene.render.engine = 'CYCLES'
        addon._apply_render_preset(self.scene)
        baseline = addon._render_snapshot(self.scene)
        current = addon._render_current(self.scene)
        set_values = addon._set_render_values
        calls = []

        def fail_once(scene, values):
            calls.append(values)
            if len(calls) == 1:
                scene.render.engine = 'CYCLES'
                raise ValueError("simulated restore failure")
            return set_values(scene, values)

        with mock.patch.object(addon, '_set_render_values', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, "restore failure"):
                addon._restore_render_preset(self.scene)
        self.assertEqual(addon._render_current(self.scene), current)
        self.assertEqual(addon._render_snapshot(self.scene), baseline)

    def isolated_startup_check(self, script):
        executable = bpy.app.binary_path or sys.executable
        with tempfile.TemporaryDirectory(prefix="helix-startup-regression-") as temp:
            root = Path(temp)
            environment = os.environ.copy()
            environment.update({
                'BLENDER_USER_RESOURCES': str(root / 'resources'),
                'BLENDER_USER_CONFIG': str(root / 'config'),
                'XDG_CONFIG_HOME': str(root / 'xdg-config'),
                'XDG_CACHE_HOME': str(root / 'xdg-cache'),
            })
            source = root / 'check.py'
            preamble = (
                "import bpy, json, sys\nfrom pathlib import Path\nfrom unittest import mock\n"
                "assert bpy.app.version == (5, 2, 2), bpy.app.version_string\n"
                f"sys.path.insert(0, {str(Path(addon.__file__).parents[1])!r})\n"
                "import area_light_shadow_control as addon\naddon.register()\n"
            )
            source.write_text(preamble + textwrap.dedent(script), encoding='utf-8')
            command = ([executable, '--background', '--factory-startup', '--disable-autoexec',
                        '--threads', '2', '--python-exit-code', '1', '--python', str(source)]
                       if bpy.app.binary_path else [executable, str(source)])
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_startup_backup_restore_and_preferences_preservation(self):
        self.isolated_startup_check("""
            scene = bpy.context.scene
            scene.render.engine = 'CYCLES'
            bpy.ops.wm.save_homefile()
            config, startup, receipt = addon._startup_paths()
            original = startup.read_bytes()
            preferences = config / 'userpref.blend'
            preferences.write_bytes(b'untouched preferences sentinel')
            original_filepath = bpy.data.filepath
            backup = Path(addon._save_startup(bpy.context))
            assert backup.read_bytes() == original
            assert startup.read_bytes() != original
            assert preferences.read_bytes() == b'untouched preferences sentinel'
            assert bpy.data.filepath == original_filepath
            assert receipt.is_file()
            addon._restore_startup()
            assert startup.read_bytes() == original
            assert backup.read_bytes() == original
            assert not receipt.exists()
            assert scene.render.engine == 'BLENDER_EEVEE'
            assert preferences.read_bytes() == b'untouched preferences sentinel'
        """)

    def test_startup_repeated_saves_keep_original_factory_restore_point(self):
        self.isolated_startup_check("""
            config, startup, receipt = addon._startup_paths(create=True)
            assert not startup.exists()
            assert addon._save_startup(bpy.context) == ''
            assert json.loads(receipt.read_text())['previous'] == 'factory'
            first = startup.read_bytes()
            backup = Path(addon._save_startup(bpy.context))
            assert backup.read_bytes() == first
            assert json.loads(receipt.read_text())['previous'] == 'factory'
            addon._restore_startup()
            assert not startup.exists()
            assert backup.exists()
            assert not receipt.exists()
        """)

    def test_startup_repeated_saves_keep_original_custom_restore_point(self):
        self.isolated_startup_check("""
            bpy.context.scene.render.engine = 'CYCLES'
            bpy.ops.wm.save_homefile()
            config, startup, receipt = addon._startup_paths()
            original = startup.read_bytes()
            first_backup = Path(addon._save_startup(bpy.context))
            second_backup = Path(addon._save_startup(bpy.context))
            assert first_backup != second_backup
            assert first_backup.read_bytes() == original
            assert json.loads(receipt.read_text())['backup'] == first_backup.name
            addon._restore_startup()
            assert startup.read_bytes() == original
            assert first_backup.exists() and second_backup.exists()
        """)

    def test_startup_changed_after_save_is_not_removed_or_overwritten(self):
        self.isolated_startup_check("""
            addon._save_startup(bpy.context)
            config, startup, receipt = addon._startup_paths()
            startup.write_bytes(b'a newer user-created startup')
            try:
                addon._restore_startup()
            except ValueError as exc:
                assert 'changed after' in str(exc)
            else:
                raise AssertionError('Modified startup was not protected')
            assert startup.read_bytes() == b'a newer user-created startup'
            assert receipt.exists()
        """)

    def test_startup_save_failure_restores_original_bytes_and_scene(self):
        self.isolated_startup_check("""
            bpy.context.scene.render.engine = 'CYCLES'
            bpy.ops.wm.save_homefile()
            config, startup, receipt = addon._startup_paths()
            original = startup.read_bytes()
            original_render = addon._render_current(bpy.context.scene)
            def failed_save():
                startup.write_bytes(b'partially written startup')
                return {'CANCELLED'}
            with mock.patch.object(addon, '_write_homefile', side_effect=failed_save):
                try:
                    addon._save_startup(bpy.context)
                except RuntimeError as exc:
                    assert 'did not successfully save' in str(exc)
                else:
                    raise AssertionError('Failed save was reported successful')
            assert startup.read_bytes() == original
            assert addon._render_current(bpy.context.scene) == original_render
            assert addon.RENDER_BASELINE_KEY not in bpy.context.scene
            assert not receipt.exists()
            backups = list(config.glob('startup.helix-tools-backup-*.blend'))
            assert len(backups) == 1 and backups[0].read_bytes() == original
        """)

    def test_startup_receipt_failure_restores_original_and_previous_record(self):
        self.isolated_startup_check("""
            bpy.ops.wm.save_homefile()
            addon._save_startup(bpy.context)
            config, startup, receipt = addon._startup_paths()
            original, original_receipt = startup.read_bytes(), receipt.read_bytes()
            original_render = addon._render_current(bpy.context.scene)
            write = addon._atomic_write
            failures = []
            def fail_receipt_once(path, data):
                if path == receipt and not failures:
                    failures.append(True)
                    raise OSError('simulated receipt write failure')
                return write(path, data)
            with mock.patch.object(addon, '_atomic_write', side_effect=fail_receipt_once):
                try:
                    addon._save_startup(bpy.context)
                except RuntimeError as exc:
                    assert 'receipt write failure' in str(exc)
                else:
                    raise AssertionError('Receipt failure was not detected')
            assert startup.read_bytes() == original
            assert receipt.read_bytes() == original_receipt
            assert addon._render_current(bpy.context.scene) == original_render
        """)

    def test_startup_failed_factory_save_removes_only_created_file(self):
        self.isolated_startup_check("""
            config, startup, receipt = addon._startup_paths(create=True)
            original_render = addon._render_current(bpy.context.scene)
            def failed_save():
                startup.write_bytes(b'partially written new startup')
                return {'CANCELLED'}
            with mock.patch.object(addon, '_write_homefile', side_effect=failed_save):
                try:
                    addon._save_startup(bpy.context)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Failed save was reported successful')
            assert not startup.exists() and not receipt.exists()
            assert not list(config.glob('startup.helix-tools-backup-*.blend'))
            assert addon._render_current(bpy.context.scene) == original_render
            assert addon.RENDER_BASELINE_KEY not in bpy.context.scene
        """)

    def test_startup_changed_backup_is_not_restored(self):
        self.isolated_startup_check("""
            bpy.ops.wm.save_homefile()
            backup = Path(addon._save_startup(bpy.context))
            config, startup, receipt = addon._startup_paths()
            saved = startup.read_bytes()
            backup.write_bytes(b'altered backup')
            try:
                addon._restore_startup()
            except ValueError as exc:
                assert 'backup was changed' in str(exc)
            else:
                raise AssertionError('Modified backup was not protected')
            assert startup.read_bytes() == saved and receipt.exists()
        """)

    def test_startup_symlink_is_rejected_before_writing(self):
        self.isolated_startup_check("""
            config, startup, receipt = addon._startup_paths(create=True)
            target = config / 'unrelated.blend'
            target.write_bytes(b'unrelated file')
            startup.symlink_to(target)
            with mock.patch.object(addon, '_write_homefile') as save:
                try:
                    addon._save_startup(bpy.context)
                except ValueError as exc:
                    assert 'nonregular file' in str(exc)
                else:
                    raise AssertionError('Startup symlink was not rejected')
                save.assert_not_called()
            assert target.read_bytes() == b'unrelated file'
            assert startup.is_symlink()
        """)

    def test_startup_malformed_receipts_cancel_before_any_changes(self):
        self.isolated_startup_check("""
            config, startup, receipt = addon._startup_paths(create=True)
            startup.write_bytes(b'original startup to preserve')
            original_render = addon._render_current(bpy.context.scene)
            for value in ([], None, {}, {'version': 1, 'previous': 'factory'}, {'version': 1, 'previous': []}):
                record_bytes = json.dumps(value).encode('utf-8')
                receipt.write_bytes(record_bytes)
                with mock.patch.object(addon, '_write_homefile') as save:
                    operator = mock.Mock()
                    assert addon.ALSC_OT_save_startup.execute(operator, bpy.context) == {'CANCELLED'}
                    assert addon.ALSC_OT_restore_startup.execute(operator, bpy.context) == {'CANCELLED'}
                    save.assert_not_called()
                assert startup.read_bytes() == b'original startup to preserve'
                assert receipt.read_bytes() == record_bytes
                assert addon._render_current(bpy.context.scene) == original_render
                assert addon.RENDER_BASELINE_KEY not in bpy.context.scene
                assert not list(config.glob('startup.helix-tools-backup-*.blend'))
        """)

    def test_startup_application_template_uses_separate_backup_and_restore(self):
        self.isolated_startup_check("""
            base = Path(bpy.utils.user_resource('CONFIG', create=True))
            default_startup = base / 'startup.blend'
            default_startup.write_bytes(b'default startup must remain untouched')
            bpy.ops.wm.read_factory_settings(app_template='2D_Animation', use_empty=True)
            assert bpy.context.preferences.app_template == '2D_Animation'
            bpy.ops.wm.save_homefile()
            config, startup, receipt = addon._startup_paths()
            assert config == base / '2D_Animation'
            original = startup.read_bytes()
            backup = Path(addon._save_startup(bpy.context))
            assert backup.parent == config and backup.read_bytes() == original
            addon._restore_startup()
            assert startup.read_bytes() == original
            assert default_startup.read_bytes() == b'default startup must remain untouched'
        """)

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
