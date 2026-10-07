# SPDX-License-Identifier: GPL-3.0-or-later
"""Light-source sizing and Eevee shadow controls for Blender 5.2.2 LTS.

Physical emitter dimensions are always calculated from a saved baseline, never
from the previous slider result. No handlers run when opening a project.
"""

bl_info = {
    "name": "Light Size and Shadow Control",
    "author": "Codex, Helices3D",
    "version": (1, 3, 2),
    "blender": (5, 2, 2),
    "location": "3D Viewport > Sidebar > Helix Tools",
    "description": "Resize light sources and enable Eevee shadows in one click",
    "category": "Lighting",
    "license": "GPL-3.0-or-later",
}

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
    IntProperty, PointerProperty, StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from ._ui import section, setup_layout

BASELINE_KEY = "_area_light_shadow_control_baseline_v1"
SCENE_PROPERTY = "area_light_shadow_control"
RENDER_BASELINE_KEY = "_helix_tools_render_preset_baseline_v1"
STARTUP_RECEIPT = ".helix-tools-startup-backup-v1.json"
_BUSY_SCENES = set()
_REGISTERED_CLASSES = []
_SCENE_PROPERTY_OWNED = False
_EXPECTED_ERRORS = (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError)


def _key(item):
    return item.as_pointer()


def _editable(item):
    return bool(getattr(item, "is_editable", item.library is None))


def _scene_for(owner, context):
    candidate = getattr(owner, "id_data", None)
    return candidate if isinstance(candidate, bpy.types.Scene) else context.scene


def _settings(scene):
    return getattr(scene, SCENE_PROPERTY)


def _set_status(settings, message, skipped=0, errors=None):
    settings.last_status = message
    settings.last_skipped = skipped
    settings.last_errors = "\n".join(errors or [])


def _object_scope(scene, settings, every_scene_light=False):
    """Collection nesting is explicit; unrelated scene/collection objects stay out."""
    scene_objects = {_key(obj): obj for obj in scene.objects}
    if every_scene_light or settings.scope == 'ALL':
        candidates = scene_objects.values()
    else:
        found = {}
        for item in settings.collections:
            collection = item.collection
            if collection is not None:
                for obj in collection.all_objects:
                    pointer = _key(obj)
                    if pointer in scene_objects:
                        found[pointer] = obj
        candidates = found.values()
    return sorted(
        (obj for obj in candidates if obj.type == 'LIGHT'),
        key=lambda obj: (obj.name_full, _key(obj)),
    )


def _size_types(settings):
    return {'AREA', 'POINT', 'SPOT'} if settings.size_light_types == 'ALL3' else {settings.size_light_types}


def _prepare_lights(scene, settings, size_only=True, every_scene_light=False):
    """Isolate editable targets from excluded aliases before changing light data.

    A single copy is shared by all included objects that used the original data.
    Read-only objects/data are skipped. This also protects aliases in other
    scenes, including objects that are not linked into the current scene.
    """
    requested = [obj for obj in _object_scope(scene, settings, every_scene_light)
                 if not size_only or obj.data.type in _size_types(settings)]
    groups = {}
    skipped = 0
    errors = []
    for obj in requested:
        if not _editable(obj) or not _editable(obj.data):
            skipped += 1
            errors.append(f"{obj.name}: linked or read-only light")
            continue
        groups.setdefault(_key(obj.data), [obj.data, []])[1].append(obj)

    aliases = {}
    for obj in bpy.data.objects:
        if obj.type == 'LIGHT':
            aliases.setdefault(_key(obj.data), []).append(obj)

    lights = []
    isolated = 0
    active = {_key(item.light) for item in settings.active_lights if item.light is not None}
    for pointer, (light, objects) in groups.items():
        editable_selected = {_key(obj) for obj in objects}
        outsiders = [obj for obj in aliases.get(pointer, [])
                     if _key(obj) not in editable_selected]
        if outsiders:
            try:
                copy = light.copy()
                copy.name = f"{light.name} (Light Control)"
            except _EXPECTED_ERRORS as exc:
                skipped += len(objects)
                errors.append(f"{light.name}: could not isolate shared data ({exc})")
                continue
            assignments = 0
            for obj in objects:
                try:
                    obj.data = copy
                    assignments += 1
                except _EXPECTED_ERRORS as exc:
                    skipped += 1
                    errors.append(f"{obj.name}: could not assign isolated data ({exc})")
            if assignments:
                lights.append(copy)
                isolated += 1
                if pointer in active:
                    # Shadow operations can split a source that is already
                    # scaled. Keep the included copy in the restoration list,
                    # and return excluded aliases to their original size now.
                    # Otherwise changing scope later would miss this copy.
                    settings.active_lights.add().light = copy
                    try:
                        _restore_light(light)
                    except _EXPECTED_ERRORS as exc:
                        errors.append(f"{light.name}: could not restore excluded source ({exc})")
            else:
                # This copy was created by this operation and has no users.
                bpy.data.lights.remove(copy)
        else:
            lights.append(light)
    return lights, skipped, isolated, errors


def _baseline(light):
    value = light.get(BASELINE_KEY)
    if value is None:
        return None
    try:
        # Version1.0 AREA baselines had no type field. Keep them valid without
        # touching dimensions or requiring a recapture after upgrading.
        light_type = str(value.get("type", 'AREA'))
        if light_type == 'AREA':
            x, y = float(value["x"]), float(value["y"])
            shape = str(value["shape"])
            if (shape not in {'SQUARE', 'RECTANGLE', 'DISK', 'ELLIPSE'}
                    or not math.isfinite(x) or not math.isfinite(y) or x <= 0 or y <= 0):
                return None
            return {"type": 'AREA', "x": x, "y": y, "shape": shape}
        if light_type in {'POINT', 'SPOT'}:
            radius = float(value["radius"])
            if not math.isfinite(radius) or radius <= 0:
                return None
            return {"type": light_type, "radius": radius}
        return None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def _capture(light, floor):
    # A zero-size emitter is given a positive baseline at the explicit floor.
    # Positive originals stay exact even below the floor: one shared multiplier
    # will raise both dimensions together during scaling, preserving aspect.
    if light.type == 'AREA':
        x = float(light.size) if light.size > 0 else floor
        y = float(light.size_y) if light.size_y > 0 else floor
        light[BASELINE_KEY] = {"type": 'AREA', "x": x, "y": y, "shape": light.shape}
    elif light.type in {'POINT', 'SPOT'}:
        radius = float(light.shadow_soft_size) if light.shadow_soft_size > 0 else floor
        light[BASELINE_KEY] = {"type": light.type, "radius": radius}
    else:
        raise ValueError(f"{light.type} lights have no source-size control")
    return _baseline(light)


