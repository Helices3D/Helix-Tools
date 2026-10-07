# Helix Tools

**Note:** These tools are mostly vibecoded, so you may encounter minor issues.

Six tools by Helices3D for **Blender 5.2.2 LTS**, available together or individually.
All panels share the **Helix Tools** tab in the 3D Viewport sidebar (`N`).

**[Download the complete Helix Tools suite](https://github.com/Helices3D/Helix-Tools/releases/latest/download/helix_tools.zip)**
and install that one ZIP through **Preferences → Get Extensions → Install from
Disk**, or download individual tools using their names below.

Individual add-on source files are in the **[addons/ folder](addons/)**.
Each tool's documentation link explains its workflow.

| Add-on | Purpose | Documentation |
| --- | --- | --- |
| [Jump By Time](https://github.com/Helices3D/Helix-Tools/releases/latest/download/jump_by_time.zip) | Jump to dialogue timestamps or editing timecodes. A starting-frame offset aligns an external editor's timeline segment with your Blender scene. | [Usage](docs/usage.md#jump-by-time) |
| [Smart Empty](https://github.com/Helices3D/Helix-Tools/releases/latest/download/smart_empty.zip) | Create independent helpers at an object's or pose bone's current position and rotation for Damped Track, Copy Location, or Copy Rotation constraints. Track their rig and bone, and hide or restore helpers per rig. | [Usage](docs/usage.md#smart-empty) |
| [Camera Timeline Culler](https://github.com/Helices3D/Helix-Tools/releases/latest/download/camera_timeline_culler.zip) | Find geometry outside the camera throughout a sampled animation range, temporarily hide it, compare the result, and restore it. | [Usage](docs/usage.md#camera-timeline-culler) |
| [Light Size and Shadow Control](https://github.com/Helices3D/Helix-Tools/releases/latest/download/area_light_shadow_control.zip) | Enable Eevee shadows in one click, resize light sources from saved baselines, and try reversible render presets with optional startup backups. | [Usage](docs/usage.md#light-size-and-shadow-control) |
| [Hair Contact Culler](https://github.com/Helices3D/Helix-Tools/releases/latest/download/hair_contact_culler.zip) | Trim native hair from its first garment contact through the tip, preserving the root and preceding bends. Compare the original and choose Full, Low, or Hide viewport detail. | [Workflow guide](addons/hair_contact_culler/WORKFLOW.md) |
| [Cloth Cache Manager](https://github.com/Helices3D/Helix-Tools/releases/latest/download/cloth_cache_manager.zip) | Batch-manage cloth physics cages and other Cloth objects: preserve simulated frames as bakes before timeline navigation, or reset their caches without clicking through each object. | [Usage](docs/usage.md#cloth-cache-manager) |

Disable the individual Helix extensions before enabling the complete suite, or
disable the suite before enabling individual copies. Saved scene settings carry
between the two installation choices. To build ZIPs from source, run
`python scripts/build_releases.py`.

To update, expand an enabled Helix extension in **Preferences → Add-ons** and
click **Check for Updates**. New published versions are downloaded and installed
automatically; restart Blender when prompted. See [update settings](docs/usage.md#updates).

See [usage and migration](docs/usage.md) and [development checks](docs/development.md).
Hair Contact Culler has a [quickstart](addons/hair_contact_culler/README.md#quickstart)
and [full workflow guide](addons/hair_contact_culler/WORKFLOW.md).
Licensed under [GPL-3.0-or-later](LICENSE); [original credits](AUTHORS.md).
