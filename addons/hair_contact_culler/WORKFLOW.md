# Hair Contact Culler workflow

For **Blender 5.2.2 LTS**. Start with the shorter
[README quickstart](README.md#quickstart), then use this guide while checking
your own hair and garment fit. The panel's **Quick Guide** also gives a short
reminder without leaving Blender.

## 1. Choose the hair and garments

Save a separate copy of your scene. Put the character in a representative
pose and finish fitting the garments. Each hair object gets its own setup;
work with one hair object at a time.

In **Object Mode**, select **one supported hair CURVES object and the garment
MESH objects** that should trim it. Selection order does not matter. Keep
the hair selected when a garment is active. Deselect the rig, lights,
cameras, other hair objects, and unrelated objects. Selecting a body mesh
would make it a contact garment too, so include it only if that is intended.

With the pointer over the 3D Viewport, press **N** and open **Helix Tools →
Hair Contact Culler**. Check the hair and garments shown in the panel.

## 2. Build the trim

Click **Build Hair Trim**. The initial build saves the selected garments
into the setup's list and uses **Auto** root direction.

The result keeps hair from its root to its first garment contact, including
the strand radius, and removes the remaining tail. Original samples and
bends before the contact stay intact. A new endpoint is added within the
contacted edge. If the root is already touching an enabled garment, the
whole strand is removed.

After the first build, selecting that hair opens its existing setup. The
button becomes **Rebuild Hair Trim**, which recalculates the trim using the
garments already in its list. New object selection does not replace that
list or automatically add garments.

### Change the garment list

- **Add:** keep the hair selected, select the new garment meshes, click
  **Add Selected Garments**, then **Rebuild Hair Trim**. Already-listed
  garments are not duplicated.
- **Remove:** click the **X** on a garment's row, then rebuild.
- **Temporarily ignore:** turn that garment's **Viewport** or **Render** trim
  switch off. This does not need a rebuild.

Adding or removing garments displays the original until you rebuild. Keep
at least one garment in a working setup. To start a new selection from
scratch, use **Remove Setup**, select the intended hair and garments, then
build again.

## 3. Compare the result and verify the roots

In **Viewport Preview**, choose **Full**. Turn **Show Original** on to see
the complete authored hair in the viewport; turn it off to return to the trimmed result.
Show Original preserves both the source and the saved setup. Rendering
continues to use the trimmed result at Full strand detail.

For an unobstructed view, turn overlays off, use Solid or Material Preview,
and rotate around the garment edge. Confirm:

- The part attached to the character remains.
- Original bends before the contact remain.
- The tail beyond the garment is gone, rather than floating on its own.

**Auto** decides the root separately for each strand. It prefers the endpoint
nearer the hair's assigned attachment surface; otherwise it prefers the
thicker endpoint. If neither distinguishes the endpoints, it uses the first
stored point. Expand **Advanced** to see the root summary from
the last build and how many decisions used each method.

Auto is a starting point, not a guarantee that every authored strand has
the anatomically correct root. If the wrong side survives, choose **First
point** or **Last point** under **Strand root**, click **Rebuild Hair
Trim**, and repeat the original/result comparison. These choices refer to
the stored order of the strand points and apply to every strand in that
hair object. The source points are not rearranged. Changing root direction
shows the original until the rebuild is complete.

## 4. Choose viewport detail

| Mode | Viewport behavior | Render behavior |
| --- | --- | --- |
| **Full** | All retained strands and their root-side samples and bends. | Full retained strand detail. |
| **Low** | A repeatable subset of complete retained strands; adjust its percentage. | Full retained strand detail. |
| **Hide** | Hair is hidden in the viewport. | Full retained strand detail. |

Low chooses whole strands. It does not simplify each surviving strand or
discard its fitted bends. Other scene and source-evaluation costs can still
affect viewport performance. Turn Show Original off to assess these preview
modes on the trimmed result.

## 5. Control garment visibility and rendering

Each listed garment has independent **Viewport** and **Render** trim
switches. Enable Viewport to use that garment's contacts in the viewport;
enable Render to use them when rendering. These switches control the
hair trim. Use Blender's normal object and collection visibility controls
to show or hide the garment itself.

A garment hidden in the viewport stops trimming viewport hair. A garment
hidden from rendering stops trimming render hair. If two enabled, visible
garments touch a strand, the nearer contact from its root wins. Disabling
that garment restores the strand only as far as the other garment permits.
If no active garment touches it, the complete strand returns.

These visibility changes use saved contacts immediately; they do not require
a rebuild. Render always uses Full strand detail, even while Low, Hide, or
Show Original is selected in the viewport. Inspect your actual hair shader
in a representative render before accepting the fit.

## 6. Rebuild after fitting changes

Contacts and roots are calculated for the pose used during the build.
Retained hair can follow deformation, but this version does not find new
garment intersections on every animation frame.

Use **Rebuild Hair Trim** after changing the pose, garment fit, hair positions
or radii, root direction, garment list, or **Extra clearance**. The status
also tells you when a setup needs a rebuild. A stale or unsupported setup
shows the intact source, including when Hide is selected.

Extra clearance is in **Advanced**. Leave it at its default
for the first check. Increasing it causes contact to be detected slightly
earlier; rebuild and inspect the boundary after a change. Its distance uses world-space
Blender units. The default `0.00002` is 0.02 mm when one Blender unit represents
one meter.

**Check Saved Inputs** in **Maintenance** checks whether saved
inputs and references still match. It does not calculate fresh physical
clearance for a changed pose. Use Rebuild Hair Trim after changing the physical fit.

## 7. Restore, save, or upgrade

Use **Show Original** for a temporary viewport comparison. Use **Remove
Setup** in **Maintenance** to remove the owned modifier, cached attributes,
and settings and restore the authored groom. Build, rebuild, preview changes,
and removal support Blender Undo/Redo; use **Edit → Undo / Redo** while
checking the result.

Save the fitted test copy, reopen it, and inspect a representative render.
Test the independent viewport and render garment switches before accepting
the scene. Do not manually edit generated node groups or cached attributes.

When upgrading, disable the older add-on copy before installing
`hair_contact_culler-0.2.1.zip`. Compatible unchanged 0.2 setups retain their
saved contacts and do not require a rebuild just for this UI update. Older
0.1 caches request a rebuild and show the intact source. Rebuild those
existing setups in a test copy to create the root-to-tip result.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| The panel asks you to choose hair and garments. | Return to Object Mode. Select one supported hair object and the clothing meshes. Deselect other hair and unrelated objects. |
| A build says the source is unsupported. | Check the input requirements below. Native sampled hair CURVES is required; legacy CURVE objects and particle hair need a separately prepared supported copy. |
| No trim is visible. | Turn Show Original off and choose Full. Check the garment's Trim in view switch and its actual viewport visibility. Confirm the hair surface touches the garment in the pose used for the build. |
| The wrong strand end survives. | Choose First point or Last point, rebuild, and compare with Show Original. |
| The trim removes whole strands. | Check Strand root and the garment list. A root already touching a listed garment removes that whole strand; check whether the character body was unintentionally included. |
| Hair returns after a change or reopening. | Read the status. A stale setup, changed root direction, or edited garment list shows the intact source until you rebuild. |
| Hiding one garment leaves some hair trimmed. | Another enabled, visible garment may still provide an earlier contact. Check the other listed items. |
| A render differs from the viewport. | Render uses Full detail and separate Trim in render switches. Also check garment and collection render visibility, then inspect the shader. |
| A new pose intersects a garment. | Rebuild for that pose. The saved boundary does not solve new collisions during animation. |
| Newly selected clothing is missing from the setup. | Rebuild uses the existing list. Select the new meshes with the hair, click Add Selected Garments, then rebuild. |
| Rebuilding fails after editing strand topology. | Remove Setup before changing point count, order, or strand topology; then build a new setup on supported hair. |

## Input requirements

Each setup supports:

- One editable native hair **CURVES** object with actual builtin **POLY**
  strands, noncyclic, with at least two samples per strand.
- An explicit finite, nonnegative **POINT radius** attribute.
- Fixed point and strand topology/order, and curve data owned by one hair
  object.
- Uniform orthogonal world scale, including rotation and translation, with
  no nonuniform scale or shear.
- At most one hair material.
- One scene and one view layer, and **1–31 distinct MESH** garment objects
  in that scene.

Separate supported hair objects can have independent setups and share
garments. Legacy CURVE splines, particle hair, changing procedural topology,
multiple hair materials, nonuniform scale, and shear are outside this version.
An integer attribute merely named `curve_type` does not establish native POLY;
trusted preparation code can set the builtin type with
`data.set_types(type='POLY')` on a prepared copy.

Stable point/strand IDs, sampled radii, attachment UV/index mapping when
present, cache contents, remappable source/material/garment references, and
evaluated curve types are checked before reuse. Remove the setup before
reauthoring topology, then build again. Unsupported or mismatched data falls
back to the source.

## Fitting and appearance limits

Contacts use finite sampled polylines with linearly interpolated radii. The
new centerline endpoint stops at the first radius-aware surface contact, so
it can sit one radius away from the garment surface. The new endpoint radius
is interpolated too. Every preceding original sample and bend remains; a
coarse source cannot supply bends it never contained.

The saved boundary stays on its original contacted edge as that edge deforms.
It does not use a fixed fraction of the whole strand's new length. Hair
entirely enclosed without touching a garment surface is not culled: this is
a surface-contact tool, with no volume or skin-to-hair coverage test.
Uncontacted zero-length strands remain intact; contacted strands with no
retained root-side length are removed.

Procedural shaders that depend on strand length, endpoint, or index can
change appearance after trimming. Inspect the actual material in a render.
Dense production-groom performance depends on the source and scene; inspect
your own viewport and renders.

## Scene review checklist

- [ ] Install in a test profile and check the Blender version.
- [ ] Verify the selected hair and garment list, including add/remove actions.
- [ ] Compare original and trimmed hair in Full with overlays off.
- [ ] Confirm Auto roots or the First/Last override on your own groom.
- [ ] Check independent Trim in view / Trim in render switches and garment overlaps.
- [ ] Check Low/Hide and a Full-detail render with acceptable shading.
- [ ] Change pose or fit, rebuild, and inspect the new boundary.
- [ ] Exercise GUI Undo/Redo, save/reopen, and append in disposable files.
- [ ] Remove Setup and confirm the source is restored.

See [LICENSE](LICENSE) and [PROVENANCE.md](PROVENANCE.md) for GPL licensing and
the generic implementation's origin.
