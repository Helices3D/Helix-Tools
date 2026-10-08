"""Draw every sidebar with native panel bodies open, closed, or selectively closed.

Scene data and registered RNA are real Blender data. The small layout double
models only UI construction; native interactive redraw is checked separately.
"""

import importlib
import tempfile
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy

from _hair_fixtures import make_garment, make_hair, select_objects
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_release_install import texture_fixture


MODULE_NAMES = (
    "jump_by_time", "smart_empty", "camera_timeline_culler",
    "area_light_shadow_control", "hair_contact_culler", "cloth_cache_manager", "texture_resolution",
)

# These are user-facing controls available before the UI refresh (5162883).
# Their placement and wording may change, but each capability must remain
# accessible by opening its section in the appropriate scene/context.
BASELINE_CONTROLS = {
    "jump_by_time": (
        {"jbt_input_mode", "jbt_timestamp", "jbt_seconds", "jbt_frames", "jbt_offset",
         "jbt_use_scene_fps", "jbt_fps_override", "jbt_range_policy"},
        {"jbt.jump_to_time", "jbt.align_offset"},
    ),
    "smart_empty": (
        {"bone_point", "display_type", "empty_size", "name_suffix", "show_name",
         "show_in_front", "use_local_orientation", "rig", "list_scope"},
        {"object.add_smart_empty_baked", "object.smart_empty_use_active_rig",
         "object.smart_empty_rig_visibility", "object.smart_empty_select"},
    ),
    "camera_timeline_culler": (
        {"scope", "tolerance", "substeps"},
        {"object.camera_cull_timeline", "object.camera_cull_toggle", "object.camera_cull_restore"},
    ),
    "area_light_shadow_control": (
        {"scope", "collection", "size_light_types", "size_reduction", "minimum_size",
         "shadow_preset", "shadow_filter", "shadow_resolution", "absolute_resolution",
         "shadow_jitter", "shadow_overblur", "shadow_scope", "shadow_casting", "viewport_jitter"},
        {"alsc.collection_add", "alsc.collection_remove", "alsc.enable_eevee_shadows",
         "alsc.refresh_sizes", "alsc.status_details", "alsc.capture_sizes", "alsc.restore_sizes",
         "alsc.apply_shadows", "alsc.eevee_scene_quality", "alsc.restore_render_settings",
         "alsc.save_suggested_startup", "alsc.restore_previous_startup"},
    ),
    "hair_contact_culler": (
        {"viewport", "render", "root_mode", "preview_original", "mode", "low_percent", "allowance"},
        {"helix.hair_cull_help", "helix.hair_cull_build", "helix.hair_cull_remove_item",
         "helix.hair_cull_add_items", "helix.hair_cull_validate", "helix.hair_cull_remove"},
    ),
    "cloth_cache_manager": (
        {"cloth_tool_selected", "show_viewport", "show_render"},
        {"cloth_manager.bake_from_cache", "cloth_manager.reset_bakes"},
    ),
    "texture_resolution": (
        {"threshold", "include_shared"},
        {"helix_textures.setup", "helix_textures.switch_resolution", "helix_textures.refresh_statistics"},
    ),
}


def _freeze(value):
    if isinstance(value, bpy.types.ID):
        return (value.bl_rna.identifier, value.name_full, value.as_pointer())
    if isinstance(value, bpy.types.PropertyGroup):
        return _rna_values(value)
    if hasattr(value, "to_dict"):
        return _freeze(value.to_dict())
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (str, bool, int, float, type(None))):
        return value
    return tuple(_freeze(item) for item in value)


def _rna_values(group):
    return tuple(
        (prop.identifier, _freeze(getattr(group, prop.identifier)))
        for prop in group.bl_rna.properties if prop.identifier != "rna_type"
    )


def _custom_properties(owner):
    return tuple(sorted((key, _freeze(owner[key])) for key in owner.keys()))


class LayoutTrace:
    """A closed panel supplies no body, exactly as UILayout.panel does."""

    def __init__(self, mode="open", closed_id=None):
        self.mode = mode
        self.closed_id = closed_id
        self.panels = []
        self.headers = []
        self.calls = {}
        self.root_calls = []
        self.controls = []
        self.layout = Layout(self)


