"""Conservative, reversible culling over sampled camera animation."""

bl_info = {
    "name": "Camera Timeline Culler",
    "author": "OpenAI, Helices3D",
    "version": (2, 2, 1),
    "blender": (5, 2, 2),
    "location": "3D View > Sidebar > Helix Tools",
    "description": "Hide geometry outside every sampled camera view; restore it at any time",
    "category": "Object",
}

import json
import math

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, PointerProperty
from mathutils import Vector

from ._ui import section, setup_layout
from ._updates import create_updater


_UPDATER = create_updater(__package__, __file__)


COLLECTION_NAME = "CAMERA CULLED | Outside View"
TAG = "_camera_cull_camera"
PREV_COLLECTIONS = "_camera_cull_original_collections"
PREV_VIEWPORT = "_camera_cull_previous_hide_viewport"
PREV_RENDER = "_camera_cull_previous_hide_render"
COLLECTION_REFS = "_camera_cull_original_collection_refs"
OWNER = "_camera_cull_owner_scene"
MANAGED_KEYS = (TAG, PREV_COLLECTIONS, PREV_VIEWPORT, PREV_RENDER, COLLECTION_REFS, OWNER)
GEOMETRY_TYPES = {"MESH", "CURVE", "FONT", "SURFACE", "META", "CURVES", "POINTCLOUD", "VOLUME"}


def _scene_collections(scene):
    result = set()
    pending = [scene.collection]
    while pending:
        collection = pending.pop()
        if collection not in result:
            result.add(collection)
            pending.extend(collection.children)
    return result


def _collection_scene_users(collection):
    return {scene for scene in bpy.data.scenes if collection in _scene_collections(scene)}


def _scene_group(scene):
    """Find this scene's results, including exclusively owned legacy results."""
    group = scene.camera_cull_settings.collection
    if group is not None and group.get(OWNER) == scene and group in scene.collection.children.values():
        return group
    for group in scene.collection.children:
        if group.get(OWNER) == scene:
            return group
        if (group.name.startswith(COLLECTION_NAME) and OWNER not in group and group.objects
                and all(TAG in obj and OWNER not in obj for obj in group.objects)
                and _collection_scene_users(group) == {scene}):
            return group
    return None


def _group_error(group, scene):
    if group is None:
        return None
    if _collection_scene_users(group) != {scene}:
        return "The cull collection is shared with another scene; make it scene-local first"
    if group.children or any(TAG not in obj or obj.get(OWNER, scene) != scene for obj in group.objects):
        return "The cull collection contains unrelated content; move that content out first"
    return None


def _original_collections(obj, group, scene):
    collections = []
    refs = obj.get(COLLECTION_REFS)
    if hasattr(refs, "values"):
        for ref in refs.values():
            if isinstance(ref, bpy.types.Collection):
                collections.append(ref)
            elif isinstance(ref, bpy.types.Scene):
                collections.append(ref.collection)
    elif PREV_COLLECTIONS in obj:
        try:
            names = json.loads(obj[PREV_COLLECTIONS])
            if isinstance(names, list):
                for name in names:
                    if not isinstance(name, str):
                        continue
                    collection = bpy.data.collections.get(name)
                    if collection is None and name == scene.collection.name:
                        collection = scene.collection
                    if collection is not None:
                        collections.append(collection)
        except (TypeError, ValueError):
            pass
    # Preserve memberships added by the user since the preceding scan.
    collections.extend(c for c in obj.users_collection if c != group)
    return tuple(dict.fromkeys(collections)) or (scene.collection,)


def _baseline(obj, group, scene):
    return (_original_collections(obj, group, scene),
            bool(obj.get(PREV_VIEWPORT, obj.hide_viewport)),
            bool(obj.get(PREV_RENDER, obj.hide_render)))