def _restore_light(light):
    value = _baseline(light)
    if value is None:
        return False
    if light.type != value["type"] or (light.type == 'AREA' and light.shape != value["shape"]):
        raise ValueError("light type or area shape changed; capture current sizes again")
    if light.type == 'AREA':
        light.size = value["x"]
        if light.shape in {'RECTANGLE', 'ELLIPSE'}:
            light.size_y = value["y"]
    else:
        light.shadow_soft_size = value["radius"]
    return True


def _restore_active(settings):
    restored = 0
    errors = []
    for item in settings.active_lights:
        light = item.light
        if light is None:
            continue
        if not _editable(light):
            errors.append(f"{light.name}: could not restore read-only data")
            continue
        try:
            restored += int(_restore_light(light))
        except _EXPECTED_ERRORS as exc:
            errors.append(f"{light.name}: could not restore baseline ({exc})")
    return restored, errors


def _replace_active(settings, lights):
    settings.active_lights.clear()
    for light in lights:
        settings.active_lights.add().light = light


def _scale_scene(scene):
    settings = _settings(scene)
    lights, skipped, isolated, errors = _prepare_lights(scene, settings)
    targets = {_key(light) for light in lights}
    # Collection membership can change without a property callback. Restore any
    # previously controlled data that is no longer included on the next edit.
    for item in settings.active_lights:
        light = item.light
        if light is not None and _key(light) not in targets and _editable(light):
            try:
                _restore_light(light)
            except _EXPECTED_ERRORS as exc:
                errors.append(f"{light.name}: could not restore excluded light ({exc})")

    requested_multiplier = math.exp2(-float(settings.size_reduction))
    applied = 0
    floor_count = 0
    changed_shape = 0
    active = []
    for light in lights:
        try:
            value = _baseline(light)
            if value is None:
                value = _capture(light, settings.minimum_size)
            if value["type"] != light.type or (light.type == 'AREA' and value["shape"] != light.shape):
                changed_shape += 1
                errors.append(f"{light.name}: type or shape changed; capture current sizes again")
                active.append(light)
                continue
            if light.type == 'AREA':
                dimensions = (value["x"], value["y"]) if light.shape in {'RECTANGLE', 'ELLIPSE'} else (value["x"],)
            else:
                dimensions = (value["radius"],)
            # One multiplier protects both dimensions and their aspect ratio.
            multiplier = max(requested_multiplier, settings.minimum_size / min(dimensions))
            if multiplier > requested_multiplier:
                floor_count += 1
            if light.type == 'AREA':
                light.size = value["x"] * multiplier
                if light.shape in {'RECTANGLE', 'ELLIPSE'}:
                    light.size_y = value["y"] * multiplier
            else:
                light.shadow_soft_size = value["radius"] * multiplier
            applied += 1
            active.append(light)
        except _EXPECTED_ERRORS as exc:
            skipped += 1
            errors.append(f"{light.name}: size update failed ({exc})")
    _replace_active(settings, active)
    extra = []
    if isolated:
        extra.append(f"{isolated} shared data isolated")
    if floor_count:
        extra.append(f"{floor_count} at size floor")
    if skipped or changed_shape:
        extra.append(f"{skipped + changed_shape} skipped")
    message = f"Scaled {applied} light-source datablock{'s' if applied != 1 else ''}"
    if extra:
        message += "; " + "; ".join(extra)
    _set_status(settings, message + ".", skipped + changed_shape, errors)


def _size_update(owner, context):
    scene = _scene_for(owner, context)
    pointer = _key(scene)
    if pointer in _BUSY_SCENES:
        return
    _BUSY_SCENES.add(pointer)
    try:
        _scale_scene(scene)
    finally:
        _BUSY_SCENES.discard(pointer)


def _scope_update(owner, context):
    scene = _scene_for(owner, context)
    pointer = _key(scene)
    if pointer in _BUSY_SCENES:
        return
    _BUSY_SCENES.add(pointer)
    try:
        settings = _settings(scene)
        restored, errors = _restore_active(settings)
        settings.active_lights.clear()
        settings.size_reduction = 0.0
        _set_status(settings, f"Targets changed. Restored {restored} light sizes; reduction reset to 0.", len(errors), errors)
    finally:
        _BUSY_SCENES.discard(pointer)


def _preset_update(settings, context):
    if settings.shadow_preset == 'CUSTOM':
        return
    pointer = _key(_scene_for(settings, context))
    if pointer in _BUSY_SCENES:
        return
    _BUSY_SCENES.add(pointer)
    try:
        settings.shadow_filter = 0.5 if settings.shadow_preset == 'CRISP' else 1.0
        settings.shadow_resolution = 0.0005 if settings.shadow_preset == 'CRISP' else 0.001
        settings.absolute_resolution = False
        settings.shadow_overblur = 0.0
        settings.shadow_jitter = True
    finally:
        _BUSY_SCENES.discard(pointer)


def _shadow_custom_update(settings, context):
    if _key(_scene_for(settings, context)) not in _BUSY_SCENES:
        settings.shadow_preset = 'CUSTOM'


class ALSC_CollectionItem(PropertyGroup):
    collection: PointerProperty(
        name="Collection", type=bpy.types.Collection, update=_scope_update,
        description="Include lights in this collection and every child collection",
    )


class ALSC_ActiveLight(PropertyGroup):
    light: PointerProperty(type=bpy.types.Light)