class Layout:
    def __init__(self, trace, *, section=None, header=False, root=True, parent=None):
        self.trace = trace
        self.section = section
        self.header = header
        self.root = root
        self.parent = parent
        self._enabled = self._active = True

    @property
    def enabled(self):
        return self._enabled and (self.parent is None or self.parent.enabled)

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)

    @property
    def active(self):
        return self._active and (self.parent is None or self.parent.active)

    @active.setter
    def active(self, value):
        self._active = bool(value)

    def _record(self, method, *arguments):
        entry = (method, *arguments)
        if self.header:
            if method != "label":
                raise AssertionError("Section headers should contain their label")
            self.trace.headers.append((self.section, *arguments))
        elif self.section is None:
            self.trace.root_calls.append(entry)
        else:
            self.trace.calls[self.section].append(entry)

    def _child(self, method):
        self._record(method)
        return Layout(self.trace, section=self.section, root=False, parent=self)

    def _control(self, kind, identifier, *, data=None, properties=None):
        self.trace.controls.append({
            "kind": kind, "identifier": identifier, "data": data,
            "properties": properties, "section": self.section,
            "enabled": self.enabled, "active": self.active,
        })

    def panel(self, identifier, *, default_closed=False):
        if not self.root:
            raise AssertionError("Native sections require the full-width root layout")
        if identifier in self.trace.calls:
            raise AssertionError(f"Duplicate native panel ID: {identifier}")
        self.trace.panels.append((identifier, default_closed))
        self.trace.calls[identifier] = []
        closed = (
            self.trace.mode == "closed"
            or identifier == self.trace.closed_id
            or self.trace.mode == "defaults" and default_closed
        )
        header = Layout(self.trace, section=identifier, header=True, root=False, parent=self)
        body = None if closed else Layout(self.trace, section=identifier, root=False, parent=self)
        return header, body

    def box(self):
        return self._child("box")

    def column(self, **kwargs):
        return self._child("column")

    def row(self, **kwargs):
        return self._child("row")

    def split(self, **kwargs):
        return self._child("split")

    def label(self, *, text="", icon="NONE", **kwargs):
        self._record("label", text, icon)

    def prop(self, data, property_name, **kwargs):
        # Do not silently accept mistyped fields just because this is a double.
        if data.bl_rna.properties.get(property_name) is None:
            raise AssertionError(f"Unknown RNA field: {property_name}")
        self._record("prop", property_name, _freeze(getattr(data, property_name)))
        self._control("prop", property_name, data=data)

    def operator(self, identifier, **kwargs):
        namespace, name = identifier.split(".", 1)
        operation = getattr(getattr(bpy.ops, namespace), name)
        operation.get_rna_type()
        self._record("operator", identifier)
        properties = SimpleNamespace()
        self._control("operator", identifier, properties=properties)
        self.trace.controls[-1]["available"] = self.enabled and operation.poll()
        return properties

    def separator(self, **kwargs):
        self._record("separator")

    def template_list(self, list_type, list_id, data, field, active_data, active_field, **kwargs):
        self._record("template_list", list_type, len(getattr(data, field)),
                     getattr(active_data, active_field))
        list_class = bpy.types.UIList.bl_rna_get_subclass_py(list_type, None)
        if list_class is None:
            raise AssertionError(f"Unknown UIList: {list_type}")
        # Native list callbacks own the controls inside a row. Exercise their
        # default filtering as well, instead of considering a template call
        # sufficient evidence that each item's controls are reachable.
        ui_list = SimpleNamespace(
            filter_name="", use_filter_invert=False, use_filter_sort_alpha=False, layout_type="DEFAULT",
            bitflag_filter_item=1 << 30, bitflag_item_never_show=1 << 16,
        )
        flags = []
        if "filter_items" in list_class.__dict__:
            flags, _ = list_class.filter_items(ui_list, bpy.context, data, field)
        for index, item in enumerate(getattr(data, field)):
            if flags and (flags[index] & ui_list.bitflag_item_never_show
                          or not flags[index] & ui_list.bitflag_filter_item):
                continue
            list_class.draw_item(ui_list, bpy.context, self, data, item, 0,
                                 active_data, active_field, index)


class SidebarDrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = {name: importlib.import_module(name) for name in MODULE_NAMES}
        cls.enabled = []
        try:
            for module in cls.modules.values():
                module.register()
                cls.enabled.append(module)
        except Exception:
            cls.tearDownClass()
            raise
        cls.panels = {
            name: tuple(panel for panel in vars(module).values()
                        if isinstance(panel, type) and panel.__module__ == name
                        and issubclass(panel, bpy.types.Panel)
                        and getattr(panel, "bl_space_type", None) == "VIEW_3D")
            for name, module in cls.modules.items()
        }

    @classmethod
    def tearDownClass(cls):
        while cls.enabled:
            cls.enabled.pop().unregister()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.scene.frame_start = self.scene.frame_end = 1
        self.source = self.garment = self.metadata = None

    def mesh(self, name, location=(0, 0, 0)):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new(name, mesh)
        self.scene.collection.objects.link(obj)
        obj.location = location
        return obj

    def camera_results(self):
        camera = bpy.data.objects.new("Sidebar Camera", bpy.data.cameras.new("Sidebar Camera"))
        self.scene.collection.objects.link(camera)
        self.scene.camera = camera
        self.mesh("Outside Camera", (100, 0, -10))
        bpy.context.view_layer.update()
        self.assertEqual(bpy.ops.object.camera_cull_timeline(), {"FINISHED"})
        group = self.scene.camera_cull_settings.collection
        self.assertIsNotNone(group)
        self.assertEqual(len(group.objects), 1)
        return group

    def cloth(self):
        obj = self.mesh("Sidebar Cloth")
        modifier = obj.modifiers.new("Sidebar Cloth Simulation", "CLOTH")
        modifier.settings.quality = 1
        modifier.point_cache.frame_start, modifier.point_cache.frame_end = 1, 4
        obj.cloth_tool_selected = True
        return obj

    def hair(self, *, built=False):
        self.source = make_hair("Sidebar Hair")
        self.garment = make_garment("Sidebar Garment")
        select_objects(self.source, self.garment)
        if built:
            addon = self.modules["hair_contact_culler"]
            self.metadata = addon.create_bundle(self.source, [self.garment])
            addon.compile_bundle(self.metadata)
            self.assertTrue(self.metadata.helix_hair_cull.valid)

    def light(self, name="Sidebar Light", kind="AREA"):
        obj = bpy.data.objects.new(name, bpy.data.lights.new(name, kind))
        self.scene.collection.objects.link(obj)
        return obj

    def populated_scene(self, *, built_hair=True):
        self.camera_results()
        cloth = self.cloth()
        select_objects(cloth)
        self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {"FINISHED"})
        self.light()
        self.light("Sidebar Spot", "SPOT")
        self.hair(built=built_hair)
        texture_directory = tempfile.TemporaryDirectory(prefix="helix-sidebar-textures-")
        self.addCleanup(texture_directory.cleanup)
        texture_fixture(self.scene, texture_directory.name, "Sidebar Texture")
        self.assertEqual(bpy.ops.helix_textures.setup(), {"FINISHED"})

    def snapshot(self):
        scene = self.scene
        groups = tuple(_rna_values(getattr(scene, name)) for name in (
            "helix_smart_empty_settings", "camera_cull_settings", "area_light_shadow_control",
            "helix_texture_resolution",
        ))
        objects = []
        for obj in bpy.data.objects:
            modifiers = []
            for modifier in obj.modifiers:
                state = (modifier.name, modifier.type, modifier.show_viewport,
                         modifier.show_render)
                if modifier.type == "CLOTH":
                    cache = modifier.point_cache
                    state += (modifier.settings.quality, cache.frame_start, cache.frame_end,
                              cache.is_baked, cache.is_outdated, cache.info)
                elif modifier.type == "NODES":
                    state += (_freeze(modifier.node_group),)
                modifiers.append(state)
            objects.append((
                _freeze(obj), _freeze(obj.data), _freeze(obj.matrix_world),
                tuple(_freeze(collection) for collection in obj.users_collection),
                obj.hide_viewport, obj.hide_render, obj.hide_select,
                obj.hide_get() if obj.name in bpy.context.view_layer.objects else None,
                obj.select_get(), obj.cloth_tool_selected, _custom_properties(obj),
                _rna_values(obj.helix_smart_empty), _rna_values(obj.helix_hair_cull),
                tuple(modifiers),
            ))
        geometry = tuple(
            (mesh.name, tuple(tuple(vertex.co) for vertex in mesh.vertices),
             tuple(tuple(face.vertices) for face in mesh.polygons), _custom_properties(mesh))
            for mesh in bpy.data.meshes
        )
        hair_geometry = tuple(
            (curves.name, tuple((attribute.name, attribute.data_type, attribute.domain,
                                self.modules["hair_contact_culler"]._values(attribute))
                               for attribute in curves.attributes))
            for curves in bpy.data.hair_curves
        )
        lights = tuple((light.name, light.type, getattr(light, "size", None),
                        getattr(light, "size_y", None), getattr(light, "shadow_soft_size", None),
                        light.energy, _custom_properties(light))
                       for light in bpy.data.lights)
        return (
            scene.frame_current, scene.frame_subframe, scene.frame_start, scene.frame_end,
            scene.render.engine, _freeze(scene.camera), _custom_properties(scene), groups,
            tuple((prop.identifier, _freeze(getattr(scene, prop.identifier)))
                  for prop in scene.bl_rna.properties if prop.identifier.startswith("jbt_")),
            bpy.context.mode, _freeze(bpy.context.view_layer.objects.active),
            tuple(objects), geometry, hair_geometry, lights,
            tuple((collection.name, collection.hide_viewport, collection.hide_render,
                   tuple(_freeze(obj) for obj in collection.objects), _custom_properties(collection))
                  for collection in bpy.data.collections),
            tuple((group.name, tuple(node.bl_idname for node in group.nodes), len(group.links))
                  for group in bpy.data.node_groups),
        )

    def draw(self, panel, *, mode="open", closed_id=None):
        bpy.context.view_layer.update()
        before = self.snapshot()
        trace = LayoutTrace(mode=mode, closed_id=closed_id)
        panel.draw(SimpleNamespace(layout=trace.layout), bpy.context)
        self.assertEqual(self.snapshot(), before, f"Drawing {panel.__name__} changed scene data")
        return trace

    def assert_closed_draws(self, panel):
        opened = self.draw(panel)
        self.assertTrue(opened.panels, panel.__name__)
        self.assertEqual(len(opened.panels), len(opened.headers))
        for identifier, _ in opened.panels:
            self.assertTrue(opened.calls[identifier], f"No controls in open section {identifier}")
        closed = self.draw(panel, mode="closed")
        self.assertEqual(closed.headers, opened.headers)
        self.assertEqual(closed.panels, opened.panels)
        self.assertEqual(closed.root_calls, opened.root_calls)
        self.assertTrue(all(not calls for calls in closed.calls.values()))
        for identifier, _ in opened.panels:
            with self.subTest(panel=panel.__name__, closed=identifier):
                selected = self.draw(panel, closed_id=identifier)
                self.assertEqual(selected.headers, opened.headers)
                self.assertEqual(selected.panels, opened.panels)
                self.assertEqual(selected.root_calls, opened.root_calls)
                for other, calls in opened.calls.items():
                    self.assertEqual(selected.calls[other], [] if other == identifier else calls)
        return opened

    def test_all_registered_sidebars_keep_later_headers_when_bodies_close(self):
        self.populated_scene()
        for package, panels in self.panels.items():
            self.assertTrue(panels, package)
            for panel in panels:
                with self.subTest(package=package, panel=panel.__name__):
                    self.assert_closed_draws(panel)

    def test_texture_redraws_use_statistics_snapshot_without_scene_scans(self):
        from texture_resolution import _core, _scan

        directory = tempfile.TemporaryDirectory(prefix="helix-texture-redraw-")
        self.addCleanup(directory.cleanup)
        texture_fixture(self.scene, directory.name, "Redraw Texture")
        self.assertEqual(bpy.ops.helix_textures.setup(), {"FINISHED"})
        addon = self.modules["texture_resolution"]
        self.assertTrue(addon.statistics_snapshot(self.scene)["available"])
        panel = self.panels["texture_resolution"][0]
        fail = AssertionError("Opening or redrawing texture panels must not scan scene references")
        with mock.patch.object(_core, "scan_scene", side_effect=fail):
            with mock.patch.object(_scan, "scan_scene", side_effect=fail):
                with mock.patch.object(addon, "memory_estimates", side_effect=fail):
                    with mock.patch.object(_core, "memory_estimates", side_effect=fail):
                        for _ in range(10):
                            self.draw(panel)
                            self.draw(panel, mode="closed")
                            self.draw(panel, mode="defaults")

    def test_texture_missing_statistics_can_be_refreshed_without_setup(self):
        addon = self.modules["texture_resolution"]
        self.assertFalse(addon.statistics_snapshot(self.scene)["available"])
        panel = self.panels["texture_resolution"][0]
        trace = self.draw(panel)
        refresh = [control for control in trace.controls
                   if control["identifier"] == "helix_textures.refresh_statistics"]
        self.assertEqual(len(refresh), 1)
        self.assertTrue(refresh[0]["available"])
        self.assertEqual(bpy.ops.helix_textures.refresh_statistics(), {"FINISHED"})
        stats = addon.statistics_snapshot(self.scene)
        self.assertTrue(stats["available"])
        self.assertEqual(stats["count"], 0)

    def test_defaults_and_section_ids_are_independent_between_packages(self):
        self.populated_scene()
        owners = {}
        for package, panels in self.panels.items():
            for panel in panels:
                trace = self.draw(panel, mode="defaults")
                for identifier, default_closed in trace.panels:
                    with self.subTest(section=identifier):
                        self.assertTrue(identifier.startswith(f"helix_tools.{package}."))
                        self.assertNotIn(identifier, owners, "Native section IDs must not collide")
                        owners[identifier] = package
                        self.assertEqual(bool(trace.calls[identifier]), not default_closed)
        self.assertEqual(set(owners.values()), set(MODULE_NAMES))

    def test_baseline_capabilities_remain_reachable_across_conditional_contexts(self):
        self.populated_scene()
        found = {package: {"prop": set(), "operator": set()} for package in MODULE_NAMES}

        def collect(package):
            for panel in self.panels[package]:
                for control in self.draw(panel).controls:
                    found[package][control["kind"]].add(control["identifier"])

        for package in MODULE_NAMES:
            collect(package)
        self.scene.jbt_input_mode = "TIMESTAMP"
        self.scene.jbt_timestamp = "00:00:00.000"
        self.scene.jbt_use_scene_fps = False
        collect("jump_by_time")
        self.metadata.helix_hair_cull.mode = "LOW"
        collect("hair_contact_culler")
        settings = self.scene.area_light_shadow_control
        settings.scope = "COLLECTIONS"
        collection = bpy.data.collections.new("Feature inventory light targets")
        self.scene.collection.children.link(collection)
        for obj in self.scene.objects:
            if obj.type == "LIGHT":
                collection.objects.link(obj)
        settings.collections.add().collection = collection
        collect("area_light_shadow_control")
        bpy.ops.object.armature_add()
        bpy.ops.object.mode_set(mode="POSE")
        try:
            collect("smart_empty")
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        for package, (fields, operators) in BASELINE_CONTROLS.items():
            with self.subTest(package=package):
                self.assertFalse(fields - found[package]["prop"], "Some existing settings are unreachable")
                self.assertFalse(operators - found[package]["operator"], "Some existing actions are unreachable")

    def test_primary_actions_are_reachable_with_optional_sections_closed_by_default(self):
        self.populated_scene()
        actions = {
            "jump_by_time": "jbt.jump_to_time",
            "smart_empty": "object.add_smart_empty_baked",
            "camera_timeline_culler": "object.camera_cull_timeline",
            "area_light_shadow_control": "alsc.enable_eevee_shadows",
            "hair_contact_culler": "helix.hair_cull_build",
            "cloth_cache_manager": "cloth_manager.bake_from_cache",
            "texture_resolution": "helix_textures.setup",
        }
        for package, action in actions.items():
            with self.subTest(package=package):
                controls = [control for panel in self.panels[package]
                            for control in self.draw(panel, mode="defaults").controls
                            if control["kind"] == "operator" and control["identifier"] == action]
                self.assertTrue(controls, "The normal action requires opening optional settings")
                self.assertTrue(any(control["available"] for control in controls))

    def test_jump_primary_action_disables_invalid_input_without_requiring_mapping(self):
        panel = self.panels["jump_by_time"][0]
        self.scene.jbt_input_mode = "TIMESTAMP"
        for timestamp, enabled in (("00:00:01.000", True), ("invalid", False)):
            with self.subTest(timestamp=timestamp):
                self.scene.jbt_timestamp = timestamp
                controls = [control for control in self.draw(panel, mode="defaults").controls
                            if control["identifier"] == "jbt.jump_to_time"]
                self.assertEqual(len(controls), 1)
                self.assertEqual(controls[0]["enabled"], enabled)

    def test_cloth_batch_action_availability_matches_checked_current_layer_caches(self):
        cloth = self.cloth()
        panel = self.panels["cloth_cache_manager"][0]

        def batch_controls():
            return [control for control in self.draw(panel, mode="defaults").controls
                    if control["identifier"] in {"cloth_manager.bake_from_cache", "cloth_manager.reset_bakes"}]

        self.assertTrue(all(control["available"] for control in batch_controls()))
        cloth.cloth_tool_selected = False
        self.assertTrue(all(not control["available"] for control in batch_controls()))
        cloth.cloth_tool_selected = True
        bpy.context.view_layer.objects.active = cloth
        cloth.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            self.assertTrue(all(not control["available"] for control in batch_controls()))
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        cloth.modifiers[0].point_cache.use_external = True
        self.assertTrue(all(not control["available"] for control in batch_controls()))

    def test_camera_scan_requires_camera_and_valid_results_but_supports_marker_camera(self):
        panel = self.panels["camera_timeline_culler"][0]

        def scan_available():
            controls = [control for control in self.draw(panel, mode="defaults").controls
                        if control["identifier"] == "object.camera_cull_timeline"]
            self.assertEqual(len(controls), 1)
            return controls[0]["available"]

        self.mesh("Geometry", (0, 0, -10))
        self.assertFalse(scan_available())
        camera = bpy.data.objects.new("Marker Camera", bpy.data.cameras.new("Marker Camera"))
        self.scene.collection.objects.link(camera)
        self.scene.timeline_markers.new("Camera Cut", frame=1).camera = camera
        self.assertTrue(scan_available())
        self.scene.camera = camera
        group = self.camera_results()
        self.assertTrue(scan_available())
        unrelated = bpy.data.objects.new("Unrelated content", None)
        group.objects.link(unrelated)
        self.assertFalse(scan_available())

    def test_hair_before_build_preserves_ids_and_later_build_section(self):
        self.hair()
        panel = self.panels["hair_contact_culler"][0]
        before_build = self.assert_closed_draws(panel)
        self.assertEqual(len(before_build.panels), 2)
        addon = self.modules["hair_contact_culler"]
        self.metadata = addon.create_bundle(self.source, [self.garment])
        addon.compile_bundle(self.metadata)
        after_build = self.assert_closed_draws(panel)
        self.assertEqual(before_build.panels, after_build.panels[:2])
        self.assertGreater(len(after_build.panels), len(before_build.panels))

    def test_hair_preview_and_missing_garment_branches_tolerate_closed_bodies(self):
        self.hair(built=True)
        settings = self.metadata.helix_hair_cull
        panel = self.panels["hair_contact_culler"][0]
        for state in ("low", "original", "hidden garment", "missing garment", "empty list"):
            with self.subTest(state=state):
                if state == "low":
                    settings.mode = "LOW"
                elif state == "original":
                    settings.preview_original = True
                elif state == "hidden garment":
                    self.garment.hide_set(True)
                    self.garment.hide_render = True
                elif state == "missing garment":
                    settings.items[0].obj = None
                else:
                    settings.items.clear()
                self.assert_closed_draws(panel)

    def test_camera_valid_and_contaminated_results_remain_collapsible(self):
        group = self.camera_results()
        panel = self.panels["camera_timeline_culler"][0]
        opened = self.assert_closed_draws(panel)
        self.assertIn("Results", [title for _, title, _ in opened.headers])
        self.scene.camera_cull_settings.scope = "PREVIEW"
        self.scene.use_preview_range = True
        self.scene.frame_preview_start, self.scene.frame_preview_end = 2, 3
        unrelated = bpy.data.objects.new("Unrelated result content", None)
        group.objects.link(unrelated)
        self.assert_closed_draws(panel)

    def test_cloth_objects_section_keeps_real_cache_controls_and_later_about(self):
        cloth = self.cloth()
        unchecked = self.mesh("Unchecked Cloth")
        unchecked.modifiers.new("Other Cloth", "CLOTH")
        unchecked.cloth_tool_selected = False
        cloth.modifiers[0].show_render = False
        panel = self.panels["cloth_cache_manager"][0]
        opened = self.assert_closed_draws(panel)
        self.assertIn("Cloth Objects", [title for _, title, _ in opened.headers])
        self.assertEqual(sum(call[:2] == ("prop", "cloth_tool_selected")
                             for calls in opened.calls.values() for call in calls), 2)
        self.assertIn("About", [title for _, title, _ in opened.headers])

    def test_cloth_model_sections_collapse_independently_and_keep_every_cage(self):
        models = [self.mesh(name) for name in ("Orc A", "Orc B")]
        cages = []
        for model in models:
            for index in range(2):
                cage = self.mesh(f"{model.name} Cage {index + 1}")
                cage.modifiers.new("Cloth", "CLOTH")
                deform = model.modifiers.new(f"Cage {index + 1}", "MESH_DEFORM")
                deform.object = cage
                cages.append(cage)
        cages[0].cloth_tool_selected = False
        panel = self.panels["cloth_cache_manager"][0]
        opened = self.assert_closed_draws(panel)
        groups = self.modules["cloth_cache_manager"].cloth_groups(self.scene)
        for group in groups:
            identifier = next(identifier for identifier, title, _ in opened.headers
                              if title == f"{group.label} (2)")
            listed = {control["data"] for control in opened.controls
                      if control["section"] == identifier
                      and control["identifier"] == "cloth_tool_selected"}
            self.assertEqual(listed, set(group.objects))
            collapsed = self.draw(panel, closed_id=identifier)
            listed = {control["data"] for control in collapsed.controls
                      if control["identifier"] == "cloth_tool_selected"}
            self.assertEqual(listed, set(cages) - set(group.objects))
        defaults = self.draw(panel, mode="defaults")
        self.assertFalse(any(control["identifier"] == "cloth_tool_model"
                             for control in defaults.controls))
        self.assertEqual({control["data"] for control in defaults.controls
                          if control["identifier"] == "cloth_tool_selected"}, set(cages))

    def test_lighting_scope_source_shadow_and_renderer_branches_do_not_mutate(self):
        self.light()
        self.light("Sidebar Point", "POINT")
        self.light("Sidebar Spot", "SPOT")
        settings = self.scene.area_light_shadow_control
        settings.scope = "COLLECTIONS"
        for populated in (False, True):
            if populated:
                collection = bpy.data.collections.new("Sidebar Light Collection")
                self.scene.collection.children.link(collection)
                for obj in self.scene.objects:
                    if obj.type == "LIGHT":
                        collection.objects.link(obj)
                settings.collections.add().collection = collection
            with self.subTest(collection_scope_populated=populated):
                self.assert_closed_draws(self.modules["area_light_shadow_control"].VIEW3D_PT_alsc_main)
        for source_type in ("AREA", "POINT", "SPOT", "ALL3"):
            settings.size_light_types = source_type
            settings.size_reduction = 1.0
            settings.last_skipped = 2
            with self.subTest(source_type=source_type):
                self.assert_closed_draws(self.modules["area_light_shadow_control"].VIEW3D_PT_alsc_sizes)
        settings.shadow_preset = "CRISP"
        for engine in ("BLENDER_EEVEE", "CYCLES"):
            self.scene.render.engine = engine
            with self.subTest(renderer=engine):
                self.assert_closed_draws(self.modules["area_light_shadow_control"].VIEW3D_PT_alsc_shadows)
        self.assert_closed_draws(self.modules["area_light_shadow_control"].VIEW3D_PT_alsc_scene)

    def test_smart_empty_pose_tracking_and_jump_invalid_input_support_closed_sections(self):
        bpy.ops.object.armature_add()
        rig = bpy.context.object
        bpy.ops.object.mode_set(mode="POSE")
        try:
            self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {"FINISHED"})
            self.scene.helix_smart_empty_settings.rig = rig
            self.scene.helix_smart_empty_settings.list_scope = "RIG"
            self.assert_closed_draws(self.panels["smart_empty"][0])
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        self.scene.jbt_input_mode = "TIMESTAMP"
        self.scene.jbt_timestamp = "invalid timestamp"
        self.scene.jbt_use_scene_fps = False
        self.scene.jbt_fps_override = 29.97
        self.assert_closed_draws(self.panels["jump_by_time"][0])


if __name__ == "__main__":
    unittest.main()