def _has_unbounded_instances(obj):
    """Viewport bounds must also account for the rendered geometry."""
    if obj.type == "VOLUME" or obj.instance_type != "NONE" or obj.particle_systems:
        return True
    if (obj.type in {"CURVE", "SURFACE", "FONT"} and obj.data.render_resolution_u
            and obj.data.render_resolution_u != obj.data.resolution_u):
        return True
    for modifier in obj.modifiers:
        if modifier.show_render != modifier.show_viewport:
            return True
        if modifier.type in {"SUBSURF", "MULTIRES"} and modifier.render_levels != modifier.levels:
            return True
        if modifier.type != "NODES" or not modifier.show_render or modifier.node_group is None:
            continue
        pending, visited = [modifier.node_group], set()
        while pending:
            tree = pending.pop()
            if tree in visited:
                continue
            visited.add(tree)
            for node in tree.nodes:
                identifier = node.bl_idname
                if (identifier.startswith("GeometryNode") and "Instance" in identifier
                        or identifier in {"GeometryNodeCollectionInfo", "GeometryNodeObjectInfo", "GeometryNodeIsViewport"}):
                    return True
                nested = getattr(node, "node_tree", None)
                if nested is not None:
                    pending.append(nested)
    return False


def _dependency_sources(scene, baselines):
    """Moving source geometry can change visible instances or node results."""
    objects, collections = set(), set()
    pending_collections = []

    def add_reference(value):
        if isinstance(value, bpy.types.Collection):
            pending_collections.append(value)
        elif isinstance(value, bpy.types.Object):
            objects.add(value)
            if value.instance_collection is not None:
                pending_collections.append(value.instance_collection)

    def add_pointer_properties(value):
        for prop in value.bl_rna.properties:
            if prop.type == "POINTER":
                add_reference(getattr(value, prop.identifier, None))

    visited_trees = set()
    for obj in scene.objects:
        if obj.instance_type == "COLLECTION":
            add_reference(obj.instance_collection)
        for modifier in obj.modifiers:
            # Includes Boolean collection operands and mesh-based deform targets.
            add_pointer_properties(modifier)
            if modifier.type != "NODES" or modifier.node_group is None:
                continue
            # Group interface inputs are stored on the modifier as ID properties.
            try:
                values = tuple(modifier.values())
            except TypeError:
                values = ()  # A node group without interface sockets has no ID properties.
            for value in values:
                add_reference(value)
            pending_trees = [modifier.node_group]
            while pending_trees:
                tree = pending_trees.pop()
                if tree in visited_trees:
                    continue
                visited_trees.add(tree)
                for node in tree.nodes:
                    add_pointer_properties(node)
                    for socket in node.inputs:
                        add_reference(getattr(socket, "default_value", None))
                    nested = getattr(node, "node_tree", None)
                    if nested is not None:
                        pending_trees.append(nested)
    while pending_collections:
        collection = pending_collections.pop()
        if collection in collections:
            continue
        collections.add(collection)
        pending_collections.extend(collection.children)
        for obj in collection.objects:
            objects.add(obj)
            if obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
                pending_collections.append(obj.instance_collection)
    # A historical cull may already have moved the source out of its collection.
    for obj, baseline in baselines.items():
        if any(collection in collections for collection in baseline[0]):
            objects.add(obj)
    return objects


def _camera_limits(camera, scene, tolerance):
    data = camera.data
    if scene.render.use_multiview or data.type not in {"PERSP", "ORTHO"}:
        return None
    try:
        # Blender's camera projection ignores camera-object scale.
        inverse = camera.matrix_world.normalized().inverted()
    except ValueError:
        return None
    if not all(math.isfinite(value) for row in inverse for value in row):
        return None
    frame = [Vector(corner) for corner in data.view_frame(scene=scene)]
    if not frame or not all(math.isfinite(value) for corner in frame for value in corner):
        return None
    if data.type == "PERSP":
        if any(-corner.z <= 0 for corner in frame):
            return None
        xs = [corner.x / -corner.z for corner in frame]
        ys = [corner.y / -corner.z for corner in frame]
    else:
        xs, ys = [corner.x for corner in frame], [corner.y for corner in frame]
    left, right, bottom, top = min(xs), max(xs), min(ys), max(ys)
    dx, dy = (right - left) * tolerance, (top - bottom) * tolerance
    return (data.type, left - dx, right + dx, bottom - dy, top + dy,
            data.clip_start, data.clip_end, inverse)


