# Hair Contact Culler provenance

- Package: `hair_contact_culler`
- Version: `0.2.1`
- Prepared: September 30, 2026
- Maintainer: Helices3D
- License: GPL-3.0-or-later

## Implementation origin

This package's implementation is newly written for the generic native-CURVES input contract, with Codex assistance. It uses directed finite tapered-radius contact tests, per-item first-contact boundaries, inferred or explicit root direction, exact native-curve trimming, world-space radii, explicit item scope, durable source/cache validation, separate viewport/render behavior, and safe rebuild/removal.

Version 0.2.0 replaces version 0.1.0's isolated contacting-edge removal with root-to-first-contact trimming. It keeps the root-side prefix and removes the entire distal tail. A new endpoint interpolates the original contacted edge and its radius; all preceding samples and bends are retained. Native curve trim length is derived from the saved edge coordinate and current deformed samples, rather than a fitted total-length fraction. Old caches are invalidated until rebuilt.

Version 0.2.1 organizes the setup, build and preview workflow, adds explicit garment-list actions and a viewport-only original-hair comparison, and provides a quickstart, full workflow guide and in-app help. Contact mathematics, the native trimming graph and cache schema are unchanged from 0.2.0; compatible unchanged 0.2 caches remain usable.

No private assets, character-specific settings, private scene data, or embedded Blender Text implementation is part of this package. Test fixtures are generated synthetic geometry. The implementation does not depend on private studies or their local file paths.

## Public comparison references

The public [Helix-Tools repository](https://github.com/Helices3D/Helix-Tools) was reviewed for independent-extension packaging, the Helix Tools sidebar convention, exact Blender 5.2.2 runtime targeting, manifest/`bl_info` version consistency, and clean archive lifecycle checks.

Reviewed default-branch commit: `a2a190c5a1bea3caaa66dd0fd6131b3f63bf1576`.

- [Development and validation guide](https://github.com/Helices3D/Helix-Tools/blob/a2a190c5a1bea3caaa66dd0fd6131b3f63bf1576/docs/development.md)
- [Public repository credits](https://github.com/Helices3D/Helix-Tools/blob/a2a190c5a1bea3caaa66dd0fd6131b3f63bf1576/AUTHORS.md)
- [Public repository source provenance](https://github.com/Helices3D/Helix-Tools/blob/a2a190c5a1bea3caaa66dd0fd6131b3f63bf1576/provenance.json)

These are the conventions used to integrate this independent extension into Helix-Tools. Its source history is separate from the repository's original supplied-source hash records. Public repository credits do not imply authorship of this new package by the credited original tools' authors.

The full GPL license text in `LICENSE` is copied from the reviewed public repository's license file. New implementation source uses the SPDX identifier `GPL-3.0-or-later`.

## Verification and publication status

The user confirmed successful testing and explicitly authorized repository upload on September 30, 2026. The four Python files are unchanged from the approved 0.2.1 package. Integration updates packaging, public documentation, and repository checks. The approved-source hashes are recorded under `new_addons` in the repository's `provenance.json`.

Repository checks use generated geometry and Blender 5.2.2 LTS. Passing tests do not establish performance or appearance for every production groom; inspect roots, fitting, and shading in your own scene.
