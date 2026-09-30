# Hair Contact Culler

**0.2.1 · Blender 5.2.2 LTS · GPL-3.0-or-later**

Trim native hair where it touches clothing. Each strand keeps its root and
original bends up to its first radius-aware garment contact; everything from
that contact through the tip is removed. The authored groom stays intact.

## Install

Build the repository's extension archives with `python scripts/build_releases.py`
(Python 3.13), or download them from a successful GitHub Actions run. In Blender,
choose **Preferences → Get Extensions → Install from Disk**, select
`hair_contact_culler-0.2.1.zip`, and enable it. Disable an older copy first.
This addon works independently of the other Helix Tools extensions.

## Quickstart

1. Save a test copy of the scene and fit the clothing in a representative pose.
2. In **Object Mode**, select **one native hair CURVES object and the garment
   MESH objects** that should trim it. Selection order does not matter.
3. Press **N** in the 3D Viewport and open **Helix Tools → Hair Contact Culler**.
   Check the inputs, then click **Build Hair Trim**.
4. Use **Show Original** to compare. If the wrong end survives, change
   **Strand root** from Auto to **First point** or **Last point**, then rebuild.
5. Choose **Full**, **Low**, or **Hide** for viewport detail. Renders always use
   Full retained strand detail, with separate **Trim in render** switches.

**Auto** chooses each root using its attachment surface, then endpoint thickness,
then the first stored point. A root already touching a garment removes the whole
strand. Include the character body in the garment list only when intended.

## Adjust or restore

- Select the hair and new clothing, choose **Add Selected Garments**, then
  **Rebuild Hair Trim**. Use a row's **X** to remove a garment, then rebuild.
- **Trim in view / Trim in render** and garment visibility use saved contacts
  immediately. With overlapping garments, the earliest active contact wins.
- Rebuild after changing pose, fit, hair positions/radii, root direction, or
  clearance. This is a fitted-pose tool; it does not solve new animation collisions.
- **Maintenance + restore → Remove Setup** restores the original groom.
  **Show Original** is a temporary viewport comparison; it does not change renders.

## Supported inputs

Editable native **CURVES** with noncyclic builtin **POLY** strands, at least two
points per strand, explicit finite nonnegative point radii, fixed point/strand
order, one object owning the curve data, at most one material, and uniform
orthogonal world scale. Each setup uses one scene/view layer and **1–31 editable
garment meshes**. Legacy CURVE splines, particle hair, changing topology,
nonuniform scale, and shear need separately prepared supported input.

Unsupported or stale inputs display the intact source until rebuilt. Contacts
use garment surfaces; fully enclosed hair without surface contact is not culled.
Procedural shading based on strand length or index can change after trimming.

The panel's **Quick Guide** provides reminders. See [WORKFLOW.md](WORKFLOW.md)
for detailed steps, troubleshooting, and limits; [PROVENANCE.md](PROVENANCE.md)
and [LICENSE](LICENSE) cover origin and licensing.
