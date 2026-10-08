# SPDX-License-Identifier: GPL-3.0-or-later
"""Find actual scene image references without remapping the Image datablock.

Slots are rediscovered whenever the user switches resolution. A node or object
rename therefore does not invalidate a saved original/alternative image pair,
and a reference the user changed to an unrelated image is left alone.
"""

from dataclasses import dataclass, field

import bpy


def _pointer(value):
    return value.as_pointer()


@dataclass
class ImageSlot:
    """One physical RNA/ID-property reference to an image in the scene graph."""

    owner: object
    property_name: str
    label: str
    is_id_property: bool = False
    shared: bool = False

    @property
    def key(self):
        return (_pointer(self.owner), self.property_name, self.is_id_property)

    @property
    def owner_id(self):
        return self.owner if isinstance(self.owner, bpy.types.ID) else self.owner.id_data

    @property
    def image(self):
        if self.is_id_property:
            return self.owner.get(self.property_name)
        return getattr(self.owner, self.property_name)

    @property
    def editable(self):
        owner_id = self.owner_id
        if owner_id is not None and not owner_id.is_editable:
            return False
        if not self.is_id_property and self.owner.is_property_readonly(self.property_name):
            return False
        return True

    def set(self, image):
        """Replace this reference only; never use Image.user_remap()."""
        if image is not None and not isinstance(image, bpy.types.Image):
            raise TypeError("The replacement must be a Blender Image")
        if not self.editable:
            raise ValueError(f"{self.label}: linked or read-only image reference")
        if self.is_id_property:
            self.owner[self.property_name] = image
        else:
            setattr(self.owner, self.property_name, image)
        owner_id = self.owner_id
        if owner_id is not None:
            owner_id.update_tag()


@dataclass
class ScanResult:
    slots: list[ImageSlot] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


