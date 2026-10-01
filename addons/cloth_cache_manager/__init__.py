# SPDX-License-Identifier: GPL-3.0-or-later
"""Batch cache control for cloth cages and other objects with Cloth physics.

Adapted from Cloth Cache Manager 1.1, by Gemini and Helices3D.
"""

bl_info = {
    "name": "Cloth Cache Manager",
    "author": "Gemini + Executive Produced by Helices3D",
    "version": (1, 2, 0),
    "blender": (5, 2, 2),
    "location": "3D View > Sidebar > Helix Tools",
    "description": "Manage cloth-cage caches for reliable timeline navigation",
    "category": "Physics",
}

from dataclasses import dataclass, field
import re

import bpy
from bpy.props import BoolProperty

from ._ui import section, setup_layout


PROPERTY_NAME = "cloth_tool_selected"


@dataclass
class BatchResult:
    """Counts are per cloth modifier's active point cache, rather than objects."""

    processed: int = 0
    skipped: int = 0
    failed: int = 0
    unchanged: int = 0
    details: list[str] = field(default_factory=list)


def _cache_label(obj, modifier):
    return f"{obj.name} / {modifier.name}"


def cached_frame_count(cache):
    """Read Blender's current cache summary; an unknown summary stays unknown.

    The point-cache API does not expose an in-memory frame collection. Its
    native info summary begins with the number of stored frames. Require that
    count instead of trusting bake_from_cache(), which also 'bakes' zero frames.
    """
    info = cache.info.strip()
    if not info:
        return 0
    # Japanese joins the frame count directly to its translated label. Do not
    # require English wording or whitespace after the number.
    match = re.match(r"^(\d+)", info)
    return int(match.group(1)) if match else None


def cloth_targets(context):
    """Return checked, editable scene cloth modifiers in the current view layer.

    Every modifier is considered, including hidden modifiers. No selection or
    visibility is changed to make excluded or linked objects accessible.
    """
    if getattr(context, "scene", None) is None or getattr(context, "view_layer", None) is None:
        raise ValueError("A scene and active view layer are required")
    targets, skipped = [], []
    layer_objects = context.view_layer.objects
    for obj in context.scene.objects:
        if not getattr(obj, PROPERTY_NAME, False):
            continue
        modifiers = [modifier for modifier in obj.modifiers if modifier.type == "CLOTH"]
        if not modifiers:
            continue
        reason = None
        if obj.name not in layer_objects:
            reason = "excluded from the current view layer"
        elif not obj.is_editable:
            reason = "linked or non-editable object"
        elif obj.data is not None and not obj.data.is_editable:
            reason = "linked or non-editable mesh data"
        for modifier in modifiers:
            if reason:
                skipped.append(f"{_cache_label(obj, modifier)}: skipped ({reason})")
            elif modifier.point_cache.use_external:
                skipped.append(f"{_cache_label(obj, modifier)}: skipped (external cache is read-only to this tool)")
            else:
                targets.append((obj, modifier))
    return targets, skipped


def _run_cache_operator(context, obj, modifier, action):
    """Run the native cache operator without changing actual object selection."""
    operator = bpy.ops.ptcache.bake_from_cache if action == "BAKE" else bpy.ops.ptcache.free_bake
    with context.temp_override(
        id=obj, object=obj, active_object=obj, point_cache=modifier.point_cache,
    ):
        if not operator.poll():
            raise RuntimeError("Blender's point-cache operator is unavailable for this cache")
        return operator()


