# SPDX-License-Identifier: GPL-3.0-or-later
"""Light-source sizing and Eevee shadow controls for Blender 5.2.2 LTS.

Physical emitter dimensions are always calculated from a saved baseline, never
from the previous slider result. No handlers run when opening a project.
"""

bl_info = {
    "name": "Light Size and Shadow Control",
    "author": "Codex, Helices3D",
    "version": (1, 2, 0),
    "blender": (5, 2, 2),
    "location": "3D Viewport > Sidebar > Helix Tools",
    "description": "Scale area, point and spot light sources and apply Eevee shadows",
    "category": "Lighting",
    "license": "GPL-3.0-or-later",
}

import math
import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
    IntProperty, PointerProperty, StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from ._ui import section, setup_layout

BASELINE_KEY = "_area_light_shadow_control_baseline_v1"
SCENE_PROPERTY = "area_light_shadow_control"
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
        name="Size Controls For", items=[
            ('AREA', "Area Lights", "Resize area emitter dimensions only"),
            ('POINT', "Point Lights", "Resize point-light source radii only"),
            ('SPOT', "Spot Lights", "Resize spot-light source radii only, preserving cone angle and blend"),
            ('ALL3', "Area, Point and Spot", "Resize all three source types; sun lights are excluded"),
        ], default='AREA', update=_scope_update, options=set(),
    )
    scope: EnumProperty(
        name="Lights to Control", items=[
            ('ALL', "Entire Scene", "Include every light object in the current scene"),
            ('COLLECTIONS', "Chosen Collections", "Include the listed collections and all descendants"),
        ], default='ALL', update=_scope_update,
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
        name="Starting Point", items=[
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
        name="Resolution Limit", default=0.001, min=0.000001, max=100.0,
        soft_max=0.01, subtype='DISTANCE', unit='LENGTH', precision=6, options=set(), update=_shadow_custom_update,
        description="Minimum shadow-map detail limit; adaptive mode accounts for screen coverage, and lower limits can increase shadow-pool use; 0.001 displays as 1 mm with metre scene units",
    )
    absolute_resolution: BoolProperty(
        name="Absolute Resolution Limit", default=False, options=set(), update=_shadow_custom_update,
        description="For area, point and spot lights: anchor resolution one Blender unit from the light origin instead of using adaptive screen coverage; can increase shadow-memory requirements; sun lights have no absolute-limit setting",
    )
    shadow_overblur: FloatProperty(
        name="Overblur", default=0.0, min=0.0, max=100.0, subtype='PERCENTAGE',
        precision=1, options=set(), update=_shadow_custom_update,
        description="Extra jitter-shadow blur; 0% preserves physical emitter-size softness",
    )
    shadow_jitter: BoolProperty(
        name="Jitter", default=True, options=set(), update=_shadow_custom_update,
        description="Enable per-light jittered shadows for more accurate soft shadows; increases rendering cost",
    )
    shadow_casting: EnumProperty(
        name="Cast Shadows", items=[
            ('KEEP', "Preserve Each Light", "Keep the existing shadow on/off state; recommended for rigs with shadow-free fill and rim lights"),
            ('ON', "Enable on All Targets", "Enable shadow casting on every included light"),
            ('OFF', "Disable on All Targets", "Disable shadow casting on every included light"),
        ], default='KEEP', options=set(),
    )
    shadow_scope: EnumProperty(
        name="Shadow Targets", items=[
            ('MATCH', "Same Scope", "Apply to all light types within the chosen size-control scope"),
            ('ALL', "Every Scene Light", "Apply to all light types in the scene, including lights outside the chosen collections"),
        ], default='MATCH', options=set(),
    )
    viewport_jitter: BoolProperty(
        name="Jitter in Viewport", default=True, options=set(),
        description="Also enable jittered shadows during viewport interaction; can noticeably slow navigation",
    )
    last_status: StringProperty(default="Move the slider to capture baselines automatically.", options={'HIDDEN'})
    last_errors: StringProperty(options={'HIDDEN'})
    last_skipped: IntProperty(default=0, options={'HIDDEN'})


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


class ALSC_OT_shadows(Operator):
    bl_idname = "alsc.apply_shadows"
    bl_label = "Apply Shadow Settings"
    bl_description = "Apply these shadow values to every light type in the chosen scope; shadow casting is preserved unless explicitly changed"
    bl_options = {'REGISTER', 'UNDO'}

    every_scene_light: BoolProperty(default=False, options={'SKIP_SAVE'})

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        every = self.every_scene_light or settings.shadow_scope == 'ALL'
        lights, skipped, isolated, errors = _prepare_lights(scene, settings, size_only=False, every_scene_light=every)
        values = {
            "shadow_filter_radius": settings.shadow_filter,
            "shadow_maximum_resolution": settings.shadow_resolution,
            "use_absolute_resolution": settings.absolute_resolution,
            "shadow_jitter_overblur": settings.shadow_overblur,
            "use_shadow_jitter": settings.shadow_jitter,
        }
        if settings.shadow_casting != 'KEEP':
            values["use_shadow"] = settings.shadow_casting == 'ON'
        applied = 0
        suns = 0
        for light in lights:
            light_values = dict(values)
            # Absolute Resolution Limit belongs to local lights in Blender5.2.
            # Sun still receives every applicable shadow control.
            if light.type == 'SUN':
                light_values.pop("use_absolute_resolution")
                suns += 1
            # Save values first so an unexpected property failure does not leave
            # only part of the requested preset on this light.
            previous = {}
            try:
                previous = {name: getattr(light, name) for name in light_values}
                for name, value in light_values.items():
                    setattr(light, name, value)
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
        self.report({'WARNING'} if errors else {'INFO'}, settings.last_status)
        return {'FINISHED'}


class ALSC_OT_scene_quality(Operator):
    bl_idname = "alsc.eevee_scene_quality"
    bl_label = "Apply Eevee Scene Quality"
    bl_description = "Switch to Eevee and set 128 render samples, 4 shadow rays, 12 steps, 100% resolution, 2 GB shadow pool, high quality normals, 16x filtering, GPU compositor, and automatic pixel size"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene, settings = context.scene, _settings(context.scene)
        values = [
            (scene.render, "engine", 'BLENDER_EEVEE'),
            (scene.eevee, "use_shadows", True),
            (scene.eevee, "taa_render_samples", 128),
            (scene.eevee, "taa_samples", 64),
            (scene.eevee, "shadow_ray_count", 4),
            (scene.eevee, "shadow_step_count", 12),
            (scene.eevee, "shadow_resolution_scale", 1.0),
            (scene.eevee, "shadow_pool_size", '2048'),
            (scene.eevee, "gi_irradiance_pool_size", '1024'),
            (scene.eevee, "use_shadow_jitter_viewport", settings.viewport_jitter),
            (scene.render, "use_high_quality_normals", True),
            (scene.render, "anisotropic_filter", 'FILTER_16'),
            (scene.render, "compositor_device", 'GPU'),
            (scene.render, "compositor_precision", 'AUTO'),
            (scene.render, "preview_pixel_size", 'AUTO'),
        ]
        previous = []
        try:
            previous = [(owner, name, getattr(owner, name)) for owner, name, _ in values]
            for owner, name, value in values:
                setattr(owner, name, value)
        except _EXPECTED_ERRORS as exc:
            errors = [f"Scene quality update failed: {exc}"]
            for owner, name, value in reversed(previous):
                try:
                    setattr(owner, name, value)
                except _EXPECTED_ERRORS as rollback_exc:
                    errors.append(f"Could not restore {name}: {rollback_exc}")
            _set_status(settings, "Scene quality could not be applied; see details.", 1, errors)
            self.report({'ERROR'}, errors[0])
            return {'CANCELLED'}
        _set_status(settings, "Applied Eevee quality and performance settings to this scene.")
        self.report({'INFO'}, settings.last_status)
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

    def execute(self, context):
        return {'FINISHED'}


class ALSC_UL_collections(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        layout.prop(item, "collection", text="", icon='OUTLINER_COLLECTION')


class VIEW3D_PT_alsc_main(Panel):
    bl_label = "Light Size and Shadow Control"
    bl_idname = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 40

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        layout.label(text="Resize sources and tune Eevee shadows.")
        targets = section(layout, "Target Lights", icon='LIGHT')
        targets.prop(settings, "size_light_types", text="Source Types")
        targets.prop(settings, "scope", text="Scope")
        if settings.scope == 'COLLECTIONS':
            row = targets.row()
            row.template_list("ALSC_UL_collections", "", settings, "collections", settings, "collection_index", rows=3)
            column = row.column(align=True)
            column.operator("alsc.collection_add", text="", icon='ADD')
            column.operator("alsc.collection_remove", text="", icon='REMOVE')
            targets.label(text="Includes child collections.", icon='INFO')
            if not any(item.collection is not None for item in settings.collections):
                targets.label(text="Choose at least one collection.", icon='ERROR')
        objects = _object_scope(context.scene, settings)
        sized = sum(obj.data.type in _size_types(settings) for obj in objects)
        targets.label(text=f"Size targets: {sized}; all types: {len(objects)}")
        targets.label(text="Changing targets restores source sizes.", icon='INFO')
        targets.label(text="Size Reduction also resets to 0.")


class VIEW3D_PT_alsc_sizes(Panel):
    bl_label = "Light Source Sizes"
    bl_idname = "VIEW3D_PT_alsc_sizes"
    bl_parent_id = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 10

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        sizes = section(layout, "Source Size", icon='LIGHT_AREA')
        sizes.prop(settings, "size_reduction", text="Reduction (Stops)", slider=True)
        percentage = math.exp2(-float(settings.size_reduction)) * 100.0
        sizes.label(text=f"Requested size: {percentage:.3g}% of baseline")
        sizes.label(text="1 stop = half; 2 stops = quarter.")
        if settings.size_light_types == 'AREA':
            sizes.label(text="Controls area emitter dimensions.")
        elif settings.size_light_types in {'POINT', 'SPOT'}:
            sizes.label(text="Controls source radius.")
            if settings.size_light_types == 'SPOT':
                sizes.label(text="Spot cone angle is unchanged.")
        else:
            sizes.label(text="Area dimensions and point/spot radii.")
        sizes.prop(settings, "minimum_size")
        sizes.label(text="Sun angular size is not controlled.", icon='INFO')
        baseline = section(layout, "Saved Baseline", icon='FILE_BLEND')
        row = baseline.row(align=True)
        row.operator("alsc.capture_sizes", text="Capture Sizes", icon='IMPORT')
        row.operator("alsc.restore_sizes", text="Restore", icon='LOOP_BACK')
        baseline.operator("alsc.refresh_sizes", icon='FILE_REFRESH')
        baseline.label(text="Captured automatically; saved in this .blend.")
        baseline.label(text="Recapture after editing source type or shape.")
        if settings.last_skipped:
            layout.label(text=f"{settings.last_skipped} skipped; see details.", icon='ERROR')
        layout.operator("alsc.status_details", text="Last Operation Details", icon='INFO')


class VIEW3D_PT_alsc_shadows(Panel):
    bl_label = "Shadow Settings"
    bl_idname = "VIEW3D_PT_alsc_shadows"
    bl_parent_id = "VIEW3D_PT_alsc_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Helix Tools"
    bl_order = 20

    def draw(self, context):
        layout, settings = self.layout, _settings(context.scene)
        setup_layout(layout)
        shadows = section(layout, "Eevee Shadow Detail", icon='LIGHT')
        shadows.prop(settings, "shadow_preset")
        if settings.shadow_preset == 'CRISP':
            shadows.label(text="Finer detail can cost memory.", icon='INFO')
            shadows.label(text="Check for shadow aliasing.")
        shadows.prop(settings, "shadow_filter")
        shadows.prop(settings, "shadow_resolution")
        shadows.prop(settings, "absolute_resolution")
        shadows.label(text="Absolute limit excludes Sun lights.")
        shadows.prop(settings, "shadow_overblur")
        shadows.prop(settings, "shadow_jitter")
        apply = section(layout, "Apply Shadows", icon='CHECKMARK')
        apply.prop(settings, "shadow_casting")
        apply.prop(settings, "shadow_scope")
        apply.operator("alsc.apply_shadows", icon='LIGHT')
        apply.label(text="Includes every light type in the target scope.")
        if context.scene.render.engine != 'BLENDER_EEVEE':
            apply.label(text="Shadow detail settings are used by Eevee.", icon='INFO')


class VIEW3D_PT_alsc_scene(Panel):
    bl_label = "Eevee Scene Quality"
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
        quality = section(layout, "Scene Quality Preset", icon='SCENE')
        quality.label(text="Switches this scene to Eevee.")
        quality.label(text="128 render samples; 4 rays; 12 steps")
        quality.label(text="2 GB shadows; 1 GB volume probes")
        quality.label(text="High quality normals; 16x filtering")
        quality.label(text="GPU compositor; automatic precision")
        quality.label(text="Automatic viewport pixel size")
        quality.prop(settings, "viewport_jitter")
        quality.operator("alsc.eevee_scene_quality", icon='SCENE')


CLASSES = (
    ALSC_CollectionItem, ALSC_ActiveLight, ALSC_Settings,
    ALSC_OT_collection_add, ALSC_OT_collection_remove, ALSC_OT_capture,
    ALSC_OT_restore, ALSC_OT_apply_sizes, ALSC_OT_shadows,
    ALSC_OT_scene_quality, ALSC_OT_details, ALSC_UL_collections,
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
