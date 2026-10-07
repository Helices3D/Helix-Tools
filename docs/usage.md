# Usage and migration

Download the [complete Helix Tools suite](https://github.com/Helices3D/Helix-Tools/releases/latest/download/helix_tools.zip)
to install all six tools once, or choose individual ZIPs from the
[README's tool list](../README.md). Install your chosen ZIPs through
**Preferences → Get Extensions → Install from Disk** in **Blender 5.2.2 LTS**,
then enable the installed extensions. The complete suite is one extension;
individual tools are separate extensions that each work independently.
Open the 3D Viewport sidebar (`N`) and choose **Helix Tools**.

Before enabling the suite, disable all individual Helix tool extensions. When
switching back to individual installs, disable the suite first. Both choices use
the same saved scene settings, object tracking, and operator names, so existing
projects and shortcuts continue to work. Keep only one enabled copy of each tool.
Versioned ZIPs are also available on the
[releases page](https://github.com/Helices3D/Helix-Tools/releases).

Click a section's disclosure header to collapse or expand its boxed controls.
Each section folds independently; collapsing it only hides its controls and
does not change the tool's settings or results.
Primary actions stay near their inputs. Optional mapping, display, sampling,
and maintenance controls start collapsed; expand them when needed. Hover over
a control for its explanation, or use the tool's help section or Quick Guide.

Save a backup of an existing `.blend` before upgrading. When installing a
replacement ZIP manually, disable the previous copy before enabling its
replacement: original operator names are preserved so existing shortcuts and
scripts continue to work. Do not run both copies simultaneously. Existing scene
settings and light baselines are retained;
upgrading does not attempt to guess which ordinary empties were created by the
old Smart Empty version.

## Updates

In **Preferences → Add-ons**, expand an enabled Helix extension and click
**Check for Updates**. The check covers all enabled Helix extensions: the full
suite updates as one package, while individual installations update their enabled
tools in place. It does not install extra standalone copies alongside the suite
or change your installation choice.

Newer versions are downloaded from the latest public, stable
[GitHub release](https://github.com/Helices3D/Helix-Tools/releases/latest), verified,
and installed automatically using Blender's extension installer. Source changes
on `main`, draft releases, and prereleases are not update targets. An extension
with an equal or newer installed version is left alone. Enable Blender's
**Allow Online Access** preference to use the check.

The optional **Check on Startup** setting is off by default. Enable it to check
and install updates once per Blender session when Blender opens. Installation
waits until playback and rendering stop. The check runs outside Blender's main
process so downloading does not block the interface.

Older releases without **Check for Updates** need one manual ZIP upgrade before
these controls become available.

After installation, a **Restart Blender** notice appears. Save any open work and
restart Blender yourself to load the new code; the updater does not close Blender
or restart it automatically. Enabled state and saved tool settings are preserved.
Downloads are temporary and cleaned up when the check or installation finishes.
Backup ZIPs of replaced packages remain in Blender's extension user storage.
If installation fails, the updater automatically reinstalls the previous package
using Blender's extension installer. If that recovery also fails, the error report
includes the exact backup ZIP path for manual recovery through **Install from
Disk**. Updates do not overwrite your startup scene or save your current
`.blend` file.

## Jump By Time

Use this for lip-sync landmarks noted in another video editor, or for navigating
to editing-time equivalents in a Blender animation.

- **Seconds + Frames** accepts elapsed whole seconds plus extra frames. Frame
  overflow is allowed without losing frames at fractional rates.
- **Elapsed Timestamp** accepts `SS.mmm`, `MM:SS.mmm`, or `HH:MM:SS.mmm`, including
  negative values. These are elapsed timestamps, not SMPTE/drop-frame labels.
- **Start Frame** (the starting-frame offset) is the Blender frame corresponding to time zero in
  the external clip or timeline segment. For example, at 30 fps an offset of 100
  maps 2 seconds + 5 frames to Blender frame 165.
- To calibrate from a known moment, enter its external timestamp, place Blender's
  playhead at the matching moment, expand **Timeline Mapping**, and choose
  **Align to Current Frame**.
- Use the scene's effective frame rate (`fps / fps_base`) or a fractional override.
  The preview shows the actual destination. **Outside Playback Range** explicitly
  allows, clamps, or cancels the jump.

The destination and **Jump to Time** button are next to the time inputs.
Expand **Jump Preview** for the current frame, elapsed time, and detailed input
errors. **Timeline Mapping** contains frame-rate and outside-range settings.

Conversion rounds elapsed time to the nearest whole frame; exact half frames round
away from zero. The integer offset is then added. This prevents repeated rounding
from changing the mapping when calibrating around frame zero.

## Smart Empty

Select an object, or enter Pose Mode and select a bone, then create a Smart Empty.
It captures the evaluated world position and rotation at the current frame, has
no live constraint or parent, and becomes the selected object in Object Mode.
Bone helpers use the head by default, with an optional tail position. Display
size stays separate from object scale so the new anchor has unit transform scale.

Use its local axes to move a target for a bone's **Damped Track** constraint, or
leave it fixed as a reference for **Copy Location** / **Copy Rotation**. The tool
creates helpers; it does not add those bone constraints automatically. Moving the
source later does not move the captured helper.

Display options control the helper's size, name display, and front visibility.
Expand **Anchor Options** to change them or the local-orientation setting.
The management panel records the source object, rig, and bone for every newly
created helper. Object pointers and bone identifiers preserve associations across
renaming and saved `.blend` files. Deleted sources remain visibly identifiable as
missing rather than being silently reassigned.
Read-only linked bones use name tracking when an identifier cannot be stored.
Copied bones with ambiguous identifiers are shown explicitly rather than silently
reassigning existing anchors.

Use the rig filter to manage one rig's helpers. The rig shortcut uses the active
armature. The scrolling anchor list can search by anchor, source, rig, or bone
name; highlight a row to inspect its source and use its select button to activate
the helper. **Hide** affects the current view
layer's viewport visibility; it does not disable rendering. **Restore**
respects helpers that were already hidden before the tool hid that rig. Helpers
for other rigs and ordinary scene empties are left alone.
Read-only linked helpers are skipped because their hide/restore metadata cannot
be saved locally; local helpers created for a linked rig are still supported.

## Camera Timeline Culler

Set a scene camera or camera-cut markers, choose the animation range and samples
per frame, then recalculate. An object is kept if its evaluated bounds intersect
any sampled camera view. A tolerance expands the camera border to retain borderline
geometry. The scan restores the current frame and camera afterwards.
The panel includes a **Camera** picker and reports camera-cut markers.
Expand **Sampling Options** for frame margin and samples per frame.

Culled geometry is grouped for reversible viewport/render hiding. Compare the
result using **Show Full Scene / Show Culled Scene**, then **Restore Collections** to restore the original collection
memberships and visibility when finished. Collection and scene references make
restoration durable across renaming and saving/reopening the project.

The tool retains unsupported, ambiguous, linked, shared-scene, or unbounded
instanced geometry conservatively. Finite sampling can miss fast motion between
samples; motion blur, procedural render geometry, reflections, shadows, and
indirect lighting can require objects outside the camera. Inspect renders before
keeping culling enabled, and increase sampling/tolerance where needed.

## Hair Contact Culler

Use this on fitted native hair in a representative pose. In Object Mode, select
one supported hair **CURVES** object and **1–31 garment MESH objects** in any order.
Open **Helix Tools → Hair Contact Culler**, verify the list, and click
**Build Hair Trim**. Each strand keeps its root-side bends up to the first
radius-aware garment contact; the entire tail beyond it is removed.

Compare with **Show Original**. **Auto** infers each root from the attachment
surface, then endpoint thickness, then stored point order. If the wrong end
survives, choose **First point** or **Last point** and rebuild.

**Full / Low / Hide** control viewport detail. Renders use Full retained strand
detail and the separate garment **Render** switches. **Show Original** affects
only the viewport. Garment visibility also controls its trim contribution.

To extend an existing setup, select the hair and new meshes, click
**Add Selected Garments**, then rebuild. Remove an item with its row's **X**.
Rebuild after changes to pose, garment fit, hair, radii, root direction, or
clearance: saved contacts do not find new collisions on every animation frame.
**Garments → Viewport / Render** controls each garment's contribution to the trim.
**Maintenance → Remove Setup** restores the authored groom. **Hair Trim** keeps
Build/Rebuild and its status first; **Advanced** retains clearance and the saved
root-detection summary. **Quick Guide** explains the full workflow in Blender.

The initial release supports editable sampled POLY hair, fixed topology, explicit
point radii, one material, uniform world scale, and one scene/view layer.
See the [quickstart](../addons/hair_contact_culler/README.md) and
[workflow and input limits](../addons/hair_contact_culler/WORKFLOW.md).

## Light Size and Shadow Control

Open **Helix Tools → Light Control**. In **Target Lights**, choose the entire scene
or specific collections, then click **Enable Eevee Shadows**. This enables the
scene's shadow switch and shadow casting on every included light type, using the
current shadow-detail values. It works immediately with the defaults; no casting
or preset dropdown changes are needed. On another renderer the button reads
**Use Eevee & Enable Shadows** and also switches the scene to Eevee. It leaves
other render-quality settings alone and supports Blender Undo. Child collections
are included. Lights outside the target scope keep their shadow settings.

In **Source Size**, choose Area, Point, Spot, or all three source types.
This filter affects resizing only. One stop of reduction halves dimensions/radius;
two stops produce one quarter. Scaling always uses a saved baseline, so repeated
updates do not compound. Rectangle/ellipse aspect ratios and a positive size floor
are preserved. Sun angular size is excluded; Spot cone settings remain unchanged.

Baselines are captured automatically and saved in the `.blend`. **Set Baseline**
sets a new baseline from the current source sizes; **Restore Sizes** returns to the
saved sizes. Recapture after changing a light's type or area shape. Changing the target
scope restores previously controlled sizes and resets reduction. **Refresh Targets**
handles objects moved between collections.

Open **Eevee Shadows** for optional tuning. **Target Scope** follows Target Lights;
**Entire Scene** overrides the collection filter for the manual apply action.
The manual **Casting → Keep Existing** default preserves each light's casting
choice; the quick enable button explicitly turns casting on for its targets.
**Extra Blur** is available when **Shadow Jitter** is on. **Detailed** and
**Crisp Detail** are starting presets, not guaranteed render-quality improvements. Absolute shadow
resolution is unavailable for Sun lights. **Fixed Distance** anchors the local-light
detail limit one Blender unit from the light, instead of adapting to screen coverage.

**Switch to Suggested Render Preset** switches the current scene to Eevee and
applies 128 render samples, 64 viewport samples, 4 shadow rays, 12 shadow steps,
2 GB of shadow memory, and 1 GB of volume-probe memory. It also enables shadows,
high-quality normals, 16× anisotropic filtering, GPU compositing with automatic
precision, and automatic viewport pixel size. **Viewport Jitter** controls jitter
during viewport interaction. This is reversible: **Restore Render Settings**
returns the affected settings to their values before the first
switch. Reapplying the preset keeps that original snapshot. The snapshot is saved
in the `.blend`; unrelated scene settings are left alone. The suggested values
are a starting point for iteration and can increase memory use and render time.

**Blender Startup → Back Up & Save Startup** is a separate, confirmed action. Its
section is collapsed by default. It applies the preset and saves the entire
current scene and layout as Blender's startup
file, including its objects. Prepare the scene you want for new projects first.
An existing `startup.blend` is copied to a uniquely named
`startup.helix-tools-backup-*.blend` beside it and verified before replacement.
The result shows the backup path. **Restore Startup** restores that file
for future startups while keeping the backup. If no custom startup existed, it
returns to Blender's factory startup instead. This does not reload or change the
currently open scene.

Repeated saves keep the original startup restore point and create an additional
safety backup each time. Automatic restoration refuses to replace a startup file
saved again outside this tool; the saved backup can still be restored manually.
If you explicitly save through the tool again after such an outside change, that
changed startup becomes the new restore point. Blender's user preferences and
the current project's `.blend` file are not saved by these actions.

Scoped operations isolate shared light datablocks where an excluded object would
otherwise change. Linked/read-only targets are skipped with details. A light
object itself shared across scenes remains the same object, so changes to that
included object can affect all its scenes. Disabling the add-on does not restore
sizes automatically; use **Restore Sizes** when you want the original appearance.

## Cloth Cache Manager

Use this to control cloth physics cages and other objects using Cloth modifiers
from one panel, rather than clicking through each cage. Unbaked simulations
usually need sequential playback from their simulation start; jumping ahead can
produce incorrect motion or expensive simulation updates. Play from that start
(often frame 0, depending on the cache range) through the animation frames you
need, then preserve the simulated data with **Cache to Bake** before
navigating those frames.

Open **Helix Tools → Cloth Cache Manager** in Object Mode. Its checklist is
independent of Blender's object selection; checked objects are included by
default, and their choices are saved in the `.blend`. Each Cloth modifier shows
its baked/outdated state, Blender's cache summary, and viewport/render switches.
**Check All / Uncheck All** changes inclusion flags for editable Cloth objects
in the current view layer. It preserves Blender's object selection and leaves
excluded or linked objects alone.

Cages appear in separate collapsible sections for the models they affect.
Grouping follows deformation links such as Mesh Deform and Surface Deform,
then combines meshes belonging to the same rig. A cage affecting multiple models
appears once under **Shared Models**; unattached cages appear under **Ungrouped**.
For an unusual setup, open **Grouping** and choose a **Model** override for the
cage. Clear that field to return to automatic detection. Overrides are saved in
the `.blend` and change the display grouping only; collapsing a group does not
exclude its checked cages from batch actions.

**Cache to Bake** (the **Current Cache to Bake** operator) promotes frames already simulated into a bake. It does
not run a new simulation or fill missing frames; a partially played timeline
produces a partial bake. Empty, outdated, and unverified caches are skipped, and
existing bakes are left alone. Play the simulation again before promoting an
outdated cache.

**Reset Bakes** confirms before freeing the checked objects' cloth bakes and
invalidating their existing simulation data. At the simulation start frame,
Blender immediately clears that data; a cleared cache is a successful reset
even when its status no longer says outdated. Simulate again to rebuild it.
Blender may also mark other physics caches on the same objects outdated, so
check any additional simulations before continuing. Other objects are left
alone. Reset is not a recoverable cache backup or a promise of ordinary Undo:
disk cache files may remain until subsequent simulation regenerates them.

Tick **Don't show again** in the reset confirmation to skip future reset warnings.
Restore the warning with **Show Reset Warning** in the Cloth Cache Manager add-on
preferences, or the Helix Tools preferences when using the full suite. This
choice follows Blender's normal preference saving; if Auto-Save Preferences is
disabled, use **Save Preferences** to keep it across restarts.

Batch actions preserve scene selection, active object, frame/subframe, visibility,
and simulation settings. Linked/read-only targets, external caches, and objects
excluded from the current view layer are skipped. Reset also skips globally
disabled objects that Blender cannot evaluate without changing visibility.
Each operation reports processed, unchanged, skipped, and failed cache counts,
with individual reasons; a failure on one target does not stop the others.

Disable the older `cloth_cage_manager.py` copy before installing this extension.
The **Cloth Cache Manager** title, original operator IDs, and
`cloth_tool_selected` checkboxes are retained; the
panel moves from **Cloth Tools** into the shared **Helix Tools** tab.