class ALSC_Settings(PropertyGroup):
    size_light_types: EnumProperty(
        name="Light Type", items=[
            ('AREA', "Area", "Resize area emitter dimensions only"),
            ('POINT', "Point", "Resize point-light source radii only"),
            ('SPOT', "Spot", "Resize spot-light source radii only, preserving cone angle and blend"),
            ('ALL3', "Area, Point, Spot", "Resize all three source types; sun lights are excluded"),
        ], default='AREA', update=_scope_update, options=set(),
        description="Choose which light sources to resize; shadow settings still affect every light type in their target scope. Changing this restores controlled sizes and resets reduction",
    )
    scope: EnumProperty(
        name="Lights to Control", items=[
            ('ALL', "Entire Scene", "Include every light object in the current scene"),
            ('COLLECTIONS', "Collections", "Include the listed collections and all descendants"),
        ], default='ALL', update=_scope_update,
        description="Choose the scene or collection scope for source sizing and targeted shadows; changing this restores controlled source sizes and resets reduction",
    )
    collections: CollectionProperty(type=ALSC_CollectionItem)
    collection_index: IntProperty(default=0)
    active_lights: CollectionProperty(type=ALSC_ActiveLight, options={'HIDDEN'})
    size_reduction: FloatProperty(
        name="Size Reduction", default=0.0, min=0.0, max=20.0, soft_max=8.0,
        update=_size_update, options=set(), precision=2,
        description="Exponential reduction in stops: 1 halves the original area dimensions or point/spot radius, 2 makes them one-quarter; always calculated from saved baselines",
    )
    minimum_size: FloatProperty(
        name="Minimum Size", default=0.00001, min=0.000001, max=1.0,
        soft_max=0.001, subtype='DISTANCE', unit='LENGTH', precision=6,
        update=_size_update, options=set(),
        description="Positive lower bound for the shorter area-emitter dimension or point/spot source radius; area axes use one multiplier to preserve aspect ratio",
    )
    shadow_preset: EnumProperty(
        name="Preset", items=[
            ('DETAILED', "Detailed", "Filter 1 px, adaptive resolution limit 0.001, jitter on, overblur 0%"),
            ('CRISP', "Crisp Detail", "Filter 0.5 px and adaptive limit 0.0005; finer detail may require more shadow memory and can reveal aliasing"),
            ('CUSTOM', "Custom", "Use the values below"),
        ], default='DETAILED', update=_preset_update,
    )
    shadow_filter: FloatProperty(
        name="Filter (px)", default=1.0, min=0.0, max=100.0, soft_max=2.0,
        precision=2, options=set(), update=_shadow_custom_update,
        description="Shadow-map filtering radius; 1 px is the balanced starting point, 0.5 px retains more edge detail but may reveal aliasing",
    )
    shadow_resolution: FloatProperty(
        name="Detail Limit", default=0.001, min=0.000001, max=100.0,
        soft_max=0.01, subtype='DISTANCE', unit='LENGTH', precision=6, options=set(), update=_shadow_custom_update,
        description="Minimum shadow-map detail limit; adaptive mode accounts for screen coverage, and lower limits can increase shadow-pool use; 0.001 displays as 1 mm with metre scene units",
    )
    absolute_resolution: BoolProperty(
        name="Fixed Distance", default=False, options=set(), update=_shadow_custom_update,
        description="For area, point and spot lights: anchor resolution one Blender unit from the light origin instead of using adaptive screen coverage; can increase shadow-memory requirements; sun lights have no absolute-limit setting",
    )
    shadow_overblur: FloatProperty(
        name="Extra Blur", default=0.0, min=0.0, max=100.0, subtype='PERCENTAGE',
        precision=1, options=set(), update=_shadow_custom_update,
        description="Extra jitter-shadow blur; 0% preserves physical emitter-size softness",
    )
    shadow_jitter: BoolProperty(
        name="Shadow Jitter", default=True, options=set(), update=_shadow_custom_update,
        description="Enable per-light jittered shadows for more accurate soft shadows; increases rendering cost",
    )
    shadow_casting: EnumProperty(
        name="Cast Shadows", items=[
            ('KEEP', "Keep Existing", "Keep the existing shadow on/off state; recommended for rigs with shadow-free fill and rim lights"),
            ('ON', "Enable", "Enable shadow casting on every included light"),
            ('OFF', "Disable", "Disable shadow casting on every included light"),
        ], default='KEEP', options=set(),
    )
    shadow_scope: EnumProperty(
        name="Shadow Targets", items=[
            ('MATCH', "Target Scope", "Apply to every light type in Target Lights, regardless of the sizing filter"),
            ('ALL', "Entire Scene", "Apply to all light types in the scene, including lights outside the chosen collections"),
        ], default='MATCH', options=set(),
    )
    viewport_jitter: BoolProperty(
        name="Viewport Jitter", default=True, options=set(),
        description="Also enable jittered shadows during viewport interaction; can noticeably slow navigation",
    )
    last_status: StringProperty(default="Move the slider to capture baselines automatically.", options={'HIDDEN'})
    last_errors: StringProperty(options={'HIDDEN'})
    last_skipped: IntProperty(default=0, options={'HIDDEN'})
    last_startup_status: StringProperty(options={'HIDDEN'})
    last_startup_backup: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})


class ALSC_OT_collection_add(Operator):
    bl_idname = "alsc.collection_add"
    bl_label = "Add Collection"
    bl_description = "Add a collection slot; changing scope restores previous sizes and resets the slider"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context.scene)
        settings.collections.add()
        settings.collection_index = len(settings.collections) - 1
        _scope_update(settings, context)
        return {'FINISHED'}


class ALSC_OT_collection_remove(Operator):
    bl_idname = "alsc.collection_remove"
    bl_label = "Remove Collection"
    bl_description = "Remove this collection from the scope and restore previous controlled sizes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_settings(context.scene).collections)

    def execute(self, context):
        settings = _settings(context.scene)
        index = min(max(settings.collection_index, 0), len(settings.collections) - 1)
        settings.collections.remove(index)
        settings.collection_index = min(index, max(len(settings.collections) - 1, 0))
        _scope_update(settings, context)
        return {'FINISHED'}


class ALSC_OT_capture(Operator):
    bl_idname = "alsc.capture_sizes"
    bl_label = "Capture Current Sizes"
    bl_description = "Use current area dimensions or point/spot radii as new saved baselines and set reduction to 0; replaces previous baselines for chosen light types in scope"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        pointer = _key(scene)
        _BUSY_SCENES.add(pointer)
        try:
            lights, skipped, isolated, errors = _prepare_lights(scene, settings)
            target_keys = {_key(light) for light in lights}
            for entry in settings.active_lights:
                light = entry.light
                if light is not None and _key(light) not in target_keys and _editable(light):
                    try:
                        _restore_light(light)
                    except _EXPECTED_ERRORS as exc:
                        errors.append(f"{light.name}: could not restore excluded light ({exc})")
            captured = []
            for light in lights:
                try:
                    _capture(light, settings.minimum_size)
                    captured.append(light)
                except _EXPECTED_ERRORS as exc:
                    skipped += 1
                    errors.append(f"{light.name}: baseline capture failed ({exc})")
            _replace_active(settings, captured)
            settings.size_reduction = 0.0
            suffix = f"; {isolated} shared data isolated" if isolated else ""
            _set_status(settings, f"Captured {len(captured)} light-source baselines{suffix}.", skipped, errors)
            self.report({'WARNING'} if errors else {'INFO'}, settings.last_status)
        finally:
            _BUSY_SCENES.discard(pointer)
        return {'FINISHED'}


