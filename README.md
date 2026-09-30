# Helix Tools

Five independently installable tools by Helices3D for **Blender 5.2.2 LTS**.
All panels share the **Helix Tools** tab in the 3D Viewport sidebar (`N`).

| Add-on | Purpose |
| --- | --- |
| Jump By Time | Jump to dialogue timestamps or editing timecodes. A starting-frame offset aligns an external editor's timeline segment with your Blender scene. |
| Smart Empty | Create independent helpers at an object's or pose bone's current position and rotation for Damped Track, Copy Location, or Copy Rotation constraints. Track their rig and bone, and hide or restore helpers per rig. |
| Camera Timeline Culler | Find geometry outside the camera throughout a sampled animation range, temporarily hide it, compare the result, and restore it. |
| Light Size and Shadow Control | Adjust light size from saved baselines, tune shadows, and try reversible render presets with optional startup backups. |
| [Hair Contact Culler](addons/hair_contact_culler/README.md) | Trim native hair from its first garment contact through the tip, preserving the root and preceding bends. Compare the original and choose Full, Low, or Hide viewport detail. |

Build the installable ZIPs with `python scripts/build_releases.py`, then
install only the tools you want through **Preferences → Get Extensions → Install
from Disk**. Disable old copies before enabling replacements.

See [usage and migration](docs/usage.md) and [development checks](docs/development.md).
Hair Contact Culler has a [quickstart](addons/hair_contact_culler/README.md#quickstart)
and [full workflow guide](addons/hair_contact_culler/WORKFLOW.md).
Licensed under [GPL-3.0-or-later](LICENSE); [original credits](AUTHORS.md).
