# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared automatic updater, mirrored into every independent extension.

HTTP and archive inspection run in a separate Python process. Only this main-
thread controller accesses Blender or invokes its supported extension installer.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import uuid
import zipfile

import addon_utils
import bpy
from bpy.props import BoolProperty

from .worker import parse_version, validate_zip

PACKAGE_IDS = (
    "jump_by_time", "smart_empty", "camera_timeline_culler",
    "area_light_shadow_control", "hair_contact_culler", "cloth_cache_manager", "texture_resolution",
    "helix_tools",
)
RUNTIME_KEY = "_helix_tools_update_runtime_v1"


def runtime():
    """Keep a batch alive while Blender reloads individual package modules."""
    state = sys.modules.get(RUNTIME_KEY)
    if state is None:
        state = ModuleType(RUNTIME_KEY)
        state.job = None
        state.status = ""
        state.backups = []
        state.startup_checked = False
        sys.modules[RUNTIME_KEY] = state
    return state


def _manifest(directory):
    import tomllib
    return tomllib.loads((Path(directory) / "blender_manifest.toml").read_text(encoding="utf-8"))


def discover_targets(context):
    """Update enabled extension roots; never interpret suite children as installs."""
    repositories = {repo.module: repo for repo in context.preferences.extensions.repos}
    targets = []
    for entry in context.preferences.addons:
        parts = entry.module.split(".")
        if len(parts) != 3 or parts[0] != "bl_ext" or parts[2] not in PACKAGE_IDS:
            continue
        repo = repositories.get(parts[1])
        module = sys.modules.get(entry.module)
        if repo is None or not repo.enabled or repo.source == 'SYSTEM' or module is None:
            continue
        if not addon_utils.check(entry.module)[1]:
            continue
        root = Path(module.__file__).resolve().parent
        if root.parent != Path(repo.directory).resolve():
            raise ValueError("An installed Helix extension does not match its repository")
        manifest = _manifest(root)
        if manifest.get("id") != parts[2]:
            raise ValueError("An installed Helix extension has an unexpected package ID")
        parse_version(manifest["version"])
        targets.append({"id": parts[2], "version": manifest["version"],
                        "repo": parts[1], "module": entry.module, "root": str(root)})
    return sorted(targets, key=lambda target: target["module"])


def python_executable():
    """Use Blender's bundled interpreter, or the supported bpy test interpreter."""
    directory = Path(bpy.utils.system_resource('PYTHON')) / "bin"
    for name in ("python.exe", f"python{sys.version_info.major}.{sys.version_info.minor}", "python3"):
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    candidate = Path(sys.executable)
    if candidate.is_file() and candidate.name.lower().startswith("python"):
        return str(candidate)
    raise ValueError("Blender's bundled Python executable could not be found")


def can_install(context):
    return not bpy.app.is_job_running('RENDER') and not any(
        screen.is_animation_playing for screen in bpy.data.screens
    )


def _remove_timer(callback):
    if bpy.app.timers.is_registered(callback):
        bpy.app.timers.unregister(callback)


def _class_identity(cls):
    if issubclass(cls, bpy.types.Operator):
        namespace, name = cls.bl_idname.split(".")
        return bpy.types.Operator, f"{namespace.upper()}_OT_{name}"
    return bpy.types.AddonPreferences, cls.__name__


def _popup(message, *, title="Helix Tools Updates", icon='INFO'):
    def draw(menu, context):
        for line in message.splitlines():
            menu.layout.label(text=line)
    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def backup_package(target, directory):
    """Keep the previous install outside the directory Blender will replace."""
    root, directory = Path(target["root"]), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    archive_path = directory / f"{target['id']}-{target['version']}-{uuid.uuid4().hex[:12]}.zip"
    total = 0
    try:
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                if path.is_symlink():
                    raise ValueError("The installed package contains a symbolic link; update was not installed")
                if path.is_file():
                    total += path.stat().st_size
                    if total > 64 * 1024 * 1024:
                        raise ValueError("The installed package is too large to back up automatically")
                    archive.write(path, path.relative_to(root).as_posix())
        validate_zip(archive_path.read_bytes(), target["id"], target["version"], (5, 2, 2))
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return str(archive_path)


