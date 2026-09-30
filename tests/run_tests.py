"""Run the real Blender API regressions in a fresh, disposable process."""

import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))


def require_isolated_profile():
    """Refuse scene-reset checks until existing disposable user paths are active."""
    import bpy
    for resource in ("CONFIG", "SCRIPTS", "DATAFILES", "EXTENSIONS"):
        configured = os.environ.get("BLENDER_USER_" + resource)
        if not configured or not Path(configured).is_dir():
            raise RuntimeError(f"Create and set a disposable BLENDER_USER_{resource} before checks")
        actual = Path(bpy.utils.user_resource(resource)).resolve()
        if actual != Path(configured).resolve():
            raise RuntimeError(f"{resource} escaped the disposable profile: {actual}")


def main():
    import bpy

    if bpy.app.version != (5, 2, 2):
        raise RuntimeError(f"Tests require Blender 5.2.2 LTS; got {bpy.app.version_string}")
    require_isolated_profile()
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--report", type=Path)
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    options = parser.parse_args(arguments)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=options.pattern)
    if suite.countTestCases() == 0:
        raise RuntimeError("No tests discovered")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps({
            "blender": bpy.app.version_string,
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "successful": result.wasSuccessful(),
        }, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