class ALSC_OT_restore(Operator):
    bl_idname = "alsc.restore_sizes"
    bl_label = "Restore Baseline"
    bl_description = "Restore saved area dimensions and point/spot radii, then reset reduction to 0; also restores previously controlled lights removed from scope"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        pointer = _key(scene)
        _BUSY_SCENES.add(pointer)
        try:
            lights, skipped, isolated, errors = _prepare_lights(scene, settings)
            all_lights = {_key(light): light for light in lights}
            for entry in settings.active_lights:
                if entry.light is not None:
                    all_lights[_key(entry.light)] = entry.light
            restored = 0
            for light in all_lights.values():
                if not _editable(light):
                    skipped += 1
                    errors.append(f"{light.name}: read-only baseline could not be restored")
                    continue
                try:
                    restored += int(_restore_light(light))
                except _EXPECTED_ERRORS as exc:
                    skipped += 1
                    errors.append(f"{light.name}: baseline restore failed ({exc})")
            settings.size_reduction = 0.0
            _replace_active(settings, lights)
            suffix = f"; {isolated} shared data isolated" if isolated else ""
            _set_status(settings, f"Restored {restored} light-source baselines{suffix}.", skipped, errors)
            self.report({'WARNING'} if errors else {'INFO'}, settings.last_status)
        finally:
            _BUSY_SCENES.discard(pointer)
        return {'FINISHED'}


class ALSC_OT_apply_sizes(Operator):
    bl_idname = "alsc.refresh_sizes"
    bl_label = "Refresh Scope"
    bl_description = "Re-evaluate collection membership, restore excluded lights, and apply the current reduction to included lights"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _size_update(_settings(context.scene), context)
        settings = _settings(context.scene)
        self.report({'WARNING'} if settings.last_errors else {'INFO'}, settings.last_status)
        return {'FINISHED'}


def _set_light_shadow_values(light, values):
    for name, value in values.items():
        setattr(light, name, value)


def _apply_scene_shadows(scene, settings, *, every=False, casting=None, prepared=None):
    lights, skipped, isolated, errors = (
        _prepare_lights(scene, settings, size_only=False, every_scene_light=every)
        if prepared is None else prepared
    )
    values = {
        "shadow_filter_radius": settings.shadow_filter,
        "shadow_maximum_resolution": settings.shadow_resolution,
        "use_absolute_resolution": settings.absolute_resolution,
        "shadow_jitter_overblur": settings.shadow_overblur,
        "use_shadow_jitter": settings.shadow_jitter,
    }
    casting = settings.shadow_casting if casting is None else casting
    if casting != 'KEEP':
        values["use_shadow"] = casting == 'ON'
    applied = 0
    suns = 0
    for light in lights:
        light_values = dict(values)
        if light.type == 'SUN':
            light_values.pop("use_absolute_resolution")
            suns += 1
        previous = {}
        try:
            previous = {name: getattr(light, name) for name in light_values}
            _set_light_shadow_values(light, light_values)
            applied += 1
        except _EXPECTED_ERRORS as exc:
            skipped += 1
            errors.append(f"{light.name}: shadow update failed ({exc})")
            for name, value in previous.items():
                try:
                    setattr(light, name, value)
                except _EXPECTED_ERRORS as rollback_exc:
                    errors.append(f"{light.name}: could not restore {name} ({rollback_exc})")
    suffix = f"; {isolated} shared data isolated" if isolated else ""
    if suns:
        suffix += f"; absolute limit applies to local lights only ({suns} sun)"
    scope_name = "every scene light" if every else "the chosen scope"
    _set_status(settings, f"Updated {applied} light datablocks in {scope_name}{suffix}.", skipped, errors)
    return applied


def _set_eevee_shadow_scene(scene, engine, enabled):
    scene.render.engine = engine
    scene.eevee.use_shadows = enabled


def _shadow_preparation_snapshot(scene, settings):
    objects = [(obj, obj.data) for obj in _object_scope(scene, settings)]
    sizes = {}
    for _, light in objects:
        if light.type == 'AREA':
            values = {"size": light.size, "size_y": light.size_y}
        elif light.type in {'POINT', 'SPOT'}:
            values = {"shadow_soft_size": light.shadow_soft_size}
        else:
            values = {}
        sizes[_key(light)] = (light, values)
    active = [item.light for item in settings.active_lights if item.light is not None]
    return objects, sizes, active


def _rollback_shadow_preparation(settings, snapshot):
    objects, sizes, active = snapshot
    copies = {}
    errors = []
    for obj, original in objects:
        if obj.data != original:
            copy = obj.data
            try:
                obj.data = original
                copies[_key(copy)] = copy
            except _EXPECTED_ERRORS as exc:
                errors.append(f"{obj.name}: could not restore original light data ({exc})")
    for light, values in sizes.values():
        if not _editable(light):
            continue
        try:
            _set_light_shadow_values(light, values)
        except _EXPECTED_ERRORS as exc:
            errors.append(f"{light.name}: could not restore source size ({exc})")
    _replace_active(settings, active)
    for light in copies.values():
        if not light.users:
            try:
                bpy.data.lights.remove(light)
            except _EXPECTED_ERRORS as exc:
                errors.append(f"Unused light copy could not be removed ({exc})")
    return errors


class ALSC_OT_shadows(Operator):
    bl_idname = "alsc.apply_shadows"
    bl_label = "Apply Shadow Settings"
    bl_description = "Apply these shadow values to every light type in the chosen scope; shadow casting is preserved unless explicitly changed"
    bl_options = {'REGISTER', 'UNDO'}

    every_scene_light: BoolProperty(default=False, options={'SKIP_SAVE'})

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        every = self.every_scene_light or settings.shadow_scope == 'ALL'
        _apply_scene_shadows(scene, settings, every=every)
        self.report({'WARNING'} if settings.last_errors else {'INFO'}, settings.last_status)
        return {'FINISHED'}