def _intersects_camera(obj, depsgraph, limits):
    if limits is None or _has_unbounded_instances(obj):
        return True
    kind, left, right, bottom, top, near, far, inverse = limits
    evaluated = obj.evaluated_get(depsgraph)
    corners = list(evaluated.bound_box)
    if (len(corners) != 8 or all(tuple(corner) == (-1.0, -1.0, -1.0) for corner in corners)
            or not all(math.isfinite(value) for corner in corners for value in corner)):
        return True
    points = [inverse @ evaluated.matrix_world @ Vector(corner) for corner in corners]
    if not all(math.isfinite(value) for point in points for value in point):
        return True
    # A separating frustum plane is proof that the entire bounding box is outside.
    # Corners inside the image are not required: large intersecting boxes are kept.
    if kind == "PERSP":
        outside = (all(p.x < left * -p.z for p in points)
                   or all(p.x > right * -p.z for p in points)
                   or all(p.y < bottom * -p.z for p in points)
                   or all(p.y > top * -p.z for p in points))
    else:
        outside = (all(p.x < left for p in points) or all(p.x > right for p in points)
                   or all(p.y < bottom for p in points) or all(p.y > top for p in points))
    return not (outside or all(-p.z < near for p in points) or all(-p.z > far for p in points))


def _range(scene):
    settings = scene.camera_cull_settings
    if settings.scope == "PREVIEW":
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


def _samples(scene, substeps):
    start, end = _range(scene)
    for frame in range(start, end):
        for substep in range(substeps):
            yield frame, substep / substeps
    yield end, 0.0


def _set_memberships(obj, collections):
    for collection in collections:
        if obj.name not in collection.objects:
            collection.objects.link(obj)
    for collection in tuple(obj.users_collection):
        if collection not in collections:
            collection.objects.unlink(obj)


def _snapshot(obj):
    tags = {}
    for key in MANAGED_KEYS:
        if key in obj:
            value = obj[key]
            tags[key] = value.to_dict() if hasattr(value, "to_dict") else value
    return tuple(obj.users_collection), obj.hide_viewport, obj.hide_render, tags


def _restore_snapshot(obj, state):
    collections, viewport, render, tags = state
    _set_memberships(obj, collections)
    obj.hide_viewport, obj.hide_render = viewport, render
    _clear_tags(obj)
    for key, value in tags.items():
        obj[key] = value


def _clear_tags(obj):
    for key in MANAGED_KEYS:
        if key in obj:
            del obj[key]


def _restore_object(obj, group, baseline, scene):
    collections, viewport, render = baseline
    shared = any(other != scene for other in obj.users_scene)
    targets = list(c for c in collections if c.is_editable and
                   (not shared or _collection_scene_users(c) <= {scene}))
    # A removed/renamed source collection never leaves an object orphaned.
    if not targets:
        targets.append(scene.collection)
    for collection in targets:
        if obj.name not in collection.objects:
            collection.objects.link(obj)
    if group is not None and obj.name in group.objects:
        group.objects.unlink(obj)
    if not shared:
        obj.hide_viewport, obj.hide_render = viewport, render
    _clear_tags(obj)


class CameraCullSettings(bpy.types.PropertyGroup):
    tolerance: FloatProperty(
        name="Frame Margin", description="Extra percent of image width and height retained on each side; protects near-edge geometry",
        default=5.0, min=0.0, max=100.0, soft_max=25.0, subtype="PERCENTAGE",
    )
    substeps: IntProperty(
        name="Samples per Frame", description="Samples each frame interval; increase for fast motion or brief camera appearances",
        default=2, min=1, max=16,
    )
    scope: EnumProperty(
        name="Scan Range", items=(
            ("TIMELINE", "Scene Timeline", "Scan the scene's start and end frames"),
            ("PREVIEW", "Preview Range", "Scan preview start and end; objects visible elsewhere may be hidden"),
        ), default="TIMELINE",
    )
    collection: PointerProperty(type=bpy.types.Collection, options={"HIDDEN"})


