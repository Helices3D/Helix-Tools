"""Offline update validation and native installation/lifecycle regressions."""

import hashlib
import importlib.util
import io
import json
import re
from pathlib import Path
import sys
from types import SimpleNamespace
from contextlib import contextmanager
import tempfile
import unittest
from unittest import mock
import zipfile

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_releases import BUNDLE_ID, PACKAGES, build
from check_release_install import installed_release_repository

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_helix_update_worker_tests", ROOT / "shared" / "helix_updates" / "worker.py",
)
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)

_controller_spec = importlib.util.spec_from_file_location(
    "_helix_updater_controller_tests", ROOT / "shared" / "helix_updates" / "__init__.py",
    submodule_search_locations=[str(ROOT / "shared" / "helix_updates")],
)
controller_api = importlib.util.module_from_spec(_controller_spec)
sys.modules[_controller_spec.name] = controller_api
_controller_spec.loader.exec_module(controller_api)


def _zip(package_id, version, *, extra=(), minimum="5.2.2", maximum="5.2.3"):
    manifest = (
        'schema_version = "1.0.0"\n'
        f'id = "{package_id}"\nversion = "{version}"\n'
        'name = "Helix Update Fixture"\n'
        'tagline = "Offline update installation fixture"\n'
        'maintainer = "Helices3D"\ntype = "add-on"\n'
        f'blender_version_min = "{minimum}"\nblender_version_max = "{maximum}"\n'
        'license = ["SPDX:GPL-3.0-or-later"]\n'
    )
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("blender_manifest.toml", manifest)
        archive.writestr("__init__.py", "def register(): pass\ndef unregister(): pass\n")
        for name, data in extra:
            archive.writestr(name, data)
    return result.getvalue()


def _target(package_id, version="1.0.0", repo="helix_updates_tests"):
    return {"id": package_id, "version": version, "repo": repo,
            "module": f"bl_ext.{repo}.{package_id}"}


class OfflineRelease:
    """A single published release snapshot; requests never reach the network."""

    def __init__(self, packages):
        self.tag = "helix-tools-v1.1.0"
        self.records = []
        self.archives = {}
        self.calls = []
        for package_id, version, archive in packages:
            filename = f"{package_id}-{version}.zip"
            digest = hashlib.sha256(archive).hexdigest()
            self.records.append({"id": package_id, "version": version, "file": filename,
                                 "kind": "suite" if package_id == "helix_tools" else "standalone",
                                 "sha256": digest})
            self.archives[filename] = archive
        self.release = {"tag_name": self.tag, "draft": False, "prerelease": False,
                        "assets": []}
        self.refresh_assets()

    def refresh_assets(self):
        self.index = json.dumps(self.records).encode()
        self.release["assets"] = [
            {"name": name, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
             "browser_download_url": "https://untrusted.invalid/ignored"}
            for name, data in {"releases.json": self.index, **self.archives}.items()
        ]

    def fetch(self, url, limit):
        self.calls.append(url)
        if url == worker.LATEST_URL:
            return json.dumps(self.release).encode()
        prefix = f"{worker.DOWNLOAD_ROOT}/{self.tag}/"
        if not url.startswith(prefix):
            raise AssertionError(f"Unexpected download URL: {url}")
        name = url[len(prefix):]
        return self.index if name == "releases.json" else self.archives[name]


class FakeProcess:
    def __init__(self, completed=True):
        self.returncode = 0 if completed else None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _drive_poll(controller):
    """Mirror native timer semantics when driving its callback synchronously."""
    result = controller.poll()
    if result is None and bpy.app.timers.is_registered(controller._poll_callback):
        bpy.app.timers.unregister(controller._poll_callback)
    return result


class _BpyInstallerBoundary:
    def __init__(self, installer):
        self.ops = SimpleNamespace(extensions=SimpleNamespace(package_install_files=installer))

    def __getattr__(self, name):
        return getattr(bpy, name)


@contextmanager
def _installer_boundary(api, *, actual=False, side_effect=None):
    native = bpy.ops.extensions.package_install_files
    installer = mock.Mock(side_effect=side_effect if side_effect is not None else
                          native if actual else AssertionError("Unexpected installation"))
    # bpy.ops constructs submodule objects on lookup, so patch the controller's
    # stable API boundary rather than a transient bpy.ops.extensions object.
    with mock.patch.object(api, "bpy", _BpyInstallerBoundary(installer)):
        yield installer


class UpdaterControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="helix-controller-tests-")
        self.addCleanup(self.directory.cleanup)
        self.storage = Path(self.directory.name)
        self.state = controller_api.runtime()
        self.assertIsNone(self.state.job)
        self.old_status, self.old_backups = self.state.status, list(self.state.backups)
        self.old_startup_checked = self.state.startup_checked
        self.state.startup_checked = False
        self.old_online = bpy.context.preferences.system.use_online_access
        bpy.context.preferences.system.use_online_access = True
        self.assertTrue(bpy.app.online_access)
        self.controller = controller_api.create_updater(
            "bl_ext.helix_update_unit.jump_by_time", str(ROOT / "addons" / "jump_by_time" / "__init__.py"),
        )
        self.controller.register()
        self.addCleanup(self.cleanup_controller)
        self.target = {**_target("jump_by_time"), "root": str(ROOT / "addons" / "jump_by_time")}
        self.target["repo"] = "helix_update_unit"
        self.target["module"] = self.controller.package
        self.popup = mock.patch.object(controller_api, "_popup").start()
        self.addCleanup(mock.patch.stopall)

    def cleanup_controller(self):
        self.controller.unregister()
        if self.state.job is not None:
            self.state.job["owner"].cancel()
        entry = bpy.context.preferences.addons.get(self.controller.package)
        if entry is not None:
            bpy.context.preferences.addons.remove(entry)
        for callback in (self.controller._startup_callback, self.controller._poll_callback):
            self.assertFalse(bpy.app.timers.is_registered(callback))
        self.assertTrue(all(not cls.is_registered for cls in self.controller.classes))
        self.state.status, self.state.backups = self.old_status, self.old_backups
        self.state.startup_checked = self.old_startup_checked
        bpy.context.preferences.system.use_online_access = self.old_online

    def start(self, *, result=None, running=False):
        process = FakeProcess(completed=not running)
        self.process = process
        release = OfflineRelease([("jump_by_time", "2.1.0", _zip("jump_by_time", "2.1.0"))])

        def launch(arguments, **kwargs):
            request_path = Path(arguments[arguments.index("--request") + 1])
            result_path = Path(arguments[arguments.index("--result") + 1])
            request = json.loads(request_path.read_text())
            completed = worker.check_for_updates(request, fetch=release.fetch)
            self.assertTrue(completed["ok"], completed)
            result_path.write_text(json.dumps(completed if result is None else result))
            return process

        with mock.patch.object(controller_api, "discover_targets", return_value=[self.target]), \
                mock.patch.object(bpy.utils, "extension_path_user", return_value=str(self.storage)), \
                mock.patch.object(controller_api, "python_executable", return_value=sys.executable), \
                mock.patch.object(controller_api.subprocess, "Popen", side_effect=launch) as popen:
            self.controller.start(bpy.context)
            popen.assert_called_once()
        self.process = process
        return self.state.job

    def test_registration_under_restrictblend_is_static_and_failed_enable_rolls_back(self):
        import _bpy_restrict_state
        self.controller.unregister()
        with mock.patch.object(_bpy_restrict_state, "_bpy", bpy), \
                mock.patch.object(controller_api.subprocess, "Popen", side_effect=AssertionError("No network during register")), \
                _bpy_restrict_state.RestrictBlend():
            self.controller.register()
            self.assertTrue(all(cls.is_registered for cls in self.controller.classes))
        self.assertTrue(bpy.app.timers.is_registered(self.controller._startup_callback))
        self.controller.unregister()
        original = bpy.utils.register_class

        def fail_last(cls):
            if cls is self.controller.classes[-1]:
                raise RuntimeError("Injected updater registration failure")
            return original(cls)

        with mock.patch.object(bpy.utils, "register_class", side_effect=fail_last):
            with self.assertRaisesRegex(RuntimeError, "Injected updater registration failure"):
                self.controller.register()
        self.assertTrue(all(not cls.is_registered for cls in self.controller.classes))
        self.assertFalse(bpy.app.timers.is_registered(self.controller._startup_callback))
        self.assertIsNone(self.state.job)

    def test_canonical_operator_collision_preserves_foreign_class_without_partial_preferences(self):
        self.controller.unregister()
        foreign = type("FOREIGN_OT_update_action", (bpy.types.Operator,), {
            "__module__": __name__, "bl_idname": self.controller.classes[1].bl_idname,
            "bl_label": "Foreign update fixture", "execute": lambda self, context: {'FINISHED'},
        })
        bpy.utils.register_class(foreign)
        try:
            with self.assertRaises(RuntimeError):
                self.controller.register()
            self.controller.unregister()
            self.assertTrue(foreign.is_registered)
            self.assertTrue(all(not cls.is_registered for cls in self.controller.classes))
            self.assertEqual(bpy.ops.helix_updates.check_jump_by_time(), {'FINISHED'})
        finally:
            bpy.utils.unregister_class(foreign)

    def test_startup_checks_are_opt_in_and_respect_online_access(self):
        entry = bpy.context.preferences.addons.new()
        entry.module = self.controller.package
        self.assertFalse(entry.preferences.auto_check)
        with mock.patch.object(self.controller, "start") as start:
            self.controller.startup()
            start.assert_not_called()
            entry.preferences.auto_check = True
            bpy.context.preferences.system.use_online_access = False
            self.controller.startup()
            start.assert_not_called()
            bpy.context.preferences.system.use_online_access = True
            self.controller.startup()
            start.assert_called_once_with(bpy.context, startup=True)
            self.state.startup_checked = True
            self.controller.startup()
            start.assert_called_once_with(bpy.context, startup=True)

    def test_offline_or_uninstalled_checks_start_no_subprocess(self):
        with mock.patch.object(controller_api.subprocess, "Popen") as popen:
            bpy.context.preferences.system.use_online_access = False
            with self.assertRaisesRegex(ValueError, "Online Access"):
                self.controller.start(bpy.context)
            bpy.context.preferences.system.use_online_access = True
            with mock.patch.object(controller_api, "discover_targets", return_value=[]):
                with self.assertRaisesRegex(ValueError, "Install and enable"):
                    self.controller.start(bpy.context)
            popen.assert_not_called()
        self.assertIsNone(self.state.job)

    def test_one_shared_job_survives_unrelated_disable_and_owner_disable_cancels_it(self):
        other = controller_api.create_updater("bl_ext.helix_update_other.smart_empty", "unused.py")
        other.register()
        self.addCleanup(other.unregister)
        job = self.start(running=True)
        folder = job["directory"]
        with mock.patch.object(controller_api.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "already running"):
                other.start(bpy.context)
            popen.assert_not_called()
        other.unregister()
        self.assertIs(self.state.job, job)
        self.assertFalse(self.process.terminated)
        self.controller.unregister()
        self.assertTrue(self.process.terminated)
        self.assertFalse(folder.exists())
        self.assertIsNone(self.state.job)
        self.assertFalse(bpy.app.timers.is_registered(self.controller._poll_callback))

    def test_playback_defers_downloaded_installation_and_cancel_removes_workspace(self):
        job = self.start()
        folder = job["directory"]
        with mock.patch.object(controller_api, "can_install", return_value=False), \
                _installer_boundary(controller_api) as install:
            self.assertEqual(_drive_poll(self.controller), 0.5)
            self.assertIn("playback or rendering", self.state.status)
            self.assertTrue(job["updates"])
            install.assert_not_called()
        self.controller.cancel()
        self.assertIsNone(self.state.job)
        self.assertFalse(folder.exists())
        self.popup.assert_not_called()

    def test_active_render_or_playback_is_an_installation_barrier(self):
        for render, playback, expected in ((True, False, False), (False, True, False), (False, False, True)):
            with self.subTest(render=render, playback=playback):
                proxy = SimpleNamespace(app=SimpleNamespace(is_job_running=lambda job: render),
                                        data=SimpleNamespace(screens=[SimpleNamespace(is_animation_playing=playback)]))
                with mock.patch.object(controller_api, "bpy", proxy):
                    self.assertEqual(controller_api.can_install(None), expected)

    def test_malformed_completed_results_end_cleanly_without_installing(self):
        for result in (None, [], {}, {"ok": True, "updates": "invalid"},
                       {"ok": True, "updates": [None]}, {"ok": False, "error": "Offline fixture failure"}):
            with self.subTest(result=result):
                job = self.start(result={"ok": False} if result is None else result)
                if result is None:
                    job["result_path"].write_text("null")
                folder = job["directory"]
                with _installer_boundary(controller_api) as install:
                    self.assertIsNone(_drive_poll(self.controller))
                    install.assert_not_called()
                self.assertIsNone(self.state.job)
                self.assertFalse(folder.exists())
                self.assertTrue(self.state.status)
                self.assertNotIn("Restart Blender", self.state.status)

    def test_download_identity_location_and_changed_checksums_are_revalidated(self):
        for mutation in ("identity", "downgrade", "outside", "tamper"):
            with self.subTest(mutation=mutation):
                job = self.start()
                result = json.loads(job["result_path"].read_text())
                update = result["updates"][0]
                if mutation == "identity":
                    update["repo"] = "another_repo"
                elif mutation == "downgrade":
                    update["version"] = self.target["version"]
                elif mutation == "outside":
                    destination = self.storage / "outside.zip"
                    destination.write_bytes(Path(update["file"]).read_bytes())
                    update["file"] = str(destination)
                else:
                    with Path(update["file"]).open("ab") as archive:
                        archive.write(b"changed after download")
                job["result_path"].write_text(json.dumps(result))
                with mock.patch.object(controller_api, "discover_targets", return_value=[self.target]), \
                        _installer_boundary(controller_api) as install:
                    self.assertIsNone(_drive_poll(self.controller))
                    install.assert_not_called()
                self.assertIsNone(self.state.job)
                self.assertNotIn("Restart Blender", self.state.status)

    def test_empty_storage_cannot_create_a_job_or_launch_a_process(self):
        with mock.patch.object(controller_api, "discover_targets", return_value=[self.target]), \
                mock.patch.object(bpy.utils, "extension_path_user", return_value=""), \
                mock.patch.object(controller_api.tempfile, "mkdtemp") as create_directory, \
                mock.patch.object(controller_api.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                self.controller.start(bpy.context)
            create_directory.assert_not_called()
            launch.assert_not_called()
        self.assertIsNone(self.state.job)
        self.assertFalse(bpy.app.timers.is_registered(self.controller._poll_callback))

    def test_polling_registration_failure_terminates_worker_and_cleans_workspace(self):
        register = bpy.app.timers.register

        def fail_poll(callback, **kwargs):
            if callback == self.controller._poll_callback:
                raise RuntimeError("Injected polling timer failure")
            return register(callback, **kwargs)

        with mock.patch.object(bpy.app.timers, "register", side_effect=fail_poll):
            with self.assertRaisesRegex(RuntimeError, "Injected polling timer failure"):
                self.start(running=True)
        self.assertTrue(self.process.terminated)
        self.assertIsNone(self.state.job)
        self.assertEqual(list(self.storage.glob("check-*")), [])

    def test_loading_a_blend_keeps_owned_download_polling_and_allows_cancel(self):
        job = self.start(running=True)
        path = self.storage / "pending-update.blend"
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(path)), {'FINISHED'})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(path)), {'FINISHED'})
        self.assertIs(self.state.job, job)
        self.assertTrue(bpy.app.timers.is_registered(self.controller._poll_callback))
        self.assertEqual(_drive_poll(self.controller), 0.25)
        self.controller.cancel()
        self.assertTrue(self.process.terminated)
        self.assertFalse(job["directory"].exists())
        self.assertIsNone(self.state.job)

    def test_a_reloaded_controller_does_not_repeat_a_completed_session_check(self):
        self.start(result={"ok": True, "updates": []})
        self.assertIsNone(_drive_poll(self.controller))
        self.assertTrue(self.state.startup_checked)
        entry = bpy.context.preferences.addons.new()
        entry.module = self.controller.package
        entry.preferences.auto_check = True
        package, package_file = self.controller.package, self.controller.package_file
        self.controller.unregister()
        self.controller = controller_api.create_updater(package, package_file)
        self.controller.register()
        with mock.patch.object(self.controller, "start") as start:
            self.controller.startup()
            start.assert_not_called()

    def test_disabling_online_access_cancels_pending_downloads(self):
        job = self.start(running=True)
        folder = job["directory"]
        bpy.context.preferences.system.use_online_access = False
        self.assertIsNone(_drive_poll(self.controller))
        self.assertTrue(self.process.terminated)
        self.assertIsNone(self.state.job)
        self.assertFalse(folder.exists())
        self.assertIn("Online Access disabled", self.state.status)


