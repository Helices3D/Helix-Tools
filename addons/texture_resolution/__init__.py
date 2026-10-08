# SPDX-License-Identifier: GPL-3.0-or-later
"""Reversible half-resolution scene textures for Blender 5.2.2 LTS."""

bl_info = {
    "name": "Texture Resolution",
    "author": "Codex, Helices3D",
    "version": (1, 0, 0),
    "blender": (5, 2, 2),
    "location": "3D Viewport > Sidebar > Helix Tools",
    "description": "Create half-resolution textures and switch back to their originals",
    "category": "Material",
    "license": "GPL-3.0-or-later",
}

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList

from ._core import (
    SCENE_PROPERTY, eligible_image, estimate_image_memory, iter_setup,
    memory_estimates, setup_scene, switch_scene,
)
from ._layout import section, setup_layout
from ._updates import create_updater

_UPDATER = create_updater(__package__, __file__)
_UPDATER_REGISTERED = False
_REGISTERED_CLASSES = []
_OWNED_SCENE_PROPERTY = None
_RUNNING_SCENES = set()
_ACTIVE_OPERATORS = []


class HELIX_TEXTURES_PG_Record(PropertyGroup):
    original: PointerProperty(type=bpy.types.Image, name="Original Texture")
    alternative: PointerProperty(type=bpy.types.Image, name="Half-Resolution Texture")
    source_signature: StringProperty(name="Source Signature", options={"HIDDEN"})
    original_width: IntProperty(default=0, min=0)
    original_height: IntProperty(default=0, min=0)
    channels: IntProperty(default=4, min=1)
    is_float: BoolProperty(default=False)
    half_precision: BoolProperty(default=True)


class HELIX_TEXTURES_PG_Settings(PropertyGroup):
    threshold: IntProperty(
        name="Minimum Size", default=4000, min=2, max=131072,
        description="Create an alternative when either image dimension reaches this many pixels; both dimensions are halved",
    )
    output_directory: StringProperty(
        name="Output Folder", subtype="DIR_PATH", default="",
        description="Folder for new texture files; blank uses helix_texture_variants beside the saved blend file, or Blender user data for an unsaved file",
    )
    include_shared: BoolProperty(
        name="Include Shared Resources", default=False,
        description="Also switch material, world, object and node references shared with other scenes; those scenes will change too",
    )
    records: CollectionProperty(type=HELIX_TEXTURES_PG_Record)
    active_index: IntProperty(default=0, min=0)
    last_report: StringProperty(name="Last Result")
    details: StringProperty(name="Details")


def _settings(scene):
    return getattr(scene, SCENE_PROPERTY)


def _available(context):
    scene = getattr(context, "scene", None)
    if scene is None or not scene.is_editable or _RUNNING_SCENES:
        return False
    return not bpy.app.is_job_running("RENDER") and not any(
        window.screen and window.screen.is_animation_playing
        for window in context.window_manager.windows
    )


def _tag_redraw(context):
    for window in context.window_manager.windows:
        if window.screen is not None:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()


def _report_result(operator, context, result):
    settings = _settings(context.scene)
    severity = {"WARNING"} if result.failed or result.skipped else {"INFO"}
    message = settings.last_report or (
        f"{result.processed} processed; {result.unchanged} unchanged, "
        f"{result.skipped} skipped, {result.failed} failed"
    )
    operator.report(severity, message)
    _tag_redraw(context)
    return {"FINISHED"} if result.processed or result.unchanged or result.changed_refs else {"CANCELLED"}