class ALSC_OT_enable_eevee_shadows(Operator):
    bl_idname = "alsc.enable_eevee_shadows"
    bl_label = "Enable Eevee Shadows"
    bl_description = "Switch this scene to Eevee, enable scene shadows and shadow casting for every light type in Target Lights, and apply current shadow detail and jitter settings; other render settings are unchanged"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        previous_scene = (scene.render.engine, scene.eevee.use_shadows)
        snapshot = _shadow_preparation_snapshot(scene, settings)
        prepared = _prepare_lights(scene, settings, size_only=False)
        if not prepared[0]:
            _set_status(settings, "No editable light targets; Eevee settings unchanged.", prepared[1], prepared[3])
            self.report({'WARNING'}, settings.last_status)
            return {'FINISHED'}
        try:
            _set_eevee_shadow_scene(scene, 'BLENDER_EEVEE', True)
        except _EXPECTED_ERRORS as exc:
            errors = list(prepared[3]) + [f"Eevee scene settings could not be enabled ({exc})"]
            rollback_errors = []
            try:
                _set_eevee_shadow_scene(scene, *previous_scene)
            except _EXPECTED_ERRORS as rollback_exc:
                rollback_errors.append(f"Previous scene settings could not be restored ({rollback_exc})")
            rollback_errors.extend(_rollback_shadow_preparation(settings, snapshot))
            errors.extend(rollback_errors)
            _set_status(settings, "Eevee shadows could not be enabled; see operation details.", prepared[1], errors)
            self.report({'WARNING'}, settings.last_status)
            # Exceptional rollback failures remain undoable instead of being
            # hidden behind a cancelled operator with no Undo entry.
            return {'FINISHED'} if rollback_errors else {'CANCELLED'}
        applied = _apply_scene_shadows(scene, settings, casting='ON', prepared=prepared)
        if not applied:
            errors = settings.last_errors.splitlines()
            try:
                _set_eevee_shadow_scene(scene, *previous_scene)
            except _EXPECTED_ERRORS as exc:
                errors.append(f"Previous scene settings could not be restored ({exc})")
            _set_status(settings, "No light shadows were enabled; see operation details.", settings.last_skipped, errors)
        else:
            settings.last_status = "Enabled Eevee scene shadows. " + settings.last_status
        self.report({'WARNING'} if settings.last_errors or not applied else {'INFO'}, settings.last_status)
        return {'FINISHED'}


def _render_preset_values(scene, settings):
    """Only these properties belong to the suggested preset and its snapshot."""
    return {
        "render.engine": 'BLENDER_EEVEE',
        "eevee.use_shadows": True,
        "eevee.taa_render_samples": 128,
        "eevee.taa_samples": 64,
        "eevee.shadow_ray_count": 4,
        "eevee.shadow_step_count": 12,
        "eevee.shadow_resolution_scale": 1.0,
        "eevee.shadow_pool_size": '2048',
        "eevee.gi_irradiance_pool_size": '1024',
        "eevee.use_shadow_jitter_viewport": settings.viewport_jitter,
        "render.use_high_quality_normals": True,
        "render.anisotropic_filter": 'FILTER_16',
        "render.compositor_device": 'GPU',
        "render.compositor_precision": 'AUTO',
        "render.preview_pixel_size": 'AUTO',
    }


def _render_current(scene):
    values = _render_preset_values(scene, _settings(scene))
    return {key: getattr(getattr(scene, key.split('.')[0]), key.split('.')[1]) for key in values}


def _render_snapshot(scene):
    value = scene.get(RENDER_BASELINE_KEY)
    if value is None:
        return None
    try:
        expected = _render_current(scene)
        if set(value.keys()) != set(expected):
            raise ValueError("snapshot fields do not match this preset")
        result = dict(value.items())
        for key, original in expected.items():
            captured = result[key]
            if isinstance(original, bool):
                valid = isinstance(captured, (bool, int)) and captured in (0, 1)
                result[key] = bool(captured)
            elif isinstance(original, (int, float)):
                valid = isinstance(captured, (int, float)) and math.isfinite(captured)
            else:
                valid = isinstance(captured, str)
            if not valid:
                raise ValueError(f"invalid snapshot value for {key}")
        return result
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Saved render snapshot is invalid: {exc}") from exc


def _set_render_values(scene, values):
    for key, value in values.items():
        owner, name = key.split('.')
        setattr(getattr(scene, owner), name, value)


def _apply_render_preset(scene):
    previous = _render_current(scene)
    baseline = _render_snapshot(scene)
    try:
        _set_render_values(scene, _render_preset_values(scene, _settings(scene)))
        if baseline is None:
            scene[RENDER_BASELINE_KEY] = previous
    except _EXPECTED_ERRORS:
        _set_render_values(scene, previous)
        if baseline is None and RENDER_BASELINE_KEY in scene:
            del scene[RENDER_BASELINE_KEY]
        raise


def _restore_render_preset(scene):
    baseline = _render_snapshot(scene)
    if baseline is None:
        raise ValueError("This scene has no saved render-settings snapshot")
    previous = _render_current(scene)
    try:
        _set_render_values(scene, baseline)
        del scene[RENDER_BASELINE_KEY]
    except _EXPECTED_ERRORS:
        _set_render_values(scene, previous)
        scene[RENDER_BASELINE_KEY] = baseline
        raise


def _startup_paths(create=False):
    config = bpy.utils.user_resource('CONFIG', create=create)
    if not config:
        raise ValueError("Blender's configuration directory is unavailable")
    config = Path(config).absolute()
    template = bpy.context.preferences.app_template
    if template:
        if Path(template).name != template or '/' in template or '\\' in template or template in {'.', '..'}:
            raise ValueError("The active application-template name is not a safe directory name")
        # Blender saves an application template's startup in its own CONFIG
        # subdirectory; the default startup must remain separate and untouched.
        config = config / template
    # Never follow a redirected startup, receipt, or configuration directory.
    if config.resolve() != config:
        raise ValueError("The Blender configuration path contains a symbolic link")
    if create:
        config.mkdir(parents=True, exist_ok=True)
    startup, receipt = config / "startup.blend", config / STARTUP_RECEIPT
    for path in (startup, receipt):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Refusing to overwrite a nonregular file: {path}")
    return config, startup, receipt


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path, data):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing to overwrite a nonregular file: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=".helix-tools-", dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_startup_receipt(data):
    record = json.loads(data.decode('utf-8'))
    if not isinstance(record, dict):
        raise ValueError("The startup backup record must be a JSON object")
    previous = record.get("previous")
    if record.get("version") != 1 or not isinstance(previous, str) or previous not in {'custom', 'factory'}:
        raise ValueError("The startup backup record has an invalid version or previous-startup type")

    def valid_hash(value):
        return isinstance(value, str) and len(value) == 64 and all(char in '0123456789abcdef' for char in value)

    def valid_backup(value):
        return (isinstance(value, str) and Path(value).name == value
                and value.startswith("startup.helix-tools-backup-") and value.endswith(".blend")
                and '/' not in value and '\\' not in value)

    if not valid_hash(record.get("written_sha256")):
        raise ValueError("The startup backup record has an invalid saved-file checksum")
    if record["previous"] == 'custom':
        if not valid_backup(record.get("backup")) or not valid_hash(record.get("previous_sha256")):
            raise ValueError("The startup backup record has an invalid backup filename or checksum")
    elif record.get("backup") != '' or record.get("previous_sha256") != '':
        raise ValueError("The factory-startup backup record is invalid")
    latest = record.get("latest_backup", '')
    if latest != '' and not valid_backup(latest):
        raise ValueError("The startup backup record has an invalid latest-backup filename")
    return record


