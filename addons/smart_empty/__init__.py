# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent, tracked transform anchors for Blender objects and posed bones."""

bl_info = {
    "name": "Smart Empty",
    "author": "Gemini, Helices3D",
    "version": (2, 1, 0),
    "blender": (5, 2, 2),
    "location": "3D View > Sidebar > Helix Tools > Smart Empty",
    "description": "Create independent object or bone anchors and manage them per rig",
    "category": "Object",
}

import uuid

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)
from mathutils import Matrix

from ._ui import section, setup_layout
from ._updates import create_updater


_UPDATER = create_updater(__package__, __file__)


_BONE_ID_KEY = "_helix_smart_empty_bone_id"
_VIEW_LAYER_ID_KEY = "_helix_smart_empty_view_layer_id"
_registered_classes = []
_registered_properties = []


def _rig_poll(settings, obj):
    return obj.type == 'ARMATURE' and obj.name in settings.id_data.objects


class SMART_EMPTY_PG_visibility(bpy.types.PropertyGroup):
    """Visibility before a bulk Hide, scoped to a scene and view layer."""

    scene: PointerProperty(type=bpy.types.Scene)
    view_layer: StringProperty()
    view_layer_id: StringProperty()
    was_hidden: BoolProperty()


class SMART_EMPTY_PG_metadata(bpy.types.PropertyGroup):
    is_tracked: BoolProperty(default=False)
    owner_scene: PointerProperty(type=bpy.types.Scene)
    source_object: PointerProperty(type=bpy.types.Object)
    source_name: StringProperty()
    rig: PointerProperty(type=bpy.types.Object)
    rig_name: StringProperty()
    bone_id: StringProperty()
    bone_name: StringProperty()
    visibility: CollectionProperty(type=SMART_EMPTY_PG_visibility)


class SMART_EMPTY_PG_settings(bpy.types.PropertyGroup):
    empty_size: FloatProperty(
        name="Display Size",
        description="Empty display size; the new object's transform scale stays at one",
        default=0.1,
        min=0.001,
        soft_max=10.0,
        subtype='DISTANCE',
    )
    display_type: EnumProperty(
        name="Display",
        items=[
            ('PLAIN_AXES', "Axes", "Show the empty's local axes"),
            ('ARROWS', "Arrows", "Show all three local axes as arrows"),
            ('SINGLE_ARROW', "Single Arrow", "Show the local Z axis"),
            ('CUBE', "Cube", "Display a wireframe cube"),
            ('SPHERE', "Sphere", "Display a wireframe sphere"),
            ('CIRCLE', "Circle", "Display a circle in the local XY plane"),
        ],
        default='PLAIN_AXES',
    )
    name_suffix: StringProperty(
        name="Name Suffix",
        description="Appended to the source object or bone name; Blender resolves duplicates",
        default=" Empty",
    )
    show_name: BoolProperty(name="Show Name", default=True)
    show_in_front: BoolProperty(name="In Front", default=True)
    use_local_orientation: BoolProperty(
        name="Use Local Transform Orientation",
        description="Switch the current transform orientation to Local for moving the new anchor",
        default=True,
    )
    bone_point: EnumProperty(
        name="Bone Position",
        items=[
            ('HEAD', "Head", "Place the anchor at the evaluated pose bone's head"),
            ('TAIL', "Tail", "Place the anchor at the evaluated pose bone's tail"),
        ],
        default='HEAD',
    )
    rig: PointerProperty(
        name="Rig",
        description="Rig whose tracked empties to list or hide in this view layer",
        type=bpy.types.Object,
        poll=_rig_poll,
    )
    list_scope: EnumProperty(
        name="List",
        items=[
            ('RIG', "Selected Rig", "List tracked empties belonging to the chosen rig"),
            ('ALL', "All in Scene", "Include object anchors and anchors with deleted sources"),
        ],
        default='RIG',
    )


def tracked_empties(scene, rig=None):
    """Return tracked empties linked to this scene, optionally belonging to a rig."""
    return sorted(
        (
            obj for obj in scene.objects
            if obj.type == 'EMPTY'
            and obj.helix_smart_empty.is_tracked
            and (rig is None or obj.helix_smart_empty.rig == rig)
        ),
        key=lambda obj: obj.name.casefold(),
    )


