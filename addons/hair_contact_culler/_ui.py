# SPDX-License-Identifier: GPL-3.0-or-later
"""Local sidebar conventions; no dependency on another extension."""
import bpy
import textwrap

def setup_layout(layout):
    layout.use_property_split=True
    layout.use_property_decorate=False
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

class HelixPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Helix Tools'


def panel_header(layout, title):
    layout.label(text=title, icon='CURVES')


def wrapped_label(layout, text, context, icon='NONE', width=None):
    """Keep workflow instructions readable in a narrow Blender sidebar."""
    width = width or max(24, int((getattr(context.region, 'width', 300) - 40) / 7))
    for index, line in enumerate(textwrap.wrap(str(text), width=width) or ['']):
        layout.label(text=line, icon=icon if index == 0 else 'NONE')
