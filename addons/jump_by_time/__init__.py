# SPDX-License-Identifier: GPL-3.0-or-later
# Original add-on by ChatGPT; maintained by Helices3D.
"""Map elapsed timestamps from external editors to Blender timeline frames."""

import math
import re

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ._ui import section, setup_layout
from ._updates import create_updater


_UPDATER = create_updater(__package__, __file__)


bl_info = {
    "name": "Jump By Time",
    "author": "ChatGPT, Helices3D",
    "version": (2, 1, 0),
    "blender": (5, 2, 2),
    "location": "3D View > Sidebar > Helix Tools",
    "description": "Map external dialogue timestamps to scene frames with an adjustable start offset",
    "category": "Animation",
}


def effective_fps(scene):
    """Return the actual scene rate, including Blender's fractional FPS base."""
    rate = float(scene.render.fps) / float(scene.render.fps_base)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Frame rate must be greater than zero")
    return rate


def _nearest_frame(value):
    """Round to the nearest frame, with exact half frames away from zero."""
    if not math.isfinite(value):
        raise ValueError("The requested time is too large")
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def compute_target_frame(offset, seconds, frames, fps):
    """Add frame units directly; overflowing frame inputs never lose frames."""
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Frame rate must be greater than zero")
    # Quantize elapsed time before adding the integer origin. This also makes
    # offset alignment stable at exact half-frame boundaries around frame zero.
    return offset + _nearest_frame(seconds * fps + frames)


_TIMESTAMP = re.compile(r"^[+-]?\d+(?::\d+){0,2}(?:\.\d+)?$", re.ASCII)


def parse_timestamp(text):
    """Parse elapsed SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm timestamps.

    These are elapsed seconds, not SMPTE frame labels or drop-frame timecode.
    The sign applies to the whole timestamp.
    """
    value = text.strip()
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Use SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm")
    negative = value.startswith("-")
    unsigned = value.lstrip("+-")
    parts = unsigned.split(":")
    seconds = float(parts[-1])
    if len(parts) > 1 and seconds >= 60:
        raise ValueError("The seconds field must be below 60")
    minutes = int(parts[-2]) if len(parts) > 1 else 0
    if len(parts) == 3 and minutes >= 60:
        raise ValueError("The minutes field must be below 60")
    hours = int(parts[0]) if len(parts) == 3 else 0
    elapsed = hours * 3600 + minutes * 60 + seconds
    if not math.isfinite(elapsed):
        raise ValueError("The requested time is too large")
    return -elapsed if negative else elapsed