def source_bone(metadata):
    """Resolve a source bone after renaming, without trusting a reused bone name."""
    rig = metadata.rig
    if rig is None or rig.type != 'ARMATURE' or not metadata.bone_name:
        return None
    if metadata.bone_id:
        matches = [bone for bone in rig.data.bones if bone.get(_BONE_ID_KEY) == metadata.bone_id]
        if len(matches) == 1:
            return matches[0]
        # A duplicated UUID cannot establish identity. Keep the anchor safe
        # rather than silently associate it with a different, renamed bone.
        return None
    return rig.data.bones.get(metadata.bone_name)


def _bone_identifier(bone, armature):
    identifier = bone.get(_BONE_ID_KEY, "")
    if identifier and sum(
        candidate.get(_BONE_ID_KEY) == identifier for candidate in armature.bones
    ) != 1:
        # Duplication copies custom properties. Do not rewrite a shared UUID,
        # which could redirect previously created anchors to the other bone.
        return ""
    if not armature.is_editable:
        # Blender can accept linked-bone ID-property writes in memory while
        # discarding them on save. Reuse only an identity stored in the library.
        return identifier
    if not identifier:
        identifier = uuid.uuid4().hex
        try:
            bone[_BONE_ID_KEY] = identifier
        except (RuntimeError, TypeError):
            # Linked read-only bones can still be anchored; name tracking is the
            # best available fallback when the library cannot store metadata.
            return ""
    return identifier


def _visibility_index(metadata, scene, view_layer):
    identifier = view_layer.get(_VIEW_LAYER_ID_KEY, "")
    return next(
        (
            index for index, entry in enumerate(metadata.visibility)
            if entry.scene == scene and (
                (entry.view_layer_id and entry.view_layer_id == identifier)
                or (not entry.view_layer_id and entry.view_layer == view_layer.name)
            )
        ),
        None,
    )


def _view_layer_identifier(scene, view_layer):
    """Keep copied view-layer custom properties from aliasing visibility records."""
    identifier = view_layer.get(_VIEW_LAYER_ID_KEY, "")
    if not identifier:
        identifier = uuid.uuid4().hex
        view_layer[_VIEW_LAYER_ID_KEY] = identifier
        return identifier
    matching_layers = [
        layer for layer in scene.view_layers if layer.get(_VIEW_LAYER_ID_KEY) == identifier
    ]
    if len(matching_layers) > 1:
        records = [
            entry for obj in tracked_empties(scene) for entry in obj.helix_smart_empty.visibility
            if entry.scene == scene and entry.view_layer_id == identifier
        ]
        named_layers = [
            layer for layer in matching_layers
            if any(entry.view_layer == layer.name for entry in records)
        ]
        # Blender appends copied layers. Existing records help identify the
        # original; scene order remains a fallback if it has since been renamed.
        original = named_layers[0] if len(named_layers) == 1 else matching_layers[0]
        for layer in matching_layers:
            if layer == original:
                continue
            replacement = uuid.uuid4().hex
            layer[_VIEW_LAYER_ID_KEY] = replacement
            for entry in records:
                if entry.view_layer == layer.name:
                    entry.view_layer_id = replacement
    return view_layer[_VIEW_LAYER_ID_KEY]


def set_rig_visibility(context, rig, hidden):
    """Hide per view layer, or restore the visibility captured by that Hide."""
    identifier = _view_layer_identifier(context.scene, context.view_layer)
    changed = 0
    skipped = 0
    for obj in tracked_empties(context.scene, rig):
        # Linked helpers cannot persist their visibility metadata in this
        # file. Local helpers attached to a linked rig remain fully supported.
        if obj.name not in context.view_layer.objects or not obj.is_editable:
            skipped += 1
            continue
        metadata = obj.helix_smart_empty
        index = _visibility_index(metadata, context.scene, context.view_layer)
        if hidden:
            if index is None:
                entry = metadata.visibility.add()
                entry.scene = context.scene
                entry.view_layer = context.view_layer.name
                entry.view_layer_id = identifier
                entry.was_hidden = obj.hide_get(view_layer=context.view_layer)
            obj.hide_set(True, view_layer=context.view_layer)
            changed += 1
        elif index is not None:
            entry = metadata.visibility[index]
            obj.hide_set(entry.was_hidden, view_layer=context.view_layer)
            metadata.visibility.remove(index)
            changed += 1
    return changed, skipped