def _with_manifest_version(source, destination, version):
    """Use real current code under an older manifest to exercise native upgrades."""
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, "w") as output:
        for info in archive.infolist():
            data = archive.read(info)
            if info.filename == "blender_manifest.toml":
                data = re.sub(rb'(?m)^version\s*=\s*"[^"\n]+"',
                              f'version = "{version}"'.encode(), data)
            output.writestr(info, data)


class NativeUpdaterInstallationTests(unittest.TestCase):
    repository_number = 0

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="helix-native-update-tests-")
        cls.folder = Path(cls.directory.name)
        cls.releases = build(cls.folder / "dist")
        cls.current = {item["id"]: cls.folder / "dist" / item["file"] for item in cls.releases}
        cls.versions = {item["id"]: item["version"] for item in cls.releases}
        cls.older = {}
        for item in cls.releases:
            destination = cls.folder / f"{item['id']}-0.0.1.zip"
            _with_manifest_version(cls.current[item["id"]], destination, "0.0.1")
            cls.older[item["id"]] = destination

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.old_online = bpy.context.preferences.system.use_online_access
        bpy.context.preferences.system.use_online_access = True
        self.addCleanup(setattr, bpy.context.preferences.system, "use_online_access", self.old_online)
        self.state = controller_api.runtime()
        self.assertIsNone(self.state.job)
        old_status, old_backups = self.state.status, list(self.state.backups)
        old_startup_checked = self.state.startup_checked
        self.state.startup_checked = False
        self.addCleanup(self.restore_runtime, old_status, old_backups, old_startup_checked)
        self.storage = tempfile.TemporaryDirectory(prefix="helix-native-update-storage-")
        self.addCleanup(self.storage.cleanup)

    def restore_runtime(self, status, backups, startup_checked):
        if self.state.job is not None:
            self.state.job["owner"].cancel()
        self.state.status, self.state.backups = status, backups
        self.state.startup_checked = startup_checked

    @contextmanager
    def installed(self, package_ids):
        type(self).repository_number += 1
        repository_id = f"helix_update_native_{self.repository_number}"
        archives = {package_id: self.older[package_id] for package_id in package_ids}
        with installed_release_repository(archives, repository_id=repository_id,
                                          enable_on_install=True) as prefix:
            modules = {package_id: sys.modules[f"{prefix}.{package_id}"] for package_id in package_ids}
            yield prefix, modules

    def start_completed(self, host):
        api = sys.modules[type(host._UPDATER).__module__]
        controller = host._UPDATER
        release = OfflineRelease([
            (item["id"], item["version"], self.current[item["id"]].read_bytes())
            for item in self.releases
        ])
        process = FakeProcess()

        def launch(arguments, **kwargs):
            request = json.loads(Path(arguments[arguments.index("--request") + 1]).read_text())
            result = worker.check_for_updates(request, fetch=release.fetch)
            self.assertTrue(result["ok"], result)
            Path(arguments[arguments.index("--result") + 1]).write_text(json.dumps(result))
            return process

        with mock.patch.object(bpy.utils, "extension_path_user", return_value=self.storage.name), \
                mock.patch.object(api, "python_executable", return_value=sys.executable), \
                mock.patch.object(api.subprocess, "Popen", side_effect=launch) as popen:
            controller.start(bpy.context)
            popen.assert_called_once()
        return api, controller, self.state.job

    def finish(self, controller):
        for _ in range(10):
            if self.state.job is None:
                return
            _drive_poll(controller)
        self.fail("Offline update fixture did not finish")

    def test_native_batch_replaces_existing_repositories_preserves_scene_preferences_and_backups(self):
        ids = ("jump_by_time", "smart_empty", "area_light_shadow_control", "texture_resolution")
        with self.installed(ids) as (prefix, modules):
            scene = bpy.context.scene
            scene.helix_texture_resolution.threshold = 8192
            scene.helix_texture_resolution.include_shared = True
            scene.jbt_offset = 145
            scene.jbt_fps_override = 29.97
            scene.helix_smart_empty_settings.empty_size = 0.75
            bpy.ops.object.empty_add(location=(2, 3, 4))
            source = bpy.context.object
            source.name = "Update Anchor Source"
            self.assertEqual(bpy.ops.object.add_smart_empty_baked(), {'FINISHED'})
            anchor_name = bpy.context.object.name
            light = bpy.data.lights.new("Update Light", 'AREA')
            light.size, light.size_y, light.shape = 2, 4, 'RECTANGLE'
            scene.collection.objects.link(bpy.data.objects.new("Update Light", light))
            scene.area_light_shadow_control.size_reduction = 1.0
            baseline_key = modules["area_light_shadow_control"].BASELINE_KEY
            self.assertIn(baseline_key, light)
            for package_id in ids:
                bpy.context.preferences.addons[f"{prefix}.{package_id}"].preferences.auto_check = True
            root_paths = {package_id: Path(module.__file__).parent for package_id, module in modules.items()}
            api, controller, job = self.start_completed(modules["jump_by_time"])
            with mock.patch.object(api, "_popup") as popup, \
                    mock.patch.object(controller, "unregister", wraps=controller.unregister) as unregister, \
                    _installer_boundary(api, actual=True) as install:
                self.finish(controller)
                self.assertEqual(install.call_count, len(ids))
                unregister.assert_called_once()  # Self-install really disables the active owner.
            self.assertEqual(job["installed"][-1], f"{prefix}.jump_by_time")
            self.assertEqual(set(job["installed"]), {f"{prefix}.{package_id}" for package_id in ids})
            self.assertEqual(job["errors"], [])
            self.assertEqual(scene.helix_texture_resolution.threshold, 8192)
            self.assertTrue(scene.helix_texture_resolution.include_shared)
            self.assertEqual(scene.jbt_offset, 145)
            self.assertAlmostEqual(scene.jbt_fps_override, 29.97, places=4)
            self.assertAlmostEqual(scene.helix_smart_empty_settings.empty_size, 0.75)
            anchor = bpy.data.objects[anchor_name]
            self.assertTrue(anchor.helix_smart_empty.is_tracked)
            self.assertEqual(anchor.helix_smart_empty.source_object, source)
            self.assertEqual(anchor.helix_smart_empty.owner_scene, scene)
            self.assertIn(baseline_key, light)
            self.assertAlmostEqual(light.size, 1.0)
            self.assertEqual(bpy.ops.alsc.restore_sizes(), {'FINISHED'})
            self.assertAlmostEqual(light.size, 2.0)
            for package_id in ids:
                module_name = f"{prefix}.{package_id}"
                loaded = sys.modules[module_name]
                self.assertEqual(Path(loaded.__file__).parent, root_paths[package_id])
                self.assertEqual(api._manifest(root_paths[package_id])["version"], self.versions[package_id])
                self.assertEqual(addon_utils.check(module_name), (True, True))
                self.assertTrue(bpy.context.preferences.addons[module_name].preferences.auto_check)
            self.assertIn("Restart Blender", self.state.status)
            popup.assert_called_once()
            self.assertEqual(popup.call_args.kwargs["title"], "Restart Blender")
            self.assertIn("Save your work", popup.call_args.args[0])
            self.assertFalse(job["directory"].exists())
            self.assertFalse(bpy.app.timers.is_registered(controller._poll_callback))
            backups = self.state.backups[len(self.state.backups) - len(ids):]
            self.assertEqual(len(backups), len(ids))
            for backup in backups:
                with zipfile.ZipFile(backup) as archive:
                    manifest = archive.read("blender_manifest.toml").decode()
                    self.assertIn('version = "0.0.1"', manifest)
                    self.assertFalse(any("__pycache__" in name for name in archive.namelist()))

    def test_suite_updates_once_without_installing_children_or_disabled_standalones(self):
        with self.installed((BUNDLE_ID,)) as (prefix, modules):
            suite = modules[BUNDLE_ID]
            child_entry = bpy.context.preferences.addons.new()
            child_entry.module = f"{prefix}.{BUNDLE_ID}.jump_by_time"
            try:
                api = sys.modules[type(suite._UPDATER).__module__]
                targets = api.discover_targets(bpy.context)
                self.assertEqual([target["module"] for target in targets], [suite.__name__])
                self.assertEqual(targets[0]["version"], "0.0.1")
                self.assertTrue(all(component._UPDATER.classes == () for component in suite.modules))
                scene = bpy.context.scene
                scene.jbt_offset = 247
                api, controller, job = self.start_completed(suite)
                with mock.patch.object(api, "_popup") as popup, _installer_boundary(api, actual=True) as install:
                    self.finish(controller)
                self.assertEqual(install.call_count, 1)
                self.assertEqual(job["installed"], [suite.__name__])
                self.assertEqual(scene.jbt_offset, 247)
                self.assertEqual(api.discover_targets(bpy.context)[0]["version"], self.versions[BUNDLE_ID])
                loaded = sys.modules[suite.__name__]
                self.assertEqual(loaded.MODULE_NAMES, PACKAGES)
                self.assertTrue(all(component._UPDATER.classes == () for component in loaded.modules))
                popup.assert_called_once()
            finally:
                bpy.context.preferences.addons.remove(child_entry)

    def test_failed_native_update_restores_previous_version_or_reports_usable_backup(self):
        for fail_recovery in (False, True):
            with self.subTest(fail_recovery=fail_recovery), self.installed(("jump_by_time",)) as (prefix, modules):
                host = modules["jump_by_time"]
                entry = bpy.context.preferences.addons[host.__name__]
                entry.preferences.auto_check = True
                scene = bpy.context.scene
                scene.jbt_offset = 391
                api, controller, job = self.start_completed(host)
                native = bpy.ops.extensions.package_install_files
                calls = []

                def fail_after_native_install(**kwargs):
                    calls.append(kwargs["filepath"])
                    if len(calls) == 1:
                        self.assertEqual(native(**kwargs), {'FINISHED'})
                        return {'CANCELLED'}  # Exercise recovery after files and module changed.
                    if fail_recovery:
                        raise RuntimeError("Injected recovery failure")
                    return native(**kwargs)

                with mock.patch.object(api, "_popup") as popup, \
                        _installer_boundary(api, side_effect=fail_after_native_install) as install:
                    self.finish(controller)
                self.assertEqual(install.call_count, 2)
                self.assertEqual(job["installed"], [])
                self.assertEqual(len(job["errors"]), 1)
                self.assertIsNone(self.state.job)
                self.assertFalse(job["directory"].exists())
                self.assertEqual(scene.jbt_offset, 391)
                self.assertTrue(bpy.context.preferences.addons[host.__name__].preferences.auto_check)
                self.assertEqual(addon_utils.check(host.__name__), (True, True))
                root = Path(sys.modules[host.__name__].__file__).parent
                backup = calls[1]
                self.assertTrue(Path(backup).is_file())
                worker.validate_zip(Path(backup).read_bytes(), "jump_by_time", "0.0.1")
                if fail_recovery:
                    self.assertEqual(api._manifest(root)["version"], self.versions["jump_by_time"])
                    self.assertIn("Install this backup manually", self.state.status)
                    self.assertIn(backup, self.state.status)
                else:
                    self.assertEqual(api._manifest(root)["version"], "0.0.1")
                    self.assertIn("Previous version restored", self.state.status)
                popup.assert_called_once()
                self.assertNotIn("Restart Blender", self.state.status)

    def test_discovery_accepts_enabled_roots_across_repositories_and_ignores_disabled_installs(self):
        with self.installed(("jump_by_time",)) as (first_prefix, first), \
                self.installed(("smart_empty",)) as (second_prefix, second):
            api = sys.modules[type(first["jump_by_time"]._UPDATER).__module__]
            found = api.discover_targets(bpy.context)
            self.assertEqual({item["module"] for item in found},
                             {f"{first_prefix}.jump_by_time", f"{second_prefix}.smart_empty"})
            self.assertEqual({item["repo"] for item in found},
                             {first_prefix.split(".")[1], second_prefix.split(".")[1]})
            addon_utils.disable(f"{second_prefix}.smart_empty", default_set=True)
            self.assertEqual([item["module"] for item in api.discover_targets(bpy.context)],
                             [f"{first_prefix}.jump_by_time"])


if __name__ == "__main__":
    unittest.main()
