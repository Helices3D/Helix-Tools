"""Consistent sidebar layout, kept local so each add-on installs independently."""


def setup_layout(layout):
    layout.use_property_split = True
    layout.use_property_decorate = False
    return layout


def section(layout, title, icon="NONE", *, section_id, default_closed=False):
    """Draw a native collapsible header and return its boxed body when open.

    Native panels must be created on the full-width root layout. Keep the box
    inside the returned body, and let callers skip its controls when collapsed.
    IDs use the standalone package name even in Blender extension namespaces.
    """
    package = __package__.rsplit(".", 1)[-1]
    header, body = layout.panel(
        f"helix_tools.{package}.{section_id}", default_closed=default_closed,
    )
    header.label(text=title, icon=icon)
    return body.box().column() if body is not None else None