def _source_description(metadata):
    if metadata.bone_name:
        if metadata.rig is None:
            return f"Missing rig: {metadata.rig_name} / {metadata.bone_name}"
        bone = source_bone(metadata)
        if bone is None:
            if metadata.bone_id and sum(
                bone.get(_BONE_ID_KEY) == metadata.bone_id for bone in metadata.rig.data.bones
            ) > 1:
                return f"{metadata.rig.name} / Copied bone identity: {metadata.bone_name}"
            return f"{metadata.rig.name} / Missing bone: {metadata.bone_name}"
        return f"{metadata.rig.name} / {bone.name}"
    if metadata.source_object is None:
        return f"Missing object: {metadata.source_name}"
    return metadata.source_object.name


def _link_anchor(context, empty):
    layer = context.view_layer.active_layer_collection
    collection = layer.collection if layer.is_visible and layer.collection.is_editable else context.scene.collection
    if not collection.is_editable:
        raise RuntimeError("This scene has no editable collection for the new empty")
    collection.objects.link(empty)


class SMART_EMPTY_OT_add_baked(bpy.types.Operator):
    """Create an independent anchor at the active object's or posed bone's world transform"""

    bl_idname = "object.add_smart_empty_baked"
    bl_label = "Add Smart Empty"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode == 'POSE':
            return context.active_object is not None and context.active_pose_bone is not None
        return context.mode == 'OBJECT' and context.active_object is not None

    def execute(self, context):
        settings = context.scene.helix_smart_empty_settings
        source = context.active_object
        pose_bone = context.active_pose_bone if context.mode == 'POSE' else None
        bone_name = pose_bone.name if pose_bone else ""
        target_name = bone_name or source.name
        rig = source if source.type == 'ARMATURE' else None

        # Evaluate before leaving Pose Mode, and copy matrices before releasing
        # evaluated data. Armature world space includes all object parents.
        context.view_layer.update()
        evaluated = source.evaluated_get(context.evaluated_depsgraph_get())
        if pose_bone:
            evaluated_bone = evaluated.pose.bones.get(bone_name)
            if evaluated_bone is None:
                self.report({'ERROR'}, "The active pose bone is unavailable in evaluated data")
                return {'CANCELLED'}
            matrix = evaluated.matrix_world @ evaluated_bone.matrix
            location = evaluated.matrix_world @ (
                evaluated_bone.tail if settings.bone_point == 'TAIL' else evaluated_bone.head
            )
        else:
            matrix = evaluated.matrix_world.copy()
            location = matrix.translation.copy()
        _location, rotation, _scale = matrix.decompose()
        transform = Matrix.LocRotScale(location, rotation, None)
        bone_id = _bone_identifier(pose_bone.bone, rig.data) if pose_bone else ""

        previous_mode = context.mode
        previous_selection = list(context.selected_objects)
        previous_orientation = context.scene.transform_orientation_slots[0].type
        empty = None
        try:
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            empty = bpy.data.objects.new(f"{target_name}{settings.name_suffix}", None)
            _link_anchor(context, empty)
            empty.empty_display_type = settings.display_type
            empty.empty_display_size = settings.empty_size
            empty.show_in_front = settings.show_in_front
            empty.show_name = settings.show_name
            empty.matrix_world = transform
            metadata = empty.helix_smart_empty
            metadata.is_tracked = True
            metadata.owner_scene = context.scene
            metadata.source_object = source
            metadata.source_name = source.name
            metadata.rig = rig
            metadata.rig_name = rig.name if rig else ""
            metadata.bone_id = bone_id
            metadata.bone_name = bone_name

            context.view_layer.update()
            if context.space_data and context.space_data.type == 'VIEW_3D' and context.space_data.local_view:
                empty.local_view_set(context.space_data, True)
            if empty.name not in context.view_layer.objects or not empty.visible_get(view_layer=context.view_layer):
                raise RuntimeError("The new empty is unavailable in the active view layer")
            for selected in context.selected_objects:
                selected.select_set(False)
            empty.select_set(True)
            context.view_layer.objects.active = empty
            if settings.use_local_orientation:
                context.scene.transform_orientation_slots[0].type = 'LOCAL'
        except (RuntimeError, TypeError, ReferenceError) as error:
            if empty is not None:
                bpy.data.objects.remove(empty, do_unlink=True)
            context.view_layer.update()
            for selected in context.selected_objects:
                selected.select_set(False)
            for selected in previous_selection:
                selected.select_set(True)
            context.view_layer.objects.active = source
            context.scene.transform_orientation_slots[0].type = previous_orientation
            if previous_mode == 'POSE' and context.mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE')
            self.report({'WARNING'}, f"Could not create anchor: {error}")
            return {'CANCELLED'}

        if rig:
            settings.rig = rig
            settings.list_scope = 'RIG'
        else:
            settings.list_scope = 'ALL'

        self.report({'INFO'}, f"Created independent anchor: {empty.name}")
        return {'FINISHED'}