def format_timestamp(seconds):
    """Format the current frame's approximate elapsed timestamp in milliseconds."""
    sign = "-" if seconds < 0 else ""
    milliseconds = _nearest_frame(abs(seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, remainder = divmod(remainder, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{remainder:03d}"


def selected_fps(scene):
    return effective_fps(scene) if scene.jbt_use_scene_fps else float(scene.jbt_fps_override)


def target_frame(scene, offset=None):
    fps = selected_fps(scene)
    base = scene.jbt_offset if offset is None else offset
    if scene.jbt_input_mode == "TIMESTAMP":
        return compute_target_frame(base, parse_timestamp(scene.jbt_timestamp), 0, fps)
    return compute_target_frame(base, scene.jbt_seconds, scene.jbt_frames, fps)


def resolve_target(scene, target):
    """Apply the user's explicit playback-range policy, then check Blender limits."""
    outside = target < scene.frame_start or target > scene.frame_end
    if outside and scene.jbt_range_policy == "REJECT":
        raise ValueError(f"Frame {target} is outside the playback range ({scene.frame_start}–{scene.frame_end})")
    if scene.jbt_range_policy == "CLAMP":
        target = min(max(target, scene.frame_start), scene.frame_end)
    limits = bpy.types.Scene.bl_rna.properties["frame_current"]
    if not limits.hard_min <= target <= limits.hard_max:
        raise ValueError(f"Frame {target} exceeds Blender's supported frame limits")
    return target


def _init_props():
    scene = bpy.types.Scene
    scene.jbt_offset = IntProperty(
        name="Starting Frame Offset",
        description="Blender frame corresponding to time zero in the external clip or timeline segment",
        default=30, min=-1048574, max=1048574,
    )
    scene.jbt_seconds = IntProperty(
        name="Seconds", description="Whole elapsed seconds from the external segment's start",
        default=0, min=0, max=999999,
    )
    scene.jbt_frames = IntProperty(
        name="Additional Frames",
        description="Additional frames after the seconds value; values larger than one second are allowed",
        default=0, min=0, max=999999,
    )
    scene.jbt_use_scene_fps = BoolProperty(
        name="Use Scene Frame Rate",
        description="Use the scene rate, including its fractional FPS base, to convert elapsed seconds",
        default=True,
    )
    scene.jbt_fps_override = FloatProperty(
        name="Frame Rate Override",
        description="Frames per elapsed second; supports fractional rates such as 29.97 or 23.976",
        default=30.0, min=0.001, max=1000.0, precision=6,
    )
    scene.jbt_input_mode = EnumProperty(
        name="Input Format",
        items=(
            ("SECONDS_FRAMES", "Seconds + Frames", "Elapsed whole seconds plus additional frames"),
            ("TIMESTAMP", "Elapsed Timestamp", "Elapsed seconds as SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm"),
        ),
        default="SECONDS_FRAMES",
    )
    scene.jbt_timestamp = StringProperty(
        name="Timestamp", default="00:00:00.000",
        description="Elapsed SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm; optional minus sign. Not SMPTE or drop-frame timecode",
    )
    scene.jbt_range_policy = EnumProperty(
        name="Outside Playback Range",
        items=(
            ("ALLOW", "Allow", "Jump outside the playback range, within Blender's supported frame limits"),
            ("CLAMP", "Clamp to Range", "Jump to the nearest playback-range boundary if the requested time is outside"),
            ("REJECT", "Cancel Jump", "Report an error instead of jumping outside the playback range"),
        ),
        default="ALLOW",
    )


_PROPERTIES = (
    "jbt_offset", "jbt_seconds", "jbt_frames", "jbt_use_scene_fps",
    "jbt_fps_override", "jbt_input_mode", "jbt_timestamp", "jbt_range_policy",
)


def _clear_props():
    for name in _PROPERTIES:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)


@persistent
def _migrate_fps_override(_unused=None):
    """Keep persisted integer overrides from the original add-on usable as floats."""
    # Blender deliberately hides scene data while an add-on is being enabled.
    # The current open file still needs migration once registration completes.
    scenes = getattr(bpy.data, "scenes", None)
    if scenes is None:
        if not bpy.app.timers.is_registered(_deferred_fps_migration):
            bpy.app.timers.register(_deferred_fps_migration, first_interval=0.0)
        return
    for scene in scenes:
        previous = scene.get("jbt_fps_override")
        if scene.library is None and isinstance(previous, int) and not isinstance(previous, bool):
            scene["jbt_fps_override"] = float(previous)
            scene.jbt_fps_override = float(previous)


def _deferred_fps_migration():
    """Migrate once enabling releases RestrictBlend; never outlive this add-on."""
    if not _registered:
        return None
    if not hasattr(bpy.data, "scenes"):
        return 0.1
    _migrate_fps_override()
    return None


class JBT_OT_jump(bpy.types.Operator):
    """Jump to an external elapsed timestamp using the starting frame offset"""
    bl_idname = "jbt.jump_to_time"
    bl_label = "Jump to Time"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        try:
            requested = target_frame(scene)
            target = resolve_target(scene, requested)
        except (ValueError, OverflowError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        scene.frame_set(target)
        actual = scene.frame_current
        if requested != actual:
            self.report({"INFO"}, f"Jumped to frame {actual}; requested frame {requested} was clamped to the playback range")
        else:
            self.report({"INFO"}, f"Jumped to frame {actual} ({selected_fps(scene):.6g} fps; offset {scene.jbt_offset})")
        return {"FINISHED"}


class JBT_OT_align_offset(bpy.types.Operator):
    """Make the entered external time correspond to the current Blender frame"""
    bl_idname = "jbt.align_offset"
    bl_label = "Align Time to Current Frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        try:
            offset = scene.frame_current - target_frame(scene, offset=0)
            limits = scene.bl_rna.properties["jbt_offset"]
            if not limits.hard_min <= offset <= limits.hard_max:
                raise ValueError("The resulting starting offset exceeds Blender's supported frame limits")
        except (ValueError, OverflowError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        scene.jbt_offset = offset
        self.report({"INFO"}, f"Time zero now maps to frame {offset}; entered time maps to frame {scene.frame_current}")
        return {"FINISHED"}


class JBT_PT_panel(bpy.types.Panel):
    bl_label = "Jump By Time"
    bl_idname = "JBT_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Helix Tools"
    bl_order = 10

    def draw(self, context):
        layout = self.layout
        setup_layout(layout)
        scene = context.scene
        timing = section(layout, "External Time", section_id="external_time")
        if timing is not None:
            timing.prop(scene, "jbt_input_mode")
            if scene.jbt_input_mode == "TIMESTAMP":
                timing.prop(scene, "jbt_timestamp")
                timing.label(text="HH:MM:SS.mmm · elapsed time", icon="TIME")
            else:
                timing.prop(scene, "jbt_seconds")
                timing.prop(scene, "jbt_frames")

        mapping = section(layout, "Timeline Mapping", section_id="timeline_mapping")
        if mapping is not None:
            mapping.prop(scene, "jbt_offset")
            mapping.operator(JBT_OT_align_offset.bl_idname, icon="TRACKING")
            mapping.prop(scene, "jbt_use_scene_fps")
            if not scene.jbt_use_scene_fps:
                mapping.prop(scene, "jbt_fps_override")
            mapping.label(text=f"Effective frame rate: {selected_fps(scene):.6g} fps")
            mapping.prop(scene, "jbt_range_policy")

        preview = section(layout, "Jump Preview", section_id="jump_preview")
        if preview is not None:
            valid = True
            try:
                requested = target_frame(scene)
                result = resolve_target(scene, requested)
                preview.label(text=f"Target frame: {result}", icon="TIME")
                if requested != result:
                    preview.label(text=f"Requested {requested}; clamped to playback range", icon="INFO")
                elif not scene.frame_start <= requested <= scene.frame_end:
                    preview.label(text="Outside playback range (allowed)", icon="INFO")
            except (ValueError, OverflowError) as error:
                valid = False
                preview.label(text=str(error), icon="ERROR")
            preview.label(text=f"Current frame: {scene.frame_current}")
            preview.label(text=f"Elapsed: {format_timestamp((scene.frame_current - scene.jbt_offset) / selected_fps(scene))}")
            row = preview.row()
            row.enabled = valid
            row.operator(JBT_OT_jump.bl_idname, icon="PLAY")


classes = (JBT_OT_jump, JBT_OT_align_offset, JBT_PT_panel)
_registered_classes = []
_owns_properties = False
_registered = False


def _preflight_registration():
    for cls in classes:
        if issubclass(cls, bpy.types.Operator):
            namespace, operator = cls.bl_idname.split(".", 1)
            identifier = f"{namespace.upper()}_OT_{operator}"
            existing = bpy.types.Operator.bl_rna_get_subclass_py(identifier, None)
        else:
            existing = bpy.types.Panel.bl_rna_get_subclass_py(cls.bl_idname, None)
        if existing is not None:
            raise RuntimeError("Disable the older or duplicate Jump By Time add-on before enabling this copy")
    if any(hasattr(bpy.types.Scene, name) for name in _PROPERTIES):
        raise RuntimeError("Disable the older or duplicate Jump By Time add-on before enabling this copy (scene settings are already registered)")


def register():
    global _owns_properties, _registered
    if _registered:
        return
    # Check before changing Scene schemas, preserving the enabled older copy.
    _preflight_registration()
    try:
        _UPDATER.register()
        _owns_properties = True
        _init_props()
        for cls in classes:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        _migrate_fps_override()
        if _migrate_fps_override not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_migrate_fps_override)
        _registered = True
    except Exception:
        unregister()
        raise


def unregister():
    global _owns_properties, _registered
    _UPDATER.unregister()
    if bpy.app.timers.is_registered(_deferred_fps_migration):
        bpy.app.timers.unregister(_deferred_fps_migration)
    if _migrate_fps_override in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_migrate_fps_override)
    for cls in reversed(_registered_classes):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    _registered_classes.clear()
    if _owns_properties:
        _clear_props()
    _owns_properties = False
    _registered = False


if __name__ == "__main__":
    register()
