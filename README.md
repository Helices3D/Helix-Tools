# Helix Tools

**Note:** These tools are mostly vibecoded, so you may encounter minor issues.

Five independently installable tools by Helices3D for **Blender 5.2.2 LTS**.
All panels share the **Helix Tools** tab in the 3D Viewport sidebar (`N`).

All add-on source files are in the **[addons/ folder](addons/)**. Click an add-on's
name below to open its folder, or use its documentation link for instructions.

| Add-on | Purpose | Documentation |
| --- | --- | --- |
| [Jump By Time](addons/jump_by_time/) | Jump to dialogue timestamps or editing timecodes. A starting-frame offset aligns an external editor's timeline segment with your Blender scene. | [Usage](docs/usage.md#jump-by-time) |
| [Smart Empty](addons/smart_empty/) | Create independent helpers at an object's or pose bone's current position and rotation for Damped Track, Copy Location, or Copy Rotation constraints. Track their rig and bone, and hide or restore helpers per rig. | [Usage](docs/usage.md#smart-empty) |
| [Camera Timeline Culler](addons/camera_timeline_culler/) | Find geometry outside the camera throughout a sampled animation range, temporarily hide it, compare the result, and restore it. | [Usage](docs/usage.md#camera-timeline-culler) |
| [Light Size and Shadow Control](addons/area_light_shadow_control/) | Adjust light size from saved baselines, tune shadows, and try reversible render presets with optional startup backups. | [Usage](docs/usage.md#light-size-and-shadow-control) |
| [Hair Contact Culler](addons/hair_contact_culler/) | Trim native hair from its first garment contact through the tip, preserving the root and preceding bends. Compare the original and choose Full, Low, or Hide viewport detail. | [Workflow guide](addons/hair_contact_culler/WORKFLOW.md) |

Build the installable ZIPs with `python scripts/build_releases.py`, then
install only the tools you want through **Preferences → Get Extensions → Install
from Disk**. Disable old copies before enabling replacements.

See [usage and migration](docs/usage.md) and [development checks](docs/development.md).
Hair Contact Culler has a [quickstart](addons/hair_contact_culler/README.md#quickstart)
and [full workflow guide](addons/hair_contact_culler/WORKFLOW.md).
Licensed under [GPL-3.0-or-later](LICENSE); [original credits](AUTHORS.md).
