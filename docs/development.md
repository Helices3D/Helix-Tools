# Development and validation

The supported user runtime is **Blender 5.2.2 LTS**. Each directory under
`addons/` is a complete, independent extension. They share layout conventions
through identical local `_ui.py` helpers; installing one never requires another.

## Run the checks

Use Python 3.13 and Blender Foundation's exact `bpy==5.2.2` package:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements-test.txt
export BLENDER_USER_RESOURCES="$PWD/.blender-test-user"
mkdir -p "$BLENDER_USER_RESOURCES"
.venv/bin/python tests/run_tests.py --report test-results/blender.json
.venv/bin/python scripts/build_releases.py
.venv/bin/python scripts/validate_releases.py
.venv/bin/python scripts/check_release_install.py
```

The regression runner also supports the complete Blender 5.2.2 executable:

```bash
blender --background --factory-startup --disable-autoexec --threads 2 \
  --python-exit-code 1 --python tests/run_tests.py -- --report test-results/blender.json
```

Only use that command when `blender --version` reports the exact required
version. The `bpy` package supplies background APIs, not a desktop executable.

The prepared cloud environment includes the official full Blender executable.
Activate its workspace paths before using it:

```bash
source /workspace/.helix-tools/activate.sh
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
The four extensions are also installed and enabled in the workspace's saved user
preferences; a normal startup loads them. Factory startup keeps regression tests
independent of those installed copies. The system `/usr/bin/blender` is 4.3.2;
activation selects `/workspace/.helix-tools/bin/blender` instead. The isolated
`/workspace/.helix-tools/venv/bin/python` remains available for the API workflow.

Run checks in a fresh process. Tests reset scenes, save temporary `.blend` files,
and may reopen them. Do not run them inside a working project with unsaved changes.
No character files or external assets are needed. A failed check returns a
nonzero exit status, and the runner rejects a zero-test run.

## Coverage and limits

Behavioral checks exercise time conversion, object and bone transforms, helper
ownership and visibility, culling and restoration, durable light baselines,
shared-data isolation, and saving/reopening scenes. Integration checks enable all
four packages together and verify registration cleanup. The archive check extracts
fresh ZIPs into separate Blender extension namespaces and uses Blender's supported
enable/disable lifecycle, including its restricted registration context.
The official Blender validator checks the distributable manifests and layouts.

Headless checks do not establish interactive panel appearance, keyboard workflows,
Undo/Redo behavior, or rendered shadow quality. Before a public release, open all
four sidebar panels in Blender 5.2.2 LTS, exercise their controls and Undo/Redo in
a disposable project, and inspect representative rendered lighting/camera results.
Camera sampling remains an approximation even when every automated check passes.

The GitHub Actions workflow runs the same API tests, builds, validation, and clean
archive checks on Python 3.13. Its artifacts include the four ZIPs, hashes, and a
JSON test report. A local passing run does not imply that hosted CI has run.

## Build distributables

`python scripts/build_releases.py` writes four versioned ZIPs, `SHA256SUMS`, and
`releases.json` to `dist/`. The archives contain the manifest and package entry
point at their root, as Blender's extension installer expects. Fixed archive
timestamps make builds from identical source byte-for-byte reproducible.
Generated artifacts are ignored by Git; source, tests, and packaging scripts
remain reviewable.

Update the package manifest and `bl_info` together when releasing a new version.
Preserve legacy operator IDs and saved-data keys, or add a tested migration.
