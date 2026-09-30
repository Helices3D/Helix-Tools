"""Consistent sidebar layout, kept local so each add-on installs independently."""


def setup_layout(layout):
    layout.use_property_split = True
    layout.use_property_decorate = False
    return layout


def section(layout, title, icon="NONE"):
    column = layout.box().column()
    column.label(text=title, icon=icon)
    return column
