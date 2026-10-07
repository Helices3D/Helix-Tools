# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover model groups for cloth objects without modifying the scene.

Deformation links take precedence over cage ancestry: a mesh deformed by a
cage identifies the model that cage affects. Armature membership then combines
that model's meshes. Names and organizational Empty parents are never used to
guess a relationship. An explicit model override can resolve unusual setups.
"""

from dataclasses import dataclass
import hashlib


GROUP_PROPERTY_NAME = "cloth_tool_model"

# Only links that actually deform geometry belong here. A Mirror reference or
# texture-coordinate object, for example, does not identify an affected model.
# Disabled modifiers still describe the same relationship, so toggling physics
# or viewport visibility does not make the list jump between groups.
_DEFORM_TARGET_PROPERTIES = {
    "MESH_DEFORM": ("object",),
    "SURFACE_DEFORM": ("target",),
    "SHRINKWRAP": ("target", "auxiliary_target"),
    "HOOK": ("object",),
    "WARP": ("object_from", "object_to"),
    "LATTICE": ("object",),
}


@dataclass(frozen=True)
class ClothGroup:
    """One display group; every cloth object appears in exactly one group.

    ``model`` is populated for a single owner. ``models`` holds all owners for
    shared cages, or is empty for Ungrouped objects. Keys stay independent of
    the checked state, modifier visibility, and cache status.
    """

    key: str
    label: str
    objects: tuple
    model: object | None
    models: tuple


def _object_sort_key(obj):
    library = getattr(obj, "library", None)
    return obj.name.casefold(), obj.name, library.filepath if library else ""


def _group_key(models):
    if not models:
        return "ungrouped"
    identities = [
        (obj.name_full, obj.library.filepath if obj.library else "")
        for obj in models
    ]
    # A fixed-size panel identifier avoids arbitrary object-name length or
    # punctuation affecting native collapsible sections.
    digest = hashlib.sha256(repr(identities).encode("utf-8")).hexdigest()[:20]
    return f"model_{digest}"


def model_override_poll(owner, candidate):
    """Allow a deliberate scene mesh, rig, or model-root Empty override."""
    import bpy

    if (candidate is None or candidate == owner
            or candidate.type not in {"MESH", "ARMATURE", "EMPTY"}):
        return False
    scene = getattr(bpy.context, "scene", None)
    return scene is not None and candidate in set(scene.objects)


def _ancestors(obj, scene_objects):
    """Walk parents defensively; an external parent is outside this scene."""
    visited = {obj}
    parent = obj.parent
    while parent is not None and parent in scene_objects and parent not in visited:
        visited.add(parent)
        yield parent
        parent = parent.parent


def _model_roots(obj, scene_objects, *, allow_self):
    """Find model/rig membership without treating a generic Empty as a model."""
    ancestors = tuple(_ancestors(obj, scene_objects))
    # A mesh parent can carry the armature modifier even when neither cages nor
    # their affected child meshes are parented directly to that armature.
    meshes = (obj,) + tuple(parent for parent in ancestors if parent.type == "MESH")
    rigs = {
        modifier.object
        for mesh in meshes
        for modifier in mesh.modifiers
        if modifier.type == "ARMATURE"
        and modifier.object in scene_objects
        and modifier.object.type == "ARMATURE"
    }
    parent_rig = next((parent for parent in ancestors if parent.type == "ARMATURE"), None)
    if parent_rig is not None:
        rigs.add(parent_rig)
    if rigs:
        return rigs
    parent_mesh = next((parent for parent in ancestors if parent.type == "MESH"), None)
    if parent_mesh is not None:
        return {parent_mesh}
    return {obj} if allow_self and obj.type == "MESH" else set()


def _deformation_consumers(scene_objects):
    """Map each deformation source to scene meshes that use its geometry."""
    consumers = {}
    for model in scene_objects:
        if model.type != "MESH":
            continue
        for modifier in model.modifiers:
            for property_name in _DEFORM_TARGET_PROPERTIES.get(modifier.type, ()):
                source = getattr(modifier, property_name, None)
                if source in scene_objects and source != model:
                    consumers.setdefault(source, set()).add(model)
    return consumers


def _terminal_consumers(obj, consumers):
    """Follow cage-to-cage chains to their final meshes, tolerating cycles."""
    terminals = set()
    pending = list(consumers.get(obj, ()))
    visited = {obj}
    while pending:
        consumer = pending.pop()
        if consumer in visited:
            continue
        visited.add(consumer)
        downstream = consumers.get(consumer)
        if downstream:
            pending.extend(downstream)
        else:
            terminals.add(consumer)
    return terminals


def cloth_groups(scene, cloth_objects=None):
    """Return sorted, scene-scoped groups from actual deformation relationships.

    ``cloth_objects`` may be supplied by the caller's existing checklist scan.
    Inclusion, object selection, cache state, and visibility are left untouched.
    A cage affecting several distinct models is listed once in a Shared Models
    group identified by that exact owner set. Unattached cages stay Ungrouped.
    """
    scene_objects = set(scene.objects)
    if cloth_objects is None:
        cloth_objects = [
            obj for obj in scene_objects
            if any(modifier.type == "CLOTH" for modifier in obj.modifiers)
        ]
    else:
        cloth_objects = [obj for obj in cloth_objects if obj in scene_objects]
    consumers = _deformation_consumers(scene_objects)
    affected_objects = {obj for objects in consumers.values() for obj in objects}
    grouped = {}
    for obj in sorted(set(cloth_objects), key=_object_sort_key):
        override = getattr(obj, GROUP_PROPERTY_NAME, None)
        if (override in scene_objects and override != obj
                and override.type in {"MESH", "ARMATURE", "EMPTY"}):
            roots = {override}
        else:
            terminals = _terminal_consumers(obj, consumers)
            roots = set()
            for terminal in terminals:
                roots.update(_model_roots(terminal, scene_objects, allow_self=True))
            if not roots:
                roots = _model_roots(obj, scene_objects, allow_self=obj in affected_objects)
        models = tuple(sorted(roots, key=_object_sort_key))
        grouped.setdefault(models, []).append(obj)

    groups = []
    for models, objects in grouped.items():
        if len(models) == 1:
            model = models[0]
            label = model.name
        else:
            model = None
            label = "Shared Models: " + ", ".join(obj.name for obj in models) if models else "Ungrouped"
        groups.append(ClothGroup(_group_key(models), label, tuple(objects), model, models))
    return sorted(groups, key=lambda group: (
        2 if not group.models else 1 if len(group.models) > 1 else 0,
        group.label.casefold(), group.label,
    ))
