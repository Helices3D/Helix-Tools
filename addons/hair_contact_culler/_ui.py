# SPDX-License-Identifier: GPL-3.0-or-later
"""Local sidebar conventions; no dependency on another extension."""
import bpy
import textwrap

def setup_layout(layout):
    layout.use_property_split=True
    layout.use_property_decorate=False
    return layout

def section(layout,title,icon='NONE'):
    column=layout.box().column()
    column.label(text=title,icon=icon)
    return column

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