class OBJECT_OT_camera_cull_timeline(bpy.types.Operator):
    bl_idname = "object.camera_cull_timeline"
    bl_label = "Scan & Cull"
    bl_description = "Hide geometry outside every sampled camera view; follows timeline camera cuts. Rescan after scene changes; restore collections to reverse"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.scene is None:
            cls.poll_message_set("Open a scene to scan")
            return False
        if context.mode != "OBJECT":
            cls.poll_message_set("Switch to Object Mode to scan the scene")
            return False
        return True

    def execute(self, context):
        scene, wm = context.scene, context.window_manager
        settings = scene.camera_cull_settings
        start, end = _range(scene)
        if start > end:
            self.report({"ERROR"}, "Scan start must not exceed scan end")
            return {"CANCELLED"}
        if scene.camera is None and not any(marker.camera for marker in scene.timeline_markers):
            self.report({"ERROR"}, "Assign a scene camera or a camera to a timeline marker")
            return {"CANCELLED"}
        group = _scene_group(scene)
        error = _group_error(group, scene)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        objects = [obj for obj in scene.objects if obj.type in GEOMETRY_TYPES
                   or group is not None and obj.name in group.objects]
        editable = [obj for obj in objects if obj.is_editable and
                    all(collection.is_editable for collection in obj.users_collection)]
        if not editable:
            self.report({"WARNING"}, "No editable geometry in this scene")
            return {"CANCELLED"}
        baseline = {obj: _baseline(obj, group, scene) for obj in editable}
        snapshots = {obj: _snapshot(obj) for obj in editable}
        old_frame, old_subframe, old_camera = scene.frame_current, scene.frame_subframe, scene.camera
        group_flags = (group.hide_viewport, group.hide_render) if group else None
        group_owner = group.get(OWNER) if group else None
        dependency_sources = _dependency_sources(scene, baseline)
        remaining = {obj for obj in editable if obj.type in GEOMETRY_TYPES
                     and not any(other != scene for other in obj.users_scene)
                     and not _has_unbounded_instances(obj)
                     and obj not in dependency_sources
                     and not baseline[obj][1]}
        total = (end - start) * settings.substeps + 1
        sampled, cameras, failure = 0, set(), None
        progress_started = False
        try:
            # Evaluate original collection membership, including Collection Info dependencies.
            for obj in editable:
                if (group is not None and obj.name in group.objects
                        and not any(other != scene for other in obj.users_scene)):
                    _set_memberships(obj, baseline[obj][0])
                    obj.hide_viewport, obj.hide_render = baseline[obj][1:]
            wm.progress_begin(0, total)
            progress_started = True
            for index, (frame, subframe) in enumerate(_samples(scene, settings.substeps), 1):
                scene.frame_set(frame, subframe=subframe)
                sampled = index
                depsgraph = context.evaluated_depsgraph_get()
                remaining.difference_update(_dependency_sources(scene, baseline))
                for instance in depsgraph.object_instances:
                    if instance.is_instance:
                        remaining.discard(instance.object.original)
                        if instance.instance_object is not None:
                            remaining.discard(instance.instance_object.original)
                camera = scene.camera
                if camera is None:
                    remaining.clear()
                    break
                cameras.add(camera.name)
                limits = _camera_limits(camera.evaluated_get(depsgraph), scene, settings.tolerance / 100.0)
                if limits is None:
                    remaining.clear()
                    break
                for obj in tuple(remaining):
                    if not obj.visible_get(view_layer=context.view_layer) or _intersects_camera(obj, depsgraph, limits):
                        remaining.remove(obj)
                if index % 10 == 0 or index == total:
                    wm.progress_update(index)
                if not remaining:
                    break
        except Exception as exc:
            failure = exc
        finally:
            # One cleanup failure must not skip restoring the frame or other objects.
            try:
                scene.frame_set(old_frame, subframe=old_subframe)
                scene.camera = old_camera
            except Exception as exc:
                failure = failure or exc
            # Apply visibility snapshots after evaluating the old frame, so manual
            # overrides to animated flags are not overwritten by that evaluation.
            for obj, state in snapshots.items():
                try:
                    _restore_snapshot(obj, state)
                except Exception as exc:
                    failure = failure or exc
            if group is not None:
                group.hide_viewport, group.hide_render = group_flags
            if progress_started:
                wm.progress_end()
        if failure:
            self.report({"ERROR"}, f"Scan stopped; previous results retained: {failure}")
            return {"CANCELLED"}

        new_group = group is None
        try:
            if new_group:
                group = bpy.data.collections.new(COLLECTION_NAME)
                scene.collection.children.link(group)
                group.color_tag = "COLOR_01"
            group[OWNER] = scene
            for obj in editable:
                original = baseline[obj]
                if obj not in remaining:
                    if obj.get(OWNER, scene) == scene and (TAG in obj or obj.name in group.objects):
                        _restore_object(obj, group, original, scene)
                    continue
                collections, viewport, render = original
                obj[PREV_COLLECTIONS] = json.dumps([collection.name for collection in collections])
                # Master collections are embedded IDs; refer to their Scene instead.
                obj[COLLECTION_REFS] = {str(index): scene if collection == scene.collection else collection
                                        for index, collection in enumerate(collections)}
                obj[PREV_VIEWPORT], obj[PREV_RENDER] = viewport, render
                obj[TAG], obj[OWNER] = ", ".join(sorted(cameras)), scene
                _set_memberships(obj, (group,))
                obj.hide_viewport, obj.hide_render = viewport, render
            group.hide_viewport = group.hide_render = True
            settings.collection = group
        except Exception as exc:
            for obj, state in snapshots.items():
                _restore_snapshot(obj, state)
            if new_group and group is not None:
                bpy.data.collections.remove(group)
            elif group is not None:
                group.hide_viewport, group.hide_render = group_flags
                if group_owner is None:
                    if OWNER in group:
                        del group[OWNER]
                else:
                    group[OWNER] = group_owner
            self.report({"ERROR"}, f"Could not apply culling; original state restored: {exc}")
            return {"CANCELLED"}
        scene["camera_cull_last_samples"] = sampled
        scene["camera_cull_last_range"] = f"{start}-{end}"
        scene["camera_cull_last_culled"] = len(remaining)
        scene["camera_cull_last_cameras"] = ", ".join(sorted(cameras))
        scene["camera_cull_last_scope"] = settings.scope
        protected = len(objects) - len(remaining)
        self.report({"INFO"}, f"Hidden {len(remaining)} objects; retained {protected}; sampled {sampled} times")
        return {"FINISHED"}