class HELIX_TEXTURES_OT_setup(Operator):
    """Create half-resolution alternatives, then use them in this scene"""

    bl_idname = "helix_textures.setup"
    bl_label = "Setup and Run"
    bl_description = (
        "Find eligible scene images, save half-resolution copies and switch to them. "
        "Original files stay unchanged. Run again to include new or edited textures"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _available(context)

    def execute(self, context):
        try:
            return _report_result(self, context, setup_scene(context))
        except Exception as error:
            self.report({"ERROR"}, f"Texture setup failed: {error}")
            return {"CANCELLED"}

    def invoke(self, context, event):
        if bpy.app.background or context.window is None:
            return self.execute(context)
        self._scene = context.scene
        self._scene_pointer = context.scene.as_pointer()
        self._iterator = iter_setup(context)
        self._timer = None
        self._wm = context.window_manager
        self._cancelled = False
        self._cleaned = False
        try:
            _RUNNING_SCENES.add(self._scene_pointer)
            _ACTIVE_OPERATORS.append(self)
            self._wm.progress_begin(0, 100)
            self._timer = self._wm.event_timer_add(0.05, window=context.window)
            self._wm.modal_handler_add(self)
        except Exception as error:
            self._cleanup(context)
            self.report({"ERROR"}, f"Could not start texture setup: {error}")
            return {"CANCELLED"}
        _tag_redraw(context)
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        timer = getattr(self, "_timer", None)
        if timer is not None:
            self._wm.event_timer_remove(timer)
            self._timer = None
        iterator = getattr(self, "_iterator", None)
        if iterator is not None:
            try:
                iterator.close()
            except (AttributeError, ReferenceError, RuntimeError, ValueError):
                # Deleted scenes or disabling the add-on can invalidate RNA
                # before a partial-result summary is written. Always release
                # its native event timer and busy state during teardown.
                pass
            self._iterator = None
        _RUNNING_SCENES.discard(getattr(self, "_scene_pointer", 0))
        if self in _ACTIVE_OPERATORS:
            _ACTIVE_OPERATORS.remove(self)
        if getattr(self, "_wm", None) is not None:
            self._wm.progress_end()
        _tag_redraw(context)

    def cancel(self, context):
        self._cleanup(context)

    def modal(self, context, event):
        if self._cancelled:
            return {"CANCELLED"}
        if event.type == "ESC" or context.scene != self._scene:
            self._cleanup(context)
            self.report({"INFO"}, "Texture setup stopped; completed alternatives are kept")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        try:
            done, total, label = next(self._iterator)
            self._wm.progress_update(100 * done / max(1, total))
            _tag_redraw(context)
        except StopIteration as finished:
            self._cleanup(context)
            return _report_result(self, context, finished.value)
        except Exception as error:
            self._cleanup(context)
            self.report({"ERROR"}, f"Texture setup failed: {error}")
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class HELIX_TEXTURES_OT_switch_resolution(Operator):
    """Switch tracked scene references without recreating image files"""

    bl_idname = "helix_textures.switch_resolution"
    bl_label = "Switch Texture Resolution"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Resolution", items=(
            ("ORIGINAL", "Original", "Use the tracked original images at their full dimensions"),
            ("HALF", "Half Resolution", "Use the saved alternatives at half of each original dimension"),
        ),
    )

    @classmethod
    def poll(cls, context):
        return _available(context) and bool(_settings(context.scene).records)

    @classmethod
    def description(cls, context, properties):
        if properties.mode == "ORIGINAL":
            return "Use original textures in tracked scene references; saved half-resolution copies stay available"
        return "Use the saved half-resolution textures in tracked scene references; original files stay unchanged"

    def execute(self, context):
        try:
            return _report_result(self, context, switch_scene(context, self.mode))
        except Exception as error:
            self.report({"ERROR"}, f"Could not switch textures: {error}")
            return {"CANCELLED"}


class HELIX_TEXTURES_OT_details(Operator):
    """Show the latest texture setup or switch details"""

    bl_idname = "helix_textures.details"
    bl_label = "Texture Details"
    bl_options = {"INTERNAL"}

    @classmethod
    def description(cls, context, properties):
        return _settings(context.scene).last_report or "Show the latest texture setup or switch details"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=650)

    def draw(self, context):
        layout = self.layout
        settings = _settings(context.scene)
        layout.label(text=settings.last_report or "No texture operation yet", icon="INFO")
        for line in settings.details.splitlines():
            # Keep large diagnostics readable without widening the sidebar.
            words, text = line.split(), ""
            for word in words:
                if len(text) + len(word) + 1 > 90:
                    layout.label(text=text)
                    text = "    " + word
                else:
                    text = f"{text} {word}" if text else word
            if text:
                layout.label(text=text)

    def execute(self, context):
        return {"FINISHED"}


def _record_dimensions(item):
    width, height = item.original_width, item.original_height
    if not width or not height:
        return "Dimensions unavailable"
    original = f"{width:,} × {height:,}" if item.original else "Missing"
    half = f"{max(1, width // 2):,} × {max(1, height // 2):,}" if item.alternative else "Missing"
    return f"{original} → {half}"