def process_caches(context, action):
    """Process checked caches, continue after individual failures, report truthfully.

    BAKE promotes already simulated frames only. RESET frees existing bakes and
    invalidates transient simulation data with Blender's dependency-graph tag;
    it does not change cloth settings or advance the scene timeline.
    """
    if action not in {"BAKE", "RESET"}:
        raise ValueError("Unknown cache action; expected BAKE or RESET")
    if getattr(context, "mode", "OBJECT") != "OBJECT":
        raise ValueError("Use Object Mode for batch cloth cache operations")
    targets, skipped = cloth_targets(context)
    result = BatchResult(skipped=len(skipped), details=skipped)
    for obj, modifier in targets:
        label = _cache_label(obj, modifier)
        try:
            cache = modifier.point_cache
            frames = cached_frame_count(cache)
            was_baked = cache.is_baked
            if action == "BAKE":
                if was_baked:
                    result.unchanged += 1
                    continue
                if cache.is_outdated:
                    result.skipped += 1
                    result.details.append(f"{label}: skipped (cache is outdated; simulate it again first)")
                    continue
                if frames is None:
                    result.skipped += 1
                    result.details.append(f"{label}: skipped (cannot verify that cached frames exist)")
                    continue
                if frames == 0:
                    result.skipped += 1
                    result.details.append(f"{label}: skipped (no cached frames; play the simulation first)")
                    continue
            else:
                if not was_baked and frames is None:
                    result.skipped += 1
                    result.details.append(f"{label}: skipped (cannot verify that cached frames exist)")
                    continue
                if not was_baked and (frames == 0 or cache.is_outdated):
                    result.unchanged += 1
                    continue

            if action == "RESET" and not obj.evaluated_get(context.evaluated_depsgraph_get()).is_evaluated:
                # The point-cache poll also succeeds for globally disabled
                # objects, but their data tag does not invalidate cache data.
                # Check before freeing a bake so skipped objects keep it.
                result.skipped += 1
                result.details.append(
                    f"{label}: skipped (enable the object's viewport monitor before resetting)"
                )
                continue

            response = _run_cache_operator(context, obj, modifier, action)
            if "FINISHED" not in response:
                raise RuntimeError("Blender cancelled the cache operation")
            if action == "BAKE":
                if not cache.is_baked:
                    raise RuntimeError("Blender did not mark this cache as baked")
            else:
                if cache.is_baked:
                    raise RuntimeError("Blender did not free this bake")
                # free_bake() alone leaves an existing memory cache reusable.
                # Tagging data marks it outdated without the original script's
                # quality +/- trick, which corrupts quality at its upper limit.
                obj.update_tag(refresh={"DATA"})
                context.view_layer.update()
                if frames not in {None, 0} and not cache.is_outdated:
                    raise RuntimeError("Blender did not invalidate the existing simulation cache")
            result.processed += 1
        except Exception as error:
            result.failed += 1
            result.details.append(f"{label}: failed ({error})")
    return result


def _report_batch(operator, context, action):
    try:
        result = process_caches(context, action)
    except (ValueError, RuntimeError) as error:
        operator.report({"WARNING"}, str(error))
        return {"CANCELLED"}
    if not (result.processed or result.unchanged or result.skipped or result.failed):
        operator.report({"WARNING"}, "Check at least one cloth object in the Cloth Objects list")
        return {"CANCELLED"}
    for detail in result.details:
        operator.report({"WARNING"}, detail)
    verb = "Promoted" if action == "BAKE" else "Reset"
    severity = {"WARNING"} if result.failed or result.skipped else {"INFO"}
    operator.report(severity, (
        f"{verb} {result.processed} cloth caches; {result.unchanged} unchanged, "
        f"{result.skipped} skipped, {result.failed} failed"
    ))
    return {"FINISHED"} if result.processed or result.unchanged else {"CANCELLED"}


class CLOTH_OT_BakeFromCache(bpy.types.Operator):
    """Keep only existing simulated frames as a bake; does not simulate missing frames"""

    bl_idname = "cloth_manager.bake_from_cache"
    bl_label = "Current Cache to Bake"
    bl_options = {"REGISTER"}

    def execute(self, context):
        return _report_batch(self, context, "BAKE")


class CLOTH_OT_ResetBakes(bpy.types.Operator):
    """Free cloth bakes and invalidate simulation data; other physics on these objects may also need resimulation"""

    bl_idname = "cloth_manager.reset_bakes"
    bl_label = "Reset Bakes"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420, confirm_text="Reset Cloth Caches")

    def draw(self, context):
        layout = self.layout
        layout.label(text="Free bakes and invalidate checked cloth caches?", icon="QUESTION")
        layout.label(text="Cloth settings and scene selection stay unchanged.")
        layout.label(text="Other physics caches on these objects may also", icon="INFO")
        layout.label(text="be invalidated and need resimulation.")
        layout.label(text="Undo cannot restore a freed bake; simulate it again.")
        layout.label(text="Disk cache files can be regenerated on playback.")

    def execute(self, context):
        return _report_batch(self, context, "RESET")


