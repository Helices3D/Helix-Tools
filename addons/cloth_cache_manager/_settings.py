# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloth preferences shared by standalone and bundled installations."""

from bpy.props import BoolProperty


PREFERENCE_NAME = "cloth_confirm_resets"
_CONFIGURED_MARKER = "_helix_cloth_preferences_configured"


def preferences_package(package=__package__):
    """Use the suite's preferences when this component is bundled inside it."""
    parent, _, _ = package.rpartition(".")
    return parent if parent.rsplit(".", 1)[-1] == "helix_tools" else package


def get_preferences(context):
    """Direct script registration has no preferences; keep confirmation safe."""
    preferences = getattr(context, "preferences", None)
    if preferences is None:
        return None
    entry = preferences.addons.get(preferences_package())
    return entry.preferences if entry is not None else None


def should_confirm_reset(context):
    preferences = get_preferences(context)
    return getattr(preferences, PREFERENCE_NAME, True)


def _mark_preferences_dirty(preferences, context):
    # Respect Blender's own Auto-Save Preferences setting. Do not write the
    # user's complete preferences file from a cloth cache action.
    settings = getattr(context, "preferences", None)
    if settings is not None:
        settings.is_dirty = True


def set_confirmation_enabled(context, enabled):
    """Change the persisted option after a successful, confirmed reset only."""
    preferences = get_preferences(context)
    if preferences is None or not hasattr(preferences, PREFERENCE_NAME):
        return False
    setattr(preferences, PREFERENCE_NAME, bool(enabled))
    return True


def configure_preferences(controller):
    """Extend the owning updater's preferences before its static registration.

    Bundled children use NoUpdater and register no preferences of their own.
    The suite calls this helper on its root controller instead. Keeping this
    separate from the updater allows identical updater files in every tool.
    """
    if not controller.classes:
        return False
    preferences_class = controller.classes[0]
    if preferences_class.__dict__.get(_CONFIGURED_MARKER, False):
        return True
    annotations = dict(getattr(preferences_class, "__annotations__", {}))
    annotations[PREFERENCE_NAME] = BoolProperty(
        name="Show Reset Warning",
        description=(
            "Ask before freeing checked cloth bakes and invalidating simulation caches; "
            "enable this to restore a warning hidden with Don't Show Again"
        ),
        default=True,
        update=_mark_preferences_dirty,
    )
    preferences_class.__annotations__ = annotations
    original_draw = preferences_class.draw

    def draw(preferences, context):
        original_draw(preferences, context)
        layout = preferences.layout
        layout.separator()
        layout.label(text="Cloth Cache Manager")
        layout.prop(preferences, PREFERENCE_NAME)

    preferences_class.draw = draw
    setattr(preferences_class, _CONFIGURED_MARKER, True)
    return True