def _bytes_label(size):
    size = max(0, size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{size:,} B"
        size /= 1024


class HELIX_TEXTURES_UL_Images(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        original, alternative = item.original, item.alternative
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row()
            row.label(text=original.name if original else "Missing Original",
                      icon="IMAGE_DATA" if original and alternative else "ERROR")
        else:
            layout.label(text="", icon="IMAGE_DATA" if original and alternative else "ERROR")

    def filter_items(self, context, data, propname):
        records = getattr(data, propname)
        if not self.filter_name:
            return [], []
        pattern = self.filter_name.casefold()
        flags = [self.bitflag_filter_item if pattern in (item.original.name.casefold() if item.original else "missing original") else 0
                 for item in records]
        return flags, []


class HELIX_TEXTURES_PT_main(Panel):
    bl_idname = "HELIX_TEXTURES_PT_main"
    bl_label = "Texture Resolution"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Helix Tools"
    bl_order = 70

    def draw(self, context):
        layout = setup_layout(self.layout)
        settings = _settings(context.scene)
        busy = context.scene.as_pointer() in _RUNNING_SCENES
        stats = memory_estimates(context.scene)
        mode = stats["current_mode"]

        resolution = section(layout, "Resolution", icon="IMAGE_DATA", section_id="resolution")
        if resolution is not None:
            controls = resolution.column()
            controls.enabled = not busy
            controls.prop(settings, "threshold", text="Min Size (px)")
            action = controls.column()
            action.scale_y = 1.25
            action.operator("helix_textures.setup", icon="FILE_REFRESH")
            if settings.records:
                row = controls.row(align=True)
                row.operator("helix_textures.switch_resolution", text="Original", depress=mode == "ORIGINAL").mode = "ORIGINAL"
                row.operator("helix_textures.switch_resolution", text="Half Resolution", depress=mode == "HALF").mode = "HALF"
                if mode == "MIXED":
                    resolution.label(text="Mixed resolutions in use", icon="INFO")
            if busy:
                resolution.label(text="Preparing textures… Esc to stop", icon="TIME")
            elif settings.last_report:
                row = resolution.row(align=True)
                row.label(text=settings.last_report.split(";")[0], icon="INFO")
                row.operator("helix_textures.details", text="", icon="TEXT")

        options = section(layout, "Settings", icon="SETTINGS", section_id="settings", default_closed=True)
        if options is not None:
            options.enabled = not busy
            options.prop(settings, "output_directory", text="Output Folder")
            row = options.row()
            row.use_property_split = False
            row.prop(settings, "include_shared")

        images = section(layout, "Sizes & Images", icon="TEXTURE", section_id="images", default_closed=True)
        if images is not None:
            images.label(text=f"{len(settings.records):,} tracked textures")
            if settings.records:
                images.template_list("HELIX_TEXTURES_UL_Images", "", settings, "records", settings,
                                     "active_index", rows=3, maxrows=6)
                index = min(settings.active_index, len(settings.records) - 1)
                item = settings.records[index]
                images.label(text=_record_dimensions(item))

        memory = section(layout, "Estimated Memory", icon="MEMORY", section_id="memory", default_closed=True)
        if memory is not None:
            def memory_row(label, original, half):
                row = memory.row()
                split = row.split(factor=0.24)
                split.column().label(text=label)
                values = split.split(factor=0.5)
                values.column().label(text=original)
                values.column().label(text=half)

            memory_row("", "Original", "Half")
            memory_row("RAM", _bytes_label(stats["original_ram"]), _bytes_label(stats["half_ram"]))
            memory_row("VRAM", _bytes_label(stats["original_vram"]), _bytes_label(stats["half_vram"]))
            memory.label(text=f"Current RAM: {_bytes_label(stats['current_ram'])}")
            memory.label(text=f"Current VRAM: {_bytes_label(stats['current_vram'])}")
            original = stats["original_vram"]
            if original:
                saved = max(0, original - stats["half_vram"])
                memory.label(text=f"Potential VRAM reduction: {saved / original:.0%}")
            memory.label(text="Estimates, not measured usage", icon="INFO")
            memory.label(text="Original buffers may remain in RAM")

        about = section(layout, "About", icon="INFO", section_id="about", default_closed=True)
        if about is not None:
            about.label(text="Both dimensions are halved")
            about.label(text="Original files stay unchanged")
            about.label(text="Run again for new or edited textures")
            about.label(text="Save blend to keep tracked pairs")
            about.label(text="Copy alternatives with project")
            about.label(text="Still images; no UDIMs/sequences")
            about.label(text="Estimates exclude other resources")


CLASSES = (
    HELIX_TEXTURES_PG_Record, HELIX_TEXTURES_PG_Settings, HELIX_TEXTURES_OT_setup,
    HELIX_TEXTURES_OT_switch_resolution, HELIX_TEXTURES_OT_details,
    HELIX_TEXTURES_UL_Images, HELIX_TEXTURES_PT_main,
)


def _registered_type(cls):
    return cls.__bases__[0].bl_rna_get_subclass_py(cls.__name__, None)


def register():
    global _UPDATER_REGISTERED, _OWNED_SCENE_PROPERTY
    if _REGISTERED_CLASSES:
        return
    if hasattr(bpy.types.Scene, SCENE_PROPERTY) or any(_registered_type(cls) for cls in CLASSES):
        raise RuntimeError("Disable the older or duplicate Texture Resolution before enabling this copy")
    try:
        _UPDATER.register()
        _UPDATER_REGISTERED = True
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _REGISTERED_CLASSES.append(cls)
        setattr(bpy.types.Scene, SCENE_PROPERTY, PointerProperty(type=HELIX_TEXTURES_PG_Settings))
        _OWNED_SCENE_PROPERTY = bpy.types.Scene.__dict__[SCENE_PROPERTY]
    except Exception:
        unregister()
        raise


def unregister():
    global _UPDATER_REGISTERED, _OWNED_SCENE_PROPERTY
    for operator in tuple(_ACTIVE_OPERATORS):
        operator._cancelled = True
        operator._cleanup(bpy.context)
    if _UPDATER_REGISTERED:
        _UPDATER.unregister()
        _UPDATER_REGISTERED = False
    if (_OWNED_SCENE_PROPERTY is not None
            and bpy.types.Scene.__dict__.get(SCENE_PROPERTY) is _OWNED_SCENE_PROPERTY):
        delattr(bpy.types.Scene, SCENE_PROPERTY)
    _OWNED_SCENE_PROPERTY = None
    for cls in reversed(_REGISTERED_CLASSES):
        if _registered_type(cls) is cls:
            bpy.utils.unregister_class(cls)
    _REGISTERED_CLASSES.clear()


if __name__ == "__main__":
    register()
