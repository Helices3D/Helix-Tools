# Development and validation

The supported user runtime is **Blender 5.2.2 LTS**. Each directory under
`addons/` is a complete, independent extension. They share layout conventions
through independent local `_ui.py` helpers; installing one never requires another.
The optional `suite/helix_tools/` wrapper packages the same six tools into one
extension. Both installation choices preserve the tools' operator IDs and saved
data keys. Disable one choice before enabling the other.

Update preferences use the shared implementation in `shared/helix_updates/`.
Each standalone archive includes its own copy; the suite has one updater for its
root extension. Run `python scripts/sync_updaters.py` after editing the shared
source to refresh the tracked `_updates/` copies in each add-on and the suite.
The builder rejects copies that differ from the shared source.

Keep sidebar controls consistent with Light Control: label/value rows without
animation decorators, a prominent primary action near its inputs, independent
collapsible sections, and optional details closed initially. Use concise visible
labels with full hover descriptions. Explicit toggle buttons and expanded enum
rows need `use_property_split = False` so they fill the row. Check populated and
empty states at both normal and narrow sidebar widths in native Blender;
headless draw traces cannot catch clipped text or blank enum buttons.

## Run the checks

Use Python 3.13 and Blender Foundation's exact `bpy==5.2.2` package:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements-test.txt
export BLENDER_USER_RESOURCES="$PWD/.blender-test-user"
export BLENDER_USER_CONFIG="$BLENDER_USER_RESOURCES/config"
export BLENDER_USER_SCRIPTS="$BLENDER_USER_RESOURCES/scripts"
export BLENDER_USER_DATAFILES="$BLENDER_USER_RESOURCES/datafiles"
export BLENDER_USER_EXTENSIONS="$BLENDER_USER_RESOURCES/extensions"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_SCRIPTS" "$BLENDER_USER_DATAFILES" "$BLENDER_USER_EXTENSIONS"
.venv/bin/python tests/run_tests.py --report test-results/blender.json
.venv/bin/python scripts/build_releases.py
.venv/bin/python scripts/validate_releases.py
.venv/bin/python scripts/check_release_install.py
```

The regression runner also supports the complete Blender 5.2.2 executable:

Keep the disposable environment above active for these executable commands.
On Windows, set the same absolute `BLENDER_USER_*` paths in PowerShell and create
each directory before launching Blender. The regression and archive runners
refuse to reset scenes unless Blender resolves those disposable paths.

```bash
blender --background --factory-startup --disable-autoexec --threads 2 \
  --python-exit-code 1 --python tests/run_tests.py -- --report test-results/blender.json
```

Only use that command when `blender --version` reports the exact required
version. The `bpy` package supplies background APIs, not a desktop executable.

The prepared cloud environment includes the official full Blender executable.
Activate its workspace paths before using it:

```bash
source /workspace/.helix-tools/activate-tests.sh
blender --version
blender --background --factory-startup --disable-autoexec --threads 2 \
  --python-exit-code 1 --python tests/run_tests.py -- --report test-results/blender.json
python scripts/build_releases.py
blender --background --factory-startup --disable-autoexec --threads 2 \
  --python-exit-code 1 --python scripts/validate_releases.py
blender --background --factory-startup --disable-autoexec --threads 2 \
  --python-exit-code 1 --python scripts/check_release_install.py
