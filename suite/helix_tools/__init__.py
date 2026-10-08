# SPDX-License-Identifier: GPL-3.0-or-later
"""Enable the current seven Helix Tools components as one Blender extension."""

bl_info = {
    "name": "Helix Tools",
    "author": "Helices3D",
    "version": (1, 4, 0),
    "blender": (5, 2, 2),
    "location": "3D View > Sidebar > Helix Tools",
    "description": "Install all seven Helix Tools add-ons together",
    "category": "Object",
}

import bpy
from ._updates import create_updater

_UPDATER = create_updater(__package__, __file__)

from . import (
    jump_by_time,
    smart_empty,
    camera_timeline_culler,
    area_light_shadow_control,
    hair_contact_culler,
    cloth_cache_manager,
    texture_resolution,
)


# Keep the release inventory in the standalone builder's canonical order.
MODULE_NAMES = (
    "jump_by_time",
    "smart_empty",
    "camera_timeline_culler",
    "area_light_shadow_control",
    "hair_contact_culler",
    "cloth_cache_manager",
    "texture_resolution",
)
modules = (
    jump_by_time,
    smart_empty,
    camera_timeline_culler,
    area_light_shadow_control,
    hair_contact_culler,
    cloth_cache_manager,
    texture_resolution,
)

# Hair restores scene nodes when disabled, so it must be registered last.
# Earlier components can be rolled back without scene access if restricted
# extension registration fails; Hair handles its own partial register failures.
REGISTER_ORDER = (
    jump_by_time,
    smart_empty,
    camera_timeline_culler,
    area_light_shadow_control,
    cloth_cache_manager,
    texture_resolution,
    hair_contact_culler,
)

CLASSES = ()  # Update preferences/operators are tracked separately by _UPDATER.
_registered_modules = []
_registered = False


def _classes(module):
    return getattr(module, "CLASSES", getattr(module, "classes", ()))


def _rna_identifier(cls):
    if issubclass(cls, bpy.types.Operator):
        namespace, name = cls.bl_idname.split(".", 1)
        return bpy.types.Operator, f"{namespace.upper()}_OT_{name}"
    for base in (bpy.types.Panel, bpy.types.PropertyGroup, bpy.types.UIList):
        if issubclass(cls, base):
            return base, cls.__dict__.get("bl_idname") or cls.__name__
    raise RuntimeError(f"Unsupported Helix Tools registration class: {cls.__name__}")


def _properties(module):
    """List static RNA schemas without reading any scene or object data."""
    if module is jump_by_time:
        return tuple((bpy.types.Scene, name) for name in jump_by_time._PROPERTIES)
    if module is smart_empty:
        return (
            (bpy.types.Object, "helix_smart_empty"),
            (bpy.types.Scene, "helix_smart_empty_settings"),
        )
    if module is camera_timeline_culler:
        return ((bpy.types.Scene, "camera_cull_settings"),)
    if module is area_light_shadow_control:
        return ((bpy.types.Scene, area_light_shadow_control.SCENE_PROPERTY),)
    if module is hair_contact_culler:
        return ((bpy.types.Object, "helix_hair_cull"),)
    if module is texture_resolution:
        return ((bpy.types.Scene, texture_resolution.SCENE_PROPERTY),)
    if module is cloth_cache_manager:
        return tuple((bpy.types.Object, name) for name in (
            cloth_cache_manager.PROPERTY_NAME, cloth_cache_manager.GROUP_PROPERTY_NAME,
        ))
    raise RuntimeError(f"Unknown Helix Tools component: {module.__name__}")


def _preflight_registration():
    """Reject every overlapping standalone before registering any component."""
    conflicts = []
    for module in modules:
        overlapping = any(hasattr(owner, name) for owner, name in _properties(module))
        for cls in _classes(module):
            base, identifier = _rna_identifier(cls)
            if base.bl_rna_get_subclass_py(identifier, None) is not None:
                overlapping = True
        if overlapping:
            conflicts.append(module.bl_info["name"])
    if conflicts:
        raise RuntimeError(
            "Helix Tools conflicts with already enabled tools: " + ", ".join(conflicts) + ". "
            "Disable their standalone add-ons or an older Helix Tools copy before enabling this extension."
        )


def register():
    global _registered
    if _registered:
        return
    if _registered_modules:
        raise RuntimeError(
            "Helix Tools has unfinished cleanup. Disable it again before enabling this extension."
        )
    _preflight_registration()
    failed_name = "Update Controls"
    try:
        cloth_cache_manager.configure_preferences(_UPDATER)
        _UPDATER.register()
        for module in REGISTER_ORDER:
            failed_name = module.bl_info["name"]
            module.register()
            _registered_modules.append(module)
    except Exception as error:
        try:
            unregister()
        except Exception as cleanup_error:
            raise RuntimeError(
                f"Helix Tools could not enable {failed_name}: {error}. "
                f"Cleanup also failed: {cleanup_error}. Disable Helix Tools again before retrying."
            ) from error
        raise RuntimeError(
            f"Helix Tools could not enable {failed_name}; completed registrations were rolled back: {error}"
        ) from error
    _registered = True


def unregister():
    """Disable only owned components, continuing after errors and allowing retry."""
    global _registered
    _registered = False
    failures = []
    try:
        _UPDATER.unregister()
    except Exception as error:
        failures.append(f"Update Controls: {error}")
    for module in reversed(tuple(_registered_modules)):
        try:
            module.unregister()
        except Exception as error:
            failures.append(f"{module.bl_info['name']}: {error}")
        else:
            _registered_modules.remove(module)
    if failures:
        raise RuntimeError("Helix Tools cleanup is incomplete: " + "; ".join(failures))


if __name__ == "__main__":
    register()