class VIEW3D_PT_ClothManager(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_ClothManager"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Helix Tools"
    bl_label = "Cloth Cache Manager"
    bl_order = 60

    def draw(self, context):
        layout = self.layout
        setup_layout(layout)
        cloth_objects = [obj for obj in context.scene.objects if any(
            modifier.type == "CLOTH" for modifier in obj.modifiers
        )]
        if not cloth_objects:
            layout.label(text="Add Cloth physics to an object to begin", icon="INFO")
            return

        operations = section(layout, "Batch Cache Actions", icon="MOD_PHYSICS")
        operations.label(text="Checked objects in the current view layer")
        row = operations.row(align=True)
        row.enabled = context.mode == "OBJECT"
        row.operator(CLOTH_OT_BakeFromCache.bl_idname, icon="FILE_TICK")
        row.operator(CLOTH_OT_ResetBakes.bl_idname, icon="FILE_REFRESH")
        operations.label(text="Play from simulation start before baking")
        operations.label(text="Bake before jumping through animation frames")
        if context.mode != "OBJECT":
            operations.label(text="Switch to Object Mode to manage caches", icon="INFO")

        objects = section(layout, "Cloth Objects", icon="OBJECT_DATAMODE")
        for obj in cloth_objects:
            column = objects.column(align=True)
            row = column.row(align=True)
            row.enabled = obj.is_editable
            row.prop(obj, PROPERTY_NAME, text="")
            row.label(text=obj.name, icon="OBJECT_DATAMODE")
            in_layer = obj.name in context.view_layer.objects
            if not in_layer:
                column.label(text="Excluded from this view layer; skipped", icon="INFO")
            elif not obj.is_editable or obj.data is not None and not obj.data.is_editable:
                column.label(text="Linked or non-editable; skipped", icon="LINKED")
            for modifier in obj.modifiers:
                if modifier.type != "CLOTH":
                    continue
                cache = modifier.point_cache
                row = column.row(align=True)
                row.enabled = obj.is_editable
                row.label(text=modifier.name, icon="MOD_CLOTH")
                row.prop(modifier, "show_viewport", text="", icon=(
                    "RESTRICT_VIEW_OFF" if modifier.show_viewport else "RESTRICT_VIEW_ON"
                ))
                row.prop(modifier, "show_render", text="", icon=(
                    "RESTRICT_RENDER_OFF" if modifier.show_render else "RESTRICT_RENDER_ON"
                ))
                status = "Baked" if cache.is_baked else "Not Baked"
                if cache.is_outdated:
                    status += " · outdated"
                column.label(text=f"Status: {status}", icon="FILE_TICK" if cache.is_baked else "FILE_CACHE")
                column.label(text=cache.info or "No cache data")
                column.label(text=f"Cache range: {cache.frame_start}–{cache.frame_end}")
                if cache.use_external:
                    column.label(text="External cache; skipped", icon="INFO")
            objects.separator()

        about = section(layout, "About", icon="INFO")
        about.label(text="Bake keeps cached frames; no new simulation")
        about.label(text="Missing frames stay missing")
        about.label(text="Includes every checked object's Cloth caches")
        about.label(text="Reset frees bakes and invalidates cached frames")
        about.label(text="Other physics on those objects may need")
        about.label(text="resimulation too; freed bakes need it to restore")


CLASSES = (CLOTH_OT_BakeFromCache, CLOTH_OT_ResetBakes, VIEW3D_PT_ClothManager)
classes = CLASSES  # Preserve the supplied script's public class collection.
_registered_classes = []
_owned_property = None


def _registered_type(cls):
    if issubclass(cls, bpy.types.Operator):
        namespace, name = cls.bl_idname.split(".", 1)
        identifier = f"{namespace.upper()}_OT_{name}"
        return bpy.types.Operator.bl_rna_get_subclass_py(identifier, None)
    return bpy.types.Panel.bl_rna_get_subclass_py(cls.bl_idname, None)


def register():
    global _owned_property
    if _registered_classes:
        return
    if hasattr(bpy.types.Object, PROPERTY_NAME) or any(_registered_type(cls) for cls in CLASSES):
        raise RuntimeError("Disable the older or duplicate Cloth Cache Manager before enabling this copy")
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        setattr(bpy.types.Object, PROPERTY_NAME, BoolProperty(
            name="Include in Cloth Cache Actions",
            description="Include every Cloth modifier on this object; independent of Blender object selection",
            default=True,
        ))
        _owned_property = bpy.types.Object.bl_rna.properties[PROPERTY_NAME]
    except Exception:
        unregister()
        raise


def unregister():
    global _owned_property
    if _owned_property is not None:
        current = bpy.types.Object.bl_rna.properties.get(PROPERTY_NAME)
        if current == _owned_property:
            delattr(bpy.types.Object, PROPERTY_NAME)
        _owned_property = None
    for cls in reversed(_registered_classes):
        if _registered_type(cls) is cls:
            bpy.utils.unregister_class(cls)
    _registered_classes.clear()


if __name__ == "__main__":
    register()
