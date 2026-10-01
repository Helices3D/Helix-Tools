# Usage and migration

Install any of the versioned ZIPs through **Preferences → Get Extensions →
Install from Disk** in **Blender 5.2.2 LTS**, then enable the installed extensions.
Open the 3D Viewport sidebar (`N`) and choose **Helix Tools**. Each tool installs
and works independently; the tab simply groups their panels consistently.

Click a section's disclosure header to collapse or expand its boxed controls.
Each section folds independently; collapsing it only hides its controls and
does not change the tool's settings or results.

Save a backup of an existing `.blend` before upgrading. Disable the previous copy
of each add-on before enabling its replacement: original operator names are
preserved so existing shortcuts and scripts continue to work. Do not run both
copies simultaneously. Existing scene settings and light baselines are retained;
upgrading does not attempt to guess which ordinary empties were created by the
old Smart Empty version.

## Jump By Time

Use this for lip-sync landmarks noted in another video editor, or for navigating
to editing-time equivalents in a Blender animation.

- **Seconds + Frames** accepts elapsed whole seconds plus extra frames. Frame
  overflow is allowed without losing frames at fractional rates.
- **Elapsed Timestamp** accepts `SS.mmm`, `MM:SS.mmm`, or `HH:MM:SS.mmm`, including
  negative values. These are elapsed timestamps, not SMPTE/drop-frame labels.
- **Starting Frame Offset** is the Blender frame corresponding to time zero in
  the external clip or timeline segment. For example, at 30 fps an offset of 100
  maps 2 seconds + 5 frames to Blender frame 165.
- To calibrate from a known moment, enter its external timestamp, place Blender's
  playhead at the matching moment, and choose **Align Time to Current Frame**.
- Use the scene's effective frame rate (`fps / fps_base`) or a fractional override.
  The preview shows the actual destination. **Outside Playback Range** explicitly
  allows, clamps, or cancels the jump.

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
The management panel records the source object, rig, and bone for every newly
created helper. Object pointers and bone identifiers preserve associations across
renaming and saved `.blend` files. Deleted sources remain visibly identifiable as
missing rather than being silently reassigned.
Read-only linked bones use name tracking when an identifier cannot be stored.
Copied bones with ambiguous identifiers are shown explicitly rather than silently
reassigning existing anchors.

Use the rig filter to manage one rig's helpers. **Hide** affects the current view
layer's viewport visibility; it does not disable rendering. **Restore Visibility**
respects helpers that were already hidden before the tool hid that rig. Helpers
for other rigs and ordinary scene empties are left alone.
Read-only linked helpers are skipped because their hide/restore metadata cannot
be saved locally; local helpers created for a linked rig are still supported.

## Camera Timeline Culler

Set a scene camera or camera-cut markers, choose the animation range and samples
per frame, then recalculate. An object is kept if its evaluated bounds intersect
any sampled camera view. A tolerance expands the camera border to retain borderline
geometry. The scan restores the current frame and camera afterwards.

Culled geometry is grouped for reversible viewport/render hiding. Compare the
result using the visibility toggle, then restore the original collection
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
detail and the separate **Trim in render** switches. **Show Original** affects
only the viewport. Garment visibility also controls its trim contribution.

To extend an existing setup, select the hair and new meshes, click
**Add Selected Garments**, then rebuild. Remove an item with its row's **X**.
Rebuild after changes to pose, garment fit, hair, radii, root direction, or
clearance: saved contacts do not find new collisions on every animation frame.
**Maintenance + restore → Remove Setup** restores the authored groom.

The initial release supports editable sampled POLY hair, fixed topology, explicit
point radii, one material, uniform world scale, and one scene/view layer.
See the [quickstart](../addons/hair_contact_culler/README.md) and
[workflow and input limits](../addons/hair_contact_culler/WORKFLOW.md).

## Light Size and Shadow Control

Choose Area, Point, Spot, or all three source types and a scene/collection scope.
Child collections are included. One stop of reduction halves dimensions/radius;
two stops produce one quarter. Scaling always uses a saved baseline, so repeated
updates do not compound. Rectangle/ellipse aspect ratios and a positive size floor
are preserved. Sun angular size is excluded; Spot cone settings remain unchanged.

Baselines are captured automatically and saved in the `.blend`. **Capture Sizes**
sets a new baseline from the current source sizes; **Restore** returns to the saved
sizes. Recapture after changing a light's type or area shape. Changing the target
scope restores previously controlled sizes and resets reduction. **Refresh Scope**
handles objects moved between collections.

Shadow settings apply to every light type in the chosen shadow scope. The default
preserves each light's existing casting choice. **Detailed** and **Crisp Detail**
are starting presets, not guaranteed render-quality improvements. Absolute shadow
resolution is unavailable for Sun lights.

**Switch to Suggested Render Preset** switches the current scene to Eevee and
applies the values shown in its panel. This is reversible: **Restore Previous
Render Settings** returns the affected settings to their values before the first
switch. Reapplying the preset keeps that original snapshot. The snapshot is saved
in the `.blend`; unrelated scene settings are left alone. The suggested values
are a starting point for iteration and can increase memory use and render time.

**Save Suggested Preset as Startup** is a separate, confirmed action. It applies
the preset and saves the entire current scene and layout as Blender's startup
file, including its objects. Prepare the scene you want for new projects first.
An existing `startup.blend` is copied to a uniquely named
`startup.helix-tools-backup-*.blend` beside it and verified before replacement.
The result shows the backup path. **Restore Previous Startup** restores that file
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
sizes automatically; use **Restore** when you want the original appearance.

## Cloth Cache Manager

Use this to control cloth physics cages and other objects using Cloth modifiers
from one panel, rather than clicking through each cage. Unbaked simulations
usually need sequential playback from their simulation start; jumping ahead can
produce incorrect motion or expensive simulation updates. Play from that start
(often frame 0, depending on the cache range) through the animation frames you
need, then preserve the simulated data with **Current Cache to Bake** before
navigating those frames.

Open **Helix Tools → Cloth Cache Manager** in Object Mode. Its checklist is
independent of Blender's object selection; checked objects are included by
default, and their choices are saved in the `.blend`. Each Cloth modifier shows
its baked/outdated state, Blender's cache summary, and viewport/render switches.

**Current Cache to Bake** promotes frames already simulated into a bake. It does
not run a new simulation or fill missing frames; a partially played timeline
produces a partial bake. Empty, outdated, and unverified caches are skipped, and
existing bakes are left alone. Play the simulation again before promoting an
outdated cache.

**Reset Bakes** confirms before freeing the checked objects' cloth bakes and
marking their existing simulation data outdated. Simulate again to rebuild it.
Blender may also mark other physics caches on the same objects outdated, so
check any additional simulations before continuing. Other objects are left
alone. Reset is not a recoverable cache backup or a promise of ordinary Undo:
disk cache files may remain until subsequent simulation regenerates them.

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