class SMART_EMPTY_OT_use_active_rig(bpy.types.Operator):
    """Choose the active armature, or the rig associated with the active tracked empty"""

    bl_idname = "object.smart_empty_use_active_rig"
    bl_label = "Use Active Rig"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and (
            obj.type == 'ARMATURE' or (
                obj.helix_smart_empty.is_tracked and obj.helix_smart_empty.rig is not None
            )
        )

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.helix_smart_empty_settings
        settings.rig = obj if obj.type == 'ARMATURE' else obj.helix_smart_empty.rig
        settings.list_scope = 'RIG'
        return {'FINISHED'}


class SMART_EMPTY_OT_rig_visibility(bpy.types.Operator):
    """Hide the chosen rig's anchors, or restore their visibility before Hide; renders are unchanged"""

    bl_idname = "object.smart_empty_rig_visibility"
    bl_label = "Set Rig Empty Visibility"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        items=[('HIDE', "Hide", "Hide tracked empties in this view layer"),
               ('RESTORE', "Restore", "Restore visibility recorded before Hide")],
    )

    @classmethod
    def poll(cls, context):
        return context.scene.helix_smart_empty_settings.rig is not None

    def execute(self, context):
        rig = context.scene.helix_smart_empty_settings.rig
        changed, skipped = set_rig_visibility(context, rig, self.action == 'HIDE')
        action = "Hidden" if self.action == 'HIDE' else "Restored"
        message = f"{action} {changed} tracked empties for {rig.name}"
        if skipped:
            message += f"; skipped {skipped} excluded or read-only empties"
        self.report({'INFO'}, message)
        return {'FINISHED'}