class _SceneScanner:
    def __init__(self):
        self.slots = {}
        self.trees = set()
        self.objects = set()
        self.collections = set()
        self.textures = set()
        self.materials = set()

    def image(self, owner, property_name, label, *, is_id_property=False):
        slot = ImageSlot(owner, property_name, label, is_id_property)
        if isinstance(slot.image, bpy.types.Image):
            self.slots.setdefault(slot.key, slot)

    def pointer_properties(self, owner, label, *, follow_objects=False):
        """Inspect defined RNA pointers, not unrelated arbitrary custom values."""
        for prop in owner.bl_rna.properties:
            if prop.type != "POINTER" or prop.identifier in {"rna_type", "id_data"}:
                continue
            kind = prop.fixed_type.identifier
            if kind not in {"Image", "Texture", "Material", "Object", "Collection"}:
                continue
            if not follow_objects and kind in {"Object", "Collection"}:
                continue
            value = getattr(owner, prop.identifier)
            if isinstance(value, bpy.types.Image):
                self.image(owner, prop.identifier, label)
            elif isinstance(value, bpy.types.Texture):
                self.texture(value)
            elif isinstance(value, bpy.types.Material):
                self.material(value)
            elif follow_objects and isinstance(value, bpy.types.Object):
                self.object(value)
            elif follow_objects and isinstance(value, bpy.types.Collection):
                self.collection(value)

    def tree(self, tree):
        if tree is None or _pointer(tree) in self.trees:
            return
        self.trees.add(_pointer(tree))
        for node in tree.nodes:
            label = f"{tree.name} / {node.name}"
            self.pointer_properties(node, label, follow_objects=True)
            # Image, object, collection and material inputs all have RNA
            # default_value pointers, including nested geometry node groups.
            for socket in node.inputs:
                self.pointer_properties(socket, f"{label} / {socket.name}", follow_objects=True)
            self.tree(getattr(node, "node_tree", None))

    def material(self, material):
        if material is None or _pointer(material) in self.materials:
            return
        self.materials.add(_pointer(material))
        self.tree(material.node_tree)
        if material.grease_pencil is not None:
            self.pointer_properties(material.grease_pencil, material.name)

    def texture(self, texture):
        if texture is None or _pointer(texture) in self.textures:
            return
        self.textures.add(_pointer(texture))
        self.pointer_properties(texture, texture.name)
        self.tree(texture.node_tree)

    def geometry_inputs(self, obj, modifier):
        group = modifier.node_group
        if group is None:
            return
        for item in group.interface.items_tree:
            if item.item_type != "SOCKET" or item.in_out != "INPUT":
                continue
            label = f"{obj.name} / {modifier.name} / {item.name}"
            # Blender 5.2 exposes modifier inputs as generated RNA structs.
            # Older Blender releases stored the same values as ID properties.
            properties = getattr(modifier, "properties", None)
            if properties is not None:
                socket = getattr(properties.inputs, item.identifier, None)
                if socket is not None:
                    self.pointer_properties(socket, label, follow_objects=True)
            else:
                value = modifier.get(item.identifier)
                if isinstance(value, bpy.types.Image):
                    self.image(modifier, item.identifier, label, is_id_property=True)
                elif isinstance(value, bpy.types.Object):
                    self.object(value)
                elif isinstance(value, bpy.types.Collection):
                    self.collection(value)
                elif isinstance(value, bpy.types.Material):
                    self.material(value)

    def collection(self, collection):
        if collection is None or _pointer(collection) in self.collections:
            return
        self.collections.add(_pointer(collection))
        for obj in collection.all_objects:
            self.object(obj)

    def object(self, obj):
        if obj is None or _pointer(obj) in self.objects:
            return
        self.objects.add(_pointer(obj))
        if isinstance(obj.data, bpy.types.Image):
            self.image(obj, "data", obj.name)
        for slot in obj.material_slots:
            self.material(slot.material)
        if obj.type == "LIGHT":
            self.tree(obj.data.node_tree)
        if obj.type == "CAMERA":
            for background in obj.data.background_images:
                self.image(background, "image", f"{obj.name} / Camera Background")
        self.collection(obj.instance_collection)
        for modifier in obj.modifiers:
            self.pointer_properties(modifier, f"{obj.name} / {modifier.name}")
            if modifier.type == "NODES":
                self.tree(modifier.node_group)
                self.geometry_inputs(obj, modifier)
        # Legacy particle texture slots can reference image textures even in
        # scenes that otherwise use only node-based materials.
        for particle_system in obj.particle_systems:
            settings = particle_system.settings
            if settings is None:
                continue
            self.object(settings.instance_object)
            self.collection(settings.instance_collection)
            for slot in settings.texture_slots:
                if slot is not None:
                    self.texture(slot.texture)

    def scene(self, scene):
        for obj in scene.objects:
            self.object(obj)
        self.object(scene.camera)
        if scene.world is not None:
            self.tree(scene.world.node_tree)
        self.tree(getattr(scene, "compositing_node_group", None))
        self.tree(getattr(scene, "node_tree", None))
        return self


def scan_scene(scene, *, check_shared=True):
    """Return image slots, marking references also reachable in another scene.

    Sharing an Image alone is harmless: only an identical reference slot in a
    shared material, node group, object, texture or world is marked shared.
    The caller decides whether the user has opted into changing those slots.
    Includes all scene objects (also hidden ones), collection instances and
    object/collection resources explicitly referenced by geometry nodes.
    """
    scanner = _SceneScanner().scene(scene)
    other_slots = set()
    if check_shared:
        for other_scene in bpy.data.scenes:
            if other_scene != scene:
                other_slots.update(_SceneScanner().scene(other_scene).slots)
    result = ScanResult(list(scanner.slots.values()))
    for slot in result.slots:
        slot.shared = slot.key in other_slots
        if not slot.editable:
            result.issues.append(f"{slot.label}: linked or read-only image reference")
    return result