class OBJECT_OT_camera_cull_toggle(bpy.types.Operator):
    bl_idname = "object.camera_cull_toggle"
    bl_label = "Compare Full / Culled"
    bl_description = "Toggle this scene's cull collection in both viewport and render"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        group = _scene_group(context.scene)
        if group is None:
            self.report({"WARNING"}, "This scene has no cull results")
            return {"CANCELLED"}
        error = _group_error(group, context.scene)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        show_full = group.hide_viewport or group.hide_render
        group.hide_viewport = group.hide_render = not show_full
        self.report({"INFO"}, "Full scene shown" if show_full else "Culled scene shown")
        return {"FINISHED"}


class OBJECT_OT_camera_cull_restore(bpy.types.Operator):
    bl_idname = "object.camera_cull_restore"
    bl_label = "Restore Original Collections"
    bl_description = "Restore memberships and visibility for this scene's culled objects"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        group = _scene_group(scene)
        if group is None:
            self.report({"WARNING"}, "This scene has no cull results")
            return {"CANCELLED"}
        error = _group_error(group, scene)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        objects = tuple(group.objects)
        snapshots = {obj: _snapshot(obj) for obj in objects}
        try:
            for obj in objects:
                _restore_object(obj, group, _baseline(obj, group, scene), scene)
        except Exception as exc:
            for obj, state in snapshots.items():
                _restore_snapshot(obj, state)
            self.report({"ERROR"}, f"Restore stopped; previous results retained: {exc}")
            return {"CANCELLED"}
        scene.camera_cull_settings.collection = None
        bpy.data.collections.remove(group)
        for key in ("camera_cull_last_samples", "camera_cull_last_range", "camera_cull_last_culled",
                    "camera_cull_last_cameras", "camera_cull_last_scope"):
            if key in scene:
                del scene[key]
        self.report({"INFO"}, f"Restored {len(objects)} objects")
        return {"FINISHED"}