```

Validate each ZIP with `blender --command extension validate dist/<package>.zip`.
The extensions are also installed and enabled in the workspace's saved user
preferences; a normal startup loads them. Factory startup keeps regression tests
independent of those installed copies, and `activate-tests.sh` uses a separate,
disposable profile for all four Blender user-resource paths. Use `activate.sh`
instead to load the persistent installed copies. The system `/usr/bin/blender` is 4.3.2;
activation selects `/workspace/.helix-tools/bin/blender` instead. The isolated
`/workspace/.helix-tools/venv/bin/python` remains available for the API workflow.

Run checks in a fresh process. Tests reset scenes, save temporary `.blend` files,
and may reopen them. Do not run them inside a working project with unsaved changes.
No character files or external assets are needed. Hair checks create their
own native curves and garment meshes; no production scenes are included. A failed
check returns a nonzero exit status, and the runner rejects a zero-test run.

## Coverage and limits

Behavioral checks exercise time conversion, object and bone transforms, helper
ownership and visibility, culling and restoration, durable light baselines,
shared-data isolation, root-to-contact hair trimming, original/preview separation,
and saving/reopening scenes. Integration checks enable all
six standalone packages together and verify registration cleanup. Suite checks
exercise its complete registration, rollback after a component fails, cleanup,
duplicate-copy rejection, and switching between suite and individual installs
while retaining saved tool settings. Cloth checks exercise real
simulation caches, promotion, invalidation, skipped targets, and preserved scene
selection, frame, visibility, and simulation settings. The archive check extracts
fresh ZIPs into separate Blender extension namespaces and uses Blender's supported
enable/disable lifecycle, including its restricted registration context.
Sidebar draw checks use registered scene data to exercise open and collapsed
sections, independent headers, and unchanged tool settings.
The official Blender validator checks the distributable manifests and layouts.

Updater checks cover release/version selection, package identities, SHA-256
verification, Blender compatibility, and installation lifecycle. Network work
runs in a worker subprocess that does not import or access `bpy`; Blender API
calls stay on the main thread. The updater respects Blender's online-access
preference and uses the native extension installer after verified downloads.

Headless checks do not establish interactive panel appearance, keyboard workflows,
Undo/Redo behavior, or rendered shadow quality. Before a public release, open all
the sidebar panels in Blender 5.2.2 LTS, exercise their controls and Undo/Redo in
a disposable project, and inspect representative rendered lighting/camera results.
Camera sampling remains an approximation even when every automated check passes.

The GitHub Actions workflow runs the same API tests, builds, validation, and clean
archive checks on Python 3.13. Its artifacts include the seven ZIPs, hashes, and a
JSON test report. A local passing run does not imply that hosted CI has run.

## Build distributables

`python scripts/build_releases.py` writes six standalone versioned ZIPs and one
complete-suite ZIP, `SHA256SUMS`, and `releases.json` to `dist/`. The initial suite
archive uses `helix_tools-<version>.zip`. Every archive contains its manifest and package
entry point at the root, as Blender's extension installer expects. The suite
archive includes the wrapper from `suite/helix_tools/` and all six tool packages;
it installs directly without unpacking or installing nested ZIPs. Fixed archive
timestamps make builds from identical source byte-for-byte reproducible.
Generated artifacts are ignored by Git; source, tests, and packaging scripts
remain reviewable.

Update the package manifest and `bl_info` together when releasing a new version.
Preserve legacy operator IDs and saved-data keys, or add a tested migration.
Whenever a component changes, also bump the suite wrapper's manifest and
`bl_info` versions so the complete download receives a new version.

## Publish downloads

Use a tag of the form `helix-tools-v<bundle_version>`, matching the suite wrapper's
version, to publish a release. The tag workflow runs the checks and builds all
seven archives from the same commit before publishing. `scripts/prepare_release.py`
prepares the assets and release metadata. Each release includes versioned ZIPs,
hashes, metadata, and stable download aliases: `helix_tools.zip` for the complete
suite and `<tool_id>.zip` for each standalone tool. The README links these aliases
through GitHub's `releases/latest/download/` route.

Ordinary pushes to `main` build and check artifacts without publishing a release.
Publish the tag after the corresponding `main` checks pass, then verify the
published asset links and hashes. Updating source on `main` alone does not change
the public downloads.

The built-in updater reads the latest public stable release and its
`releases.json` metadata. Keep release metadata, archive hashes, manifests, and
stable ZIP aliases consistent; unreleased changes on `main` do not reach users
through update checks. A suite update replaces the root suite extension rather
than installing its embedded components separately. Preserve the root manifest
IDs so installed extensions can be updated in their existing repositories.