def _write_homefile():
    """Keep Blender's disk-writing operation at one transactional boundary."""
    return bpy.ops.wm.save_homefile()


def _save_startup(context):
    config, startup, receipt_path = _startup_paths(create=True)
    original = startup.read_bytes() if startup.exists() else None
    old_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
    previous_record = _read_startup_receipt(old_receipt) if old_receipt is not None else None
    preserve_restore_point = (previous_record is not None and original is not None
                              and _digest(original) == previous_record.get("written_sha256"))
    backup = None
    if original is not None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = config / f"startup.helix-tools-backup-{stamp}-{uuid.uuid4().hex[:8]}.blend"
        with backup.open('xb') as stream:
            os.chmod(backup, 0o600)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        if backup.read_bytes() != original:
            raise OSError("Startup backup verification failed; startup was not changed")
    previous_render = _render_current(context.scene)
    previous_baseline = _render_snapshot(context.scene)
    write_attempted = False
    try:
        _apply_render_preset(context.scene)
        write_attempted = True
        result = _write_homefile()
        _, startup, _ = _startup_paths()
        if result != {'FINISHED'} or not startup.exists() or startup.stat().st_size == 0:
            raise OSError("Blender did not successfully save the startup file")
        receipt = dict(previous_record) if preserve_restore_point else {
            "version": 1,
            "previous": "custom" if original is not None else "factory",
            "backup": backup.name if backup is not None else "",
            "previous_sha256": _digest(original) if original is not None else "",
        }
        receipt["written_sha256"] = _digest(startup.read_bytes())
        receipt["latest_backup"] = backup.name if backup is not None else ""
        _atomic_write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode('utf-8'))
    except Exception as exc:
        rollback_errors = []
        if write_attempted:
            try:
                if original is None:
                    if startup.exists() and not startup.is_symlink():
                        startup.unlink()
                else:
                    _atomic_write(startup, original)
                if old_receipt is None:
                    if receipt_path.exists() and not receipt_path.is_symlink():
                        receipt_path.unlink()
                else:
                    _atomic_write(receipt_path, old_receipt)
            except (OSError, ValueError) as rollback_exc:
                rollback_errors.append(f"startup rollback failed: {rollback_exc}")
        try:
            _set_render_values(context.scene, previous_render)
            if previous_baseline is None:
                if RENDER_BASELINE_KEY in context.scene:
                    del context.scene[RENDER_BASELINE_KEY]
            else:
                context.scene[RENDER_BASELINE_KEY] = previous_baseline
        except _EXPECTED_ERRORS as rollback_exc:
            rollback_errors.append(f"scene rollback failed: {rollback_exc}")
        suffix = "; " + "; ".join(rollback_errors) if rollback_errors else ""
        raise RuntimeError(f"Startup save failed: {exc}{suffix}") from exc
    return str(backup) if backup is not None else ""


def _restore_startup():
    config, startup, receipt_path = _startup_paths()
    receipt_bytes = receipt_path.read_bytes()
    receipt = _read_startup_receipt(receipt_bytes)
    if not startup.exists() or _digest(startup.read_bytes()) != receipt.get("written_sha256"):
        raise ValueError("Startup was changed after this tool saved it; restore the backup manually to preserve your newer file")
    current = startup.read_bytes()
    original = None
    if receipt["previous"] == 'custom':
        name = receipt.get("backup", "")
        if (Path(name).name != name or not name.startswith("startup.helix-tools-backup-")
                or not name.endswith(".blend")):
            raise ValueError("The startup backup filename is invalid")
        backup = config / name
        if backup.is_symlink() or not backup.is_file():
            raise ValueError("The startup backup is missing or is not a regular file")
        original = backup.read_bytes()
        if _digest(original) != receipt.get("previous_sha256"):
            raise ValueError("The startup backup was changed; refusing to restore it")
    try:
        if original is None:
            startup.unlink()
        else:
            _atomic_write(startup, original)
        receipt_path.unlink()
    except (OSError, ValueError):
        _atomic_write(startup, current)
        _atomic_write(receipt_path, receipt_bytes)
        raise
    return "Restored previous custom startup; backup retained." if original is not None else "Restored Blender's factory startup by removing the tool-created custom startup."


class ALSC_OT_scene_quality(Operator):
    bl_idname = "alsc.eevee_scene_quality"
    bl_label = "Switch to Suggested Render Preset"
    bl_description = "Apply suggested Eevee settings to this scene; previous settings are saved in the blend file and can be restored or undone; startup is unchanged"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        try:
            _apply_render_preset(scene)
        except _EXPECTED_ERRORS as exc:
            _set_status(settings, "Render preset could not be applied.", 1, [str(exc)])
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _set_status(settings, "Applied suggested Eevee preset; previous render settings can be restored.")
        self.report({'INFO'}, settings.last_status)
        return {'FINISHED'}


class ALSC_OT_restore_render(Operator):
    bl_idname = "alsc.restore_render_settings"
    bl_label = "Restore Previous Render Settings"
    bl_description = "Restore only the render settings changed by the suggested preset, using the snapshot saved in this blend file"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and RENDER_BASELINE_KEY in context.scene

    def execute(self, context):
        settings = _settings(context.scene)
        try:
            _restore_render_preset(context.scene)
        except _EXPECTED_ERRORS as exc:
            _set_status(settings, "Previous render settings could not be restored.", 1, [str(exc)])
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _set_status(settings, "Restored previous render settings; startup is unchanged.")
        self.report({'INFO'}, settings.last_status)
        return {'FINISHED'}