class SMART_EMPTY_OT_select(bpy.types.Operator):
    """Select this anchor in Object Mode; reveal it if locally hidden in this view layer"""

    bl_idname = "object.smart_empty_select"
    bl_label = "Select Empty"
    bl_options = {'REGISTER', 'UNDO'}

    object_name: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        obj = context.scene.objects.get(self.object_name)
        if obj is None or obj.type != 'EMPTY' or not obj.helix_smart_empty.is_tracked:
            self.report({'WARNING'}, "The tracked empty no longer exists in this scene")
            return {'CANCELLED'}
        if obj.name not in context.view_layer.objects or obj.hide_viewport or obj.hide_select:
            self.report({'WARNING'}, "Enable this empty's collection and selection in the Outliner")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.hide_set(False, view_layer=context.view_layer)
        if not obj.visible_get(view_layer=context.view_layer):
            self.report({'WARNING'}, "Enable this empty's collection in the Outliner")
            return {'CANCELLED'}
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class SMART_EMPTY_PT_panel(bpy.types.Panel):
    bl_label = "Smart Empty"
    bl_idname = "VIEW3D_PT_smart_empty"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Helix Tools'
    bl_order = 20

    def draw(self, context):
        layout = self.layout
        setup_layout(layout)
        settings = context.scene.helix_smart_empty_settings
        create = section(layout, "Create Anchor", 'EMPTY_AXIS', section_id="create_anchor")
        if create is not None:
            if context.mode == 'POSE' and context.active_pose_bone:
                create.label(text=f"Bone: {context.active_pose_bone.name}", icon='BONE_DATA')
                create.prop(settings, "bone_point")
            elif context.mode == 'OBJECT' and context.active_object:
                create.label(text=f"Object: {context.active_object.name}", icon='OBJECT_DATA')
            else:
                create.label(text="Select an object or a pose bone", icon='INFO')
            create.prop(settings, "display_type")
            create.prop(settings, "empty_size")
            create.prop(settings, "name_suffix")
            row = create.row(align=True)
            row.prop(settings, "show_name")
            row.prop(settings, "show_in_front")
            create.prop(settings, "use_local_orientation")
            create.operator("object.add_smart_empty_baked", text="Add Smart Empty", icon='ADD')

        manage = section(layout, "Tracked Anchors", 'OUTLINER_OB_EMPTY', section_id="tracked_anchors")
        if manage is not None:
            manage.prop(settings, "rig")
            manage.operator("object.smart_empty_use_active_rig", icon='ARMATURE_DATA')
            row = manage.row(align=True)
            row.enabled = settings.rig is not None
            row.operator("object.smart_empty_rig_visibility", text="Hide Empties", icon='HIDE_ON').action = 'HIDE'
            row.operator("object.smart_empty_rig_visibility", text="Restore Visibility", icon='HIDE_OFF').action = 'RESTORE'
            manage.label(text="Viewport only; restore keeps prior hidden states", icon='INFO')
            manage.prop(settings, "list_scope")
            objects = tracked_empties(context.scene)
            if settings.list_scope == 'RIG':
                objects = [obj for obj in objects if settings.rig and obj.helix_smart_empty.rig == settings.rig]
            manage.label(text=f"{len(objects)} tracked empties")
            for obj in objects:
                item = manage.column(align=True)
                row = item.row(align=True)
                row.label(text=obj.name, icon='EMPTY_AXIS')
                select = row.row(align=True)
                select.enabled = obj.name in context.view_layer.objects and not obj.hide_viewport and not obj.hide_select
                select.operator("object.smart_empty_select", text="", icon='RESTRICT_SELECT_OFF').object_name = obj.name
                item.label(text=_source_description(obj.helix_smart_empty))


classes = (
    SMART_EMPTY_PG_visibility,
    SMART_EMPTY_PG_metadata,
    SMART_EMPTY_PG_settings,
    SMART_EMPTY_OT_add_baked,
    SMART_EMPTY_OT_use_active_rig,
    SMART_EMPTY_OT_rig_visibility,
    SMART_EMPTY_OT_select,
    SMART_EMPTY_PT_panel,
)


def register():
    if _registered_classes:
        return
    for cls in classes:
        if issubclass(cls, bpy.types.Operator):
            group, name = cls.bl_idname.split(".", 1)
            existing = bpy.types.Operator.bl_rna_get_subclass_py(f"{group.upper()}_OT_{name}")
        elif issubclass(cls, bpy.types.Panel):
            existing = bpy.types.Panel.bl_rna_get_subclass_py(cls.bl_idname)
        else:
            existing = bpy.types.PropertyGroup.bl_rna_get_subclass_py(cls.__name__)
        if existing is not None:
            raise RuntimeError("Disable the older Smart Empty add-on before enabling Helix Smart Empty")
    for owner, name in (
        (bpy.types.Object, "helix_smart_empty"),
        (bpy.types.Scene, "helix_smart_empty_settings"),
    ):
        if hasattr(owner, name):
            raise RuntimeError("Disable the other Smart Empty copy before enabling Helix Smart Empty")
    try:
        _UPDATER.register()
        for cls in classes:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        bpy.types.Object.helix_smart_empty = PointerProperty(type=SMART_EMPTY_PG_metadata)
        _registered_properties.append((bpy.types.Object, "helix_smart_empty"))
        bpy.types.Scene.helix_smart_empty_settings = PointerProperty(type=SMART_EMPTY_PG_settings)
        _registered_properties.append((bpy.types.Scene, "helix_smart_empty_settings"))
    except Exception:
        unregister()
        raise


def unregister():
    _UPDATER.unregister()
    for owner, name in reversed(_registered_properties):
        if hasattr(owner, name):
            delattr(owner, name)
    _registered_properties.clear()
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes.clear()


if __name__ == "__main__":
    register()