class VIEW3D_PT_camera_cull_timeline(bpy.types.Panel):
    bl_label = "Camera Timeline Culler"
    bl_idname = "VIEW3D_PT_camera_cull_timeline"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Helix Tools"
    bl_order = 30

    def draw(self, context):
        layout = self.layout
        setup_layout(layout)
        scene, settings = context.scene, context.scene.camera_cull_settings
        start, end = _range(scene)
        camera_cuts = sum(marker.camera is not None for marker in scene.timeline_markers)
        has_camera = scene.camera is not None or bool(camera_cuts)
        group = _scene_group(scene)
        error = _group_error(group, scene)
        scan = section(layout, "Camera Scan", icon="VIEW_CAMERA", section_id="sampling")
        if scan is not None:
            scan.prop(scene, "camera", text="Camera")
            scan.prop(settings, "scope", text="Range")
            scan.label(text=f"Frames {start}–{end}")
            if start <= end:
                scan.label(text=f"Up to {(end - start) * settings.substeps + 1:,} samples")
            else:
                scan.label(text="Start frame is after end frame", icon="ERROR")
            if camera_cuts:
                scan.label(text=f"Camera cuts: {camera_cuts}", icon="VIEW_CAMERA")
            if settings.scope == "PREVIEW":
                scan.label(text="Outside preview range is ignored", icon="ERROR")
            if not has_camera:
                scan.label(text="Choose a camera to begin", icon="INFO")
            elif error:
                scan.label(text="Resolve existing results first", icon="ERROR")
            action = scan.column()
            action.enabled = has_camera and start <= end and error is None
            action.scale_y = 1.25
            action.operator("object.camera_cull_timeline", icon="VIEWZOOM")
        sampling = section(layout, "Sampling Options", icon="SETTINGS", section_id="sampling_options", default_closed=True)
        if sampling is not None:
            sampling.prop(settings, "tolerance", text="Margin", slider=True)
            sampling.prop(settings, "substeps", text="Samples")
        if group is not None:
            results = section(layout, "Results", icon="OUTLINER_COLLECTION", section_id="results")
            if results is not None:
                if error:
                    results.label(text="Shared or unrelated content", icon="ERROR")
                    results.label(text="Separate before changing results")
                else:
                    culled_view = group.hide_viewport or group.hide_render
                    results.label(text=f"{len(group.objects)} culled objects")
                    results.label(text="Showing culled scene" if culled_view else "Showing full scene")
                    results.operator("object.camera_cull_toggle", text="Show Full Scene" if culled_view else "Show Culled Scene",
                                     icon="HIDE_OFF" if culled_view else "HIDE_ON")
                    results.operator("object.camera_cull_restore", text="Restore Collections", icon="LOOP_BACK")
        if "camera_cull_last_samples" in scene:
            layout.label(text=f"Last scan: {scene['camera_cull_last_samples']:,} samples ({scene['camera_cull_last_range']})")
        help_box = section(layout, "About", icon="INFO", section_id="about", default_closed=True)
        if help_box is not None:
            help_box.label(text="Reversible; no visibility keyframes")
            help_box.label(text="Rescan after camera or scene changes")
            help_box.label(text="Shared, instanced and uncertain bounds stay")
            help_box.label(text="Check reflections, shadows and motion blur")


CLASSES = (CameraCullSettings, OBJECT_OT_camera_cull_timeline, OBJECT_OT_camera_cull_toggle,
           OBJECT_OT_camera_cull_restore, VIEW3D_PT_camera_cull_timeline)
_registered_classes = []
_owns_scene_property = False


def _registered_type(cls):
    return cls.__bases__[0].bl_rna_get_subclass_py(cls.__name__, None)


def register():
    global _owns_scene_property
    if _registered_classes:
        return
    if hasattr(bpy.types.Scene, "camera_cull_settings") or any(_registered_type(cls) for cls in CLASSES):
        raise RuntimeError("Camera Timeline Culler is already registered. Disable the older or duplicate copy first.")
    try:
        _UPDATER.register()
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        bpy.types.Scene.camera_cull_settings = PointerProperty(type=CameraCullSettings)
        _owns_scene_property = True
    except Exception:
        unregister()
        raise


def unregister():
    global _owns_scene_property
    _UPDATER.unregister()
    if (_owns_scene_property and hasattr(bpy.types.Scene, "camera_cull_settings")
            and _registered_type(CameraCullSettings) is CameraCullSettings):
        del bpy.types.Scene.camera_cull_settings
    _owns_scene_property = False
    for cls in reversed(_registered_classes):
        if _registered_type(cls) is cls:
            bpy.utils.unregister_class(cls)
    _registered_classes.clear()