class ALSC_OT_save_startup(Operator):
    bl_idname = "alsc.save_suggested_startup"
    bl_label = "Save Suggested Preset as Startup"
    bl_description = "Apply the preset and save this file's scenes and objects as Blender startup, after backing up any existing custom startup; changes to startup are restored separately from Undo"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600, confirm_text="Back Up and Save Startup")

    def draw(self, context):
        layout = self.layout
        layout.label(text="This replaces Blender's startup file.", icon='ERROR')
        layout.label(text="The entire current file becomes startup: scenes, objects and layout.")
        layout.label(text="Any existing custom startup is backed up first.")
        layout.label(text="Use Restore Previous Startup to reverse this disk change.")
        layout.label(text="Scene Undo does not restore the startup file.")
        try:
            config, startup, _ = _startup_paths()
            layout.label(text=f"Startup: {startup}")
            if not startup.exists():
                layout.label(text="No custom startup exists; restore will return to factory startup.")
            layout.label(text=f"Backups are retained in: {config}")
        except ValueError as exc:
            layout.label(text=str(exc), icon='ERROR')

    def execute(self, context):
        settings = _settings(context.scene)
        try:
            backup = _save_startup(context)
        except (OSError, RuntimeError, ValueError) as exc:
            settings.last_startup_status = str(exc)
            _set_status(settings, "Startup was not saved; see operation details.", 1, [str(exc)])
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings.last_startup_backup = backup
        settings.last_startup_status = (
            f"Saved suggested startup. Latest safety backup: {backup}"
            if backup else "Saved suggested startup. No custom startup existed; restore returns to factory startup."
        )
        _set_status(settings, "Saved suggested startup; use Restore Previous Startup to reverse it.")
        self.report({'INFO'}, settings.last_startup_status)
        return {'FINISHED'}


class ALSC_OT_restore_startup(Operator):
    bl_idname = "alsc.restore_previous_startup"
    bl_label = "Restore Previous Startup"
    bl_description = "Restore the startup from before the first tool save, or return to factory startup; repeated tool saves preserve this restore point; affects future launches, leaving the current scene open"

    @classmethod
    def poll(cls, context):
        try:
            return _startup_paths()[2].is_file()
        except ValueError:
            return False

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600, confirm_text="Restore Previous Startup")

    def draw(self, context):
        self.layout.label(text="Restore the startup that existed before the first tool save?")
        self.layout.label(text="The current scene stays open; backup files are retained.")
        self.layout.label(text="This changes future startups; restore current render settings separately.")
        self.layout.label(text="A startup changed since that save will be preserved.")
        try:
            config, _, receipt = _startup_paths()
            record = _read_startup_receipt(receipt.read_bytes())
            if record.get("previous") == 'factory':
                self.layout.label(text="Previous startup: Blender factory default (no custom file).")
            else:
                self.layout.label(text=f"Backup: {config / Path(record.get('backup', '')).name}")
        except (OSError, ValueError) as exc:
            self.layout.label(text=str(exc), icon='ERROR')

    def execute(self, context):
        settings = _settings(context.scene)
        try:
            message = _restore_startup()
        except (OSError, RuntimeError, ValueError) as exc:
            settings.last_startup_status = str(exc)
            _set_status(settings, "Startup could not be restored; see operation details.", 1, [str(exc)])
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings.last_startup_status = message
        _set_status(settings, message)
        self.report({'INFO'}, message)
        return {'FINISHED'}


class ALSC_OT_details(Operator):
    bl_idname = "alsc.status_details"
    bl_label = "Operation Details"
    bl_description = "Show the result and any skipped or failed lights"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        settings = _settings(context.scene)
        self.layout.label(text=settings.last_status)
        if settings.last_errors:
            for line in settings.last_errors.splitlines():
                self.layout.label(text=line, icon='ERROR')
        else:
            self.layout.label(text="No skipped lights or errors.", icon='CHECKMARK')
        if settings.last_startup_status:
            self.layout.label(text=settings.last_startup_status)
        if settings.last_startup_backup:
            self.layout.prop(settings, "last_startup_backup", text="Latest Safety Backup")

    def execute(self, context):
        return {'FINISHED'}