def installed_version_matches(target, version):
    loaded = sys.modules.get(target["module"])
    return (loaded is not None
            and _manifest(Path(loaded.__file__).parent).get("version") == version
            and addon_utils.check(target["module"])[1])


class NoUpdater:
    classes = ()

    def register(self):
        pass

    def unregister(self):
        pass


class Controller:
    def __init__(self, package, package_file):
        self.package = package
        self.package_id = package.rsplit(".", 1)[-1]
        self.package_file = package_file
        self._registered_classes = []
        self._startup_callback = self.startup
        self._poll_callback = self.poll
        controller = self

        def draw(preferences, context):
            layout = preferences.layout
            layout.use_property_split = False
            layout.use_property_decorate = False
            job = runtime().job
            if job is None:
                layout.operator(f"helix_updates.check_{controller.package_id}", text="Check for Updates", icon='FILE_REFRESH')
            else:
                row = layout.row()
                row.enabled = not job["installing"]
                row.operator(f"helix_updates.cancel_{controller.package_id}", text="Cancel Update Check", icon='CANCEL')
            layout.prop(preferences, "auto_check")
            if runtime().status:
                layout.label(text=runtime().status)
            if not bpy.app.online_access:
                layout.label(text="Enable Blender Online Access to check.", icon='INFO')

        def execute_check(operator, context):
            try:
                controller.start(context)
            except (OSError, RuntimeError, ValueError) as error:
                operator.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            operator.report({'INFO'}, "Checking enabled Helix tools for updates.")
            return {'FINISHED'}

        def execute_cancel(operator, context):
            job = runtime().job
            if job is not None and not job["installing"]:
                job["owner"].cancel()
            return {'FINISHED'}

        prefs = type(f"HX_{self.package_id}_UpdatePreferences", (bpy.types.AddonPreferences,), {
            "__module__": package,
            "bl_idname": package,
            "__annotations__": {"auto_check": BoolProperty(
                name="Check on Startup", default=False,
                description="Automatically check, download and install newer enabled Helix tools when Blender starts; show a restart reminder after installation",
            )},
            "draw": draw,
        })
        check = type(f"HELIX_UPDATES_OT_check_{self.package_id}", (bpy.types.Operator,), {
            "__module__": package,
            "bl_idname": f"helix_updates.check_{self.package_id}",
            "bl_label": "Check for Updates",
            "bl_description": "Check all enabled Helix extensions, verify newer stable release downloads, install them into their existing repositories, and show a restart reminder",
            "execute": execute_check,
        })
        cancel = type(f"HELIX_UPDATES_OT_cancel_{self.package_id}", (bpy.types.Operator,), {
            "__module__": package,
            "bl_idname": f"helix_updates.cancel_{self.package_id}",
            "bl_label": "Cancel Update Check",
            "bl_description": "Cancel the download or pending updates; already installed updates remain installed",
            "execute": execute_cancel,
        })
        self.classes = (prefs, check, cancel)

    def register(self):
        if self._registered_classes:
            return
        for cls in self.classes:
            base, identifier = _class_identity(cls)
            if base.bl_rna_get_subclass_py(identifier, None) is not None:
                raise RuntimeError("Another Helix update controller is already registered")
        try:
            for cls in self.classes:
                bpy.utils.register_class(cls)
                self._registered_classes.append(cls)
            bpy.app.timers.register(self._startup_callback, first_interval=1.0)
        except Exception:
            self.unregister()
            raise

    def unregister(self):
        _remove_timer(self._startup_callback)
        job = runtime().job
        if job is not None and job["owner"] is self:
            if not job["installing"]:
                self.cancel()
            else:
                _remove_timer(self._poll_callback)
        for cls in reversed(tuple(self._registered_classes)):
            base, identifier = _class_identity(cls)
            if base.bl_rna_get_subclass_py(identifier, None) is cls:
                bpy.utils.unregister_class(cls)
            self._registered_classes.remove(cls)

    def startup(self):
        if not self._registered_classes or runtime().job is not None or runtime().startup_checked:
            return None
        entry = bpy.context.preferences.addons.get(self.package)
        if entry is not None and getattr(entry.preferences, "auto_check", False) and bpy.app.online_access:
            try:
                self.start(bpy.context, startup=True)
            except (OSError, RuntimeError, ValueError) as error:
                runtime().status = str(error)
        return None

    def start(self, context, *, startup=False):
        if bpy.app.version != (5, 2, 2):
            raise ValueError("Helix updates require Blender 5.2.2 LTS")
        if not bpy.app.online_access:
            raise ValueError("Enable Blender Online Access before checking for updates")
        if runtime().job is not None:
            raise ValueError("A Helix update check is already running")
        targets = discover_targets(context)
        if not targets or not any(target["module"] == self.package for target in targets):
            raise ValueError("Install and enable a Helix extension ZIP before checking for updates")
        storage_path = bpy.utils.extension_path_user(self.package, path="updates", create=True)
        if not storage_path:
            raise ValueError("Extension user storage is unavailable; update check was not started")
        storage = Path(storage_path)
        directory = Path(tempfile.mkdtemp(prefix="check-", dir=storage))
        request = {"targets": [{key: target[key] for key in ("id", "version", "repo", "module")} for target in targets],
                   "download_dir": str(directory), "blender_version": [5, 2, 2]}
        request_path, result_path = directory / "request.json", directory / "result.json"
        try:
            request_path.write_text(json.dumps(request), encoding="utf-8")
            process = subprocess.Popen([
                python_executable(), str(Path(__file__).with_name("worker.py")),
                "--request", str(request_path), "--result", str(result_path),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            shutil.rmtree(directory)
            raise
        runtime().job = {"owner": self, "process": process, "directory": directory,
                         "result_path": result_path, "targets": targets, "startup": startup,
                         "storage": storage, "updates": None, "installed": [], "errors": [],
                         "installing": False}
        runtime().status = "Checking GitHub releases…"
        runtime().startup_checked = True
        try:
            bpy.app.timers.register(self._poll_callback, first_interval=0.25, persistent=True)
        except Exception:
            self.cancel()
            raise

    def cancel(self):
        job = runtime().job
        if job is None or job["owner"] is not self or job["installing"]:
            return
        process = job["process"]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        _remove_timer(self._poll_callback)
        if job["installed"]:
            job["errors"].append("Remaining updates cancelled.")
            self._finish(job)
            return
        shutil.rmtree(job["directory"], ignore_errors=True)
        runtime().job = None
        runtime().status = "Update check cancelled."

    def _finish(self, job):
        installed, errors, notes = job["installed"], job["errors"], job.get("notes", [])
        runtime().job = None
        shutil.rmtree(job["directory"], ignore_errors=True)
        if installed:
            runtime().status = f"Installed {len(installed)} update(s). Restart Blender."
            message = "Updates installed.\nSave your work and restart Blender."
            if errors:
                message += "\nSome updates failed; see update status."
            _popup(message, title="Restart Blender", icon='FILE_REFRESH')
        elif errors:
            runtime().status = errors[0]
            if not job["startup"]:
                _popup(errors[0], icon='ERROR')
        else:
            runtime().status = notes[0] if notes else "Enabled Helix tools are up to date."
        if errors and installed:
            runtime().status += " " + errors[0]
        elif notes and installed:
            runtime().status += " " + notes[0]
        return None

    def poll(self):
        state, job = runtime(), runtime().job
        if job is None or job["owner"] is not self:
            return None
        if not bpy.app.online_access:
            self.cancel()
            state.status = "Online Access disabled; pending updates cancelled."
            return None
        if job["process"].poll() is None:
            return 0.25
        if job["updates"] is None:
            try:
                if job["result_path"].stat().st_size > 1024 * 1024:
                    raise ValueError("Update result is too large")
                result = json.loads(job["result_path"].read_text(encoding="utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("Unexpected update result")
                if result.get("ok") is not True:
                    raise ValueError(result.get("error", "Update check failed"))
                if job["process"].poll() != 0:
                    raise ValueError("The update worker did not complete successfully")
                frozen = {target["module"]: target for target in job["targets"]}
                updates = result.get("updates")
                if not isinstance(updates, list) or len(updates) > len(frozen):
                    raise ValueError("Unexpected update result")
                seen = set()
                for update in updates:
                    if not isinstance(update, dict):
                        raise ValueError("Unexpected update package")
                    target = frozen.get(update["module"])
                    if target is None or update["module"] in seen or any(update[key] != target[key] for key in ("id", "repo")):
                        raise ValueError("Unexpected update package identity")
                    seen.add(update["module"])
                    if parse_version(update["version"]) <= parse_version(target["version"]):
                        raise ValueError("Update result would not be an upgrade")
                    path = Path(update["file"])
                    if path.is_symlink() or path.resolve().parent != job["directory"].resolve() or not path.is_file():
                        raise ValueError("Unexpected update archive location")
                # Install the host last: reloading other packages cannot cancel this batch.
                job["updates"] = sorted(updates, key=lambda item: item["module"] == self.package)
                job["notes"] = [note for note in result.get("skipped", []) if isinstance(note, str)]
            except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
                job["errors"].append(str(error))
                return self._finish(job)
        if not job["updates"]:
            return self._finish(job)
        if not can_install(bpy.context):
            state.status = "Updates downloaded; waiting for playback or rendering to stop."
            return 0.5
        update = job["updates"].pop(0)
        try:
            current = next((target for target in discover_targets(bpy.context) if target["module"] == update["module"]), None)
            if current is None or parse_version(current["version"]) >= parse_version(update["version"]):
                return 0.25
            path = Path(update["file"])
            if path.is_symlink() or not path.is_file() or path.resolve().parent != job["directory"].resolve() or path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Unexpected update archive location or size")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != update["sha256"]:
                raise ValueError("Downloaded update checksum changed; update was not installed")
            validate_zip(data, current["id"], update["version"], bpy.app.version)
            backup = backup_package(current, job["storage"] / "backups")
            state.backups.append(backup)
            state.status = f"Installing {current['id']} {update['version']}…"
            job["installing"] = True
            try:
                outcome = bpy.ops.extensions.package_install_files(
                    filepath=str(path), repo=current["repo"], enable_on_install=True,
                )
                if outcome != {'FINISHED'}:
                    raise RuntimeError("Blender could not install the verified update")
                if not installed_version_matches(current, update["version"]):
                    raise RuntimeError("Update did not enable correctly")
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                try:
                    restored = bpy.ops.extensions.package_install_files(
                        filepath=backup, repo=current["repo"], enable_on_install=True,
                    )
                    if restored != {'FINISHED'} or not installed_version_matches(current, current["version"]):
                        raise RuntimeError("Previous version did not enable correctly")
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as recovery_error:
                    raise RuntimeError(
                        f"{error}. Recovery failed: {recovery_error}.\nInstall this backup manually: {backup}"
                    ) from recovery_error
                raise RuntimeError(f"{error}. Previous version restored.") from error
            job["installed"].append(update["module"])
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            job["errors"].append(f"{update['id']}: {error}")
        finally:
            job["installing"] = False
        if not job["updates"]:
            return self._finish(job)
        return 0.25


def create_updater(package, package_file):
    if package.startswith("bl_ext.") and len(package.split(".")) != 3:
        return NoUpdater()
    return Controller(package, package_file)
