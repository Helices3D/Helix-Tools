# Cloth Cache Manager

Manage cloth physics cages and other objects using Cloth modifiers from one
panel. Play the simulation from its starting frame, preserve the simulated
frames as bakes before timeline navigation, or reset checked objects' caches.
Cages are grouped by the models they affect, with optional model overrides.
Keep the checklist, cache status, and viewport/render visibility controls in the
shared **Helix Tools** sidebar. The reset warning can be hidden and restored
from the add-on preferences.

Targets **Blender 5.2.2 LTS**. Build the installable ZIP with
`python scripts/build_releases.py` and install it through **Preferences → Get
Extensions → Install from Disk**. Disable older copies first.

See [usage and migration](../../docs/usage.md#cloth-cache-manager). Originally
supplied as `cloth_cage_manager.py`, credited to Gemini and Helices3D; maintained
as a standalone extension under GPL-3.0-or-later.

For automatic installation of newer published versions, use **Preferences →
Add-ons → Check for Updates** in an enabled Helix extension, then restart Blender
when prompted. See the [update guide](https://github.com/Helices3D/Helix-Tools/blob/main/docs/usage.md#updates).