class ALSC_UL_collections(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        layout.prop(item, "collection", text="", icon='OUTLINER_COLLECTION')


class VIEW3D_PT_alsc_main(Panel):
    bl_label = "Light Control"
    bl_idname = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 40

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        targets = section(layout, "Target Lights", icon='LIGHT', section_id="target_lights")
        if targets is not None:
            targets.prop(settings, "scope", text="Include")
            if settings.scope == 'COLLECTIONS':
                row = targets.row()
                row.template_list("ALSC_UL_collections", "", settings, "collections", settings, "collection_index", rows=3)
                column = row.column(align=True)
                column.operator("alsc.collection_add", text="", icon='ADD')
                column.operator("alsc.collection_remove", text="", icon='REMOVE')
                if not any(item.collection is not None for item in settings.collections):
                    targets.label(text="Choose a collection.", icon='ERROR')
            objects = _object_scope(context.scene, settings)
            targets.label(text=f"{len(objects)} lights in scope", icon='LIGHT')
            enable = targets.column()
            enable.enabled = bool(objects)
            enable.scale_y = 1.25
            enable.operator(
                "alsc.enable_eevee_shadows",
                text="Enable Eevee Shadows" if context.scene.render.engine == 'BLENDER_EEVEE' else "Use Eevee & Enable Shadows",
                icon='LIGHT',
            )
            targets.operator("alsc.refresh_sizes", text="Refresh Targets", icon='FILE_REFRESH')
        details_text = f"Details ({settings.last_skipped} skipped)" if settings.last_skipped else "Operation Details"
        layout.operator("alsc.status_details", text=details_text,
                        icon='ERROR' if settings.last_skipped or settings.last_errors else 'INFO')


class VIEW3D_PT_alsc_sizes(Panel):
    bl_label = "Source Size"
    bl_idname = "VIEW3D_PT_alsc_sizes"
    bl_parent_id = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 10

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        title = {
            'AREA': "Area Dimensions", 'POINT': "Point Radius",
            'SPOT': "Spot Radius", 'ALL3': "Dimensions / Radii",
        }[settings.size_light_types]
        icon = {'AREA': 'LIGHT_AREA', 'POINT': 'LIGHT_POINT',
                'SPOT': 'LIGHT_SPOT', 'ALL3': 'LIGHT'}[settings.size_light_types]
        sizes = section(layout, title, icon=icon, section_id="source_size")
        if sizes is not None:
            sizes.prop(settings, "size_light_types")
            sizes.prop(settings, "size_reduction", text="Reduction (Stops)", slider=True)
            percentage = math.exp2(-float(settings.size_reduction)) * 100.0
            sizes.label(text=f"Requested scale: {percentage:.3g}%")
            sizes.prop(settings, "minimum_size")
        baseline = section(layout, "Saved Sizes", icon='FILE_BLEND', section_id="saved_baseline")
        if baseline is not None:
            capture = baseline.column(align=True)
            capture.enabled = any(obj.data.type in _size_types(settings)
                                  for obj in _object_scope(context.scene, settings))
            capture.operator("alsc.capture_sizes", text="Set Baseline", icon='IMPORT')
            baseline.operator("alsc.restore_sizes", text="Restore Sizes", icon='LOOP_BACK')


class VIEW3D_PT_alsc_shadows(Panel):
    bl_label = "Eevee Shadows"
    bl_idname = "VIEW3D_PT_alsc_shadows"
    bl_parent_id = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 20
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        shadows = section(layout, "Shadow Detail", icon='LIGHT', section_id="shadow_detail")
        if shadows is not None:
            shadows.prop(settings, "shadow_preset")
            shadows.prop(settings, "shadow_filter")
            shadows.prop(settings, "shadow_resolution")
            shadows.prop(settings, "absolute_resolution")
            shadows.prop(settings, "shadow_jitter")
            blur = shadows.column()
            blur.active = settings.shadow_jitter
            blur.prop(settings, "shadow_overblur")
        apply = section(layout, "Apply Shadows", icon='CHECKMARK', section_id="apply_shadows")
        if apply is not None:
            apply.prop(settings, "shadow_scope", text="Targets")
            apply.prop(settings, "shadow_casting", text="Casting")
            every = settings.shadow_scope == 'ALL' or settings.scope == 'ALL'
            action = apply.column()
            action.enabled = bool(_object_scope(context.scene, settings, every_scene_light=every))
            action.operator("alsc.apply_shadows", text="Apply to Scene Lights" if every else "Apply to Collection Lights", icon='CHECKMARK')
            if context.scene.render.engine != 'BLENDER_EEVEE':
                apply.label(text="Used by Eevee.", icon='INFO')


class VIEW3D_PT_alsc_scene(Panel):
    bl_label = "Render & Startup"
    bl_idname = "VIEW3D_PT_alsc_scene"
    bl_parent_id = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 30
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        quality = section(layout, "Eevee Render Preset", icon='SCENE', section_id="scene_quality")
        if quality is not None:
            quality.label(text="128 samples; 4 rays; 12 steps")
            quality.label(text="2 GB shadows; 1 GB probes")
            quality.prop(settings, "viewport_jitter")
            quality.operator("alsc.eevee_scene_quality", icon='SCENE')
            quality.operator("alsc.restore_render_settings", text="Restore Render Settings", icon='LOOP_BACK')
            quality.label(text="Reversible: Restore or Undo.", icon='INFO')
        startup = section(layout, "Blender Startup", icon='FILE_BLEND', section_id="startup", default_closed=True)
        if startup is not None:
            startup.label(text="Saves scene + layout.")
            startup.operator("alsc.save_suggested_startup", text="Back Up & Save Startup", icon='FILE_TICK')
            startup.operator("alsc.restore_previous_startup", text="Restore Startup", icon='LOOP_BACK')


CLASSES = (
    ALSC_CollectionItem, ALSC_ActiveLight, ALSC_Settings,
    ALSC_OT_collection_add, ALSC_OT_collection_remove, ALSC_OT_capture,
    ALSC_OT_restore, ALSC_OT_apply_sizes, ALSC_OT_shadows, ALSC_OT_enable_eevee_shadows,
    ALSC_OT_scene_quality, ALSC_OT_restore_render, ALSC_OT_save_startup,
    ALSC_OT_restore_startup, ALSC_OT_details, ALSC_UL_collections,
    VIEW3D_PT_alsc_main, VIEW3D_PT_alsc_sizes, VIEW3D_PT_alsc_shadows,
    VIEW3D_PT_alsc_scene,
)


def register():
    # A second register() call is harmless. No sizing occurs at registration.
    global _SCENE_PROPERTY_OWNED
    if _SCENE_PROPERTY_OWNED:
        return
    if hasattr(bpy.types.Scene, SCENE_PROPERTY):
        raise RuntimeError(
            "Another copy of Light Size and Shadow Control is enabled. "
            "Disable the older copy before enabling this add-on."
        )
    # Blender replaces an existing operator with the same bl_idname without
    # raising an error. Check canonical RNA identifiers before registering any
    # class, including a foreign operator whose Python class name is different.
    for cls in CLASSES:
        base = next(base for base in (Operator, Panel, PropertyGroup, UIList)
                    if issubclass(cls, base))
        if base is Operator:
            namespace, name = cls.bl_idname.split('.')
            identifier = f"{namespace.upper()}_OT_{name}"
        else:
            identifier = getattr(cls, "bl_idname", "") or cls.__name__
        if base.bl_rna_get_subclass_py(identifier, None) is not None:
            raise RuntimeError(
                f"Another add-on already registers {identifier}. "
                "Disable the conflicting or older copy before enabling this add-on."
            )
    registered = []
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        setattr(bpy.types.Scene, SCENE_PROPERTY, PointerProperty(type=ALSC_Settings))
        _SCENE_PROPERTY_OWNED = True
        _REGISTERED_CLASSES.extend(registered)
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    # Deliberately keep emitter dimensions and saved ID-property baselines.
    # Removing an add-on should not unexpectedly alter a user's lighting.
    global _SCENE_PROPERTY_OWNED
    if _SCENE_PROPERTY_OWNED and hasattr(bpy.types.Scene, SCENE_PROPERTY):
        delattr(bpy.types.Scene, SCENE_PROPERTY)
    _SCENE_PROPERTY_OWNED = False
    for cls in reversed(_REGISTERED_CLASSES):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
    _REGISTERED_CLASSES.clear()
    _BUSY_SCENES.clear()


if __name__ == "__main__":
    register()
