# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified-update tests run without Blender, external requests, or real installs."""

from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import warnings
import zipfile


WORKER_PATH = Path(__file__).resolve().parents[1] / "shared" / "helix_updates" / "worker.py"
spec = importlib.util.spec_from_file_location("helix_update_test_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def make_archive(package_id="smart_empty", version="2.0.2", extra=(), minimum="5.2.2", maximum="5.2.3", kind="add-on"):
    manifest = (f'id = "{package_id}"\nversion = "{version}"\ntype = "{kind}"\n'
                f'blender_version_min = "{minimum}"\nblender_version_max = "{maximum}"\n')
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore", UserWarning)
        archive.writestr("__init__.py", "# Downloaded source must never be executed by the worker.\n")
        archive.writestr("blender_manifest.toml", manifest)
        for name, data in extra:
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self, packages=None):
        self.calls = []
        self.tag = "helix-tools-v1.0.2"
        self.archives = packages or {"smart_empty": ("2.0.2", make_archive())}
        self.records = [{"id": package_id, "version": version,
                         "file": f"{package_id}-{version}.zip",
                         "kind": "suite" if package_id == "helix_tools" else "standalone",
                         "sha256": hashlib.sha256(data).hexdigest()}
                        for package_id, (version, data) in self.archives.items()]
        self.release = {"tag_name": self.tag, "draft": False, "prerelease": False, "assets": []}
        self.refresh_assets()
        self.overrides = {}

    def refresh_assets(self):
        index = json.dumps(self.records).encode()
        self.release["assets"] = [{"name": "releases.json", "size": len(index),
                                   "digest": "sha256:" + hashlib.sha256(index).hexdigest()}]
        for package_id, (version, data) in self.archives.items():
            self.release["assets"].append({"name": f"{package_id}-{version}.zip", "size": len(data),
                                           "digest": "sha256:" + hashlib.sha256(data).hexdigest()})

    def __call__(self, url, limit):
        self.calls.append((url, limit))
        if url in self.overrides:
            return self.overrides[url]
        if url == worker.LATEST_URL:
            return json.dumps(self.release).encode()
        if url == f"{worker.DOWNLOAD_ROOT}/{self.tag}/releases.json":
            return json.dumps(self.records).encode()
        for package_id, (version, data) in self.archives.items():
            if url == f"{worker.DOWNLOAD_ROOT}/{self.tag}/{package_id}-{version}.zip":
                return data
        raise AssertionError(f"Worker attempted an unexpected URL: {url}")


class UpdateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helix-worker-")
        self.directory = Path(self.temp.name) / "downloads"
        self.request = {"targets": [self.target()], "download_dir": str(self.directory),
                        "blender_version": [5, 2, 2]}
        self.github = FakeGitHub()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def target(package_id="smart_empty", version="2.0.1", repo="user_default"):
        return {"id": package_id, "version": version, "repo": repo,
                "module": f"bl_ext.{repo}.{package_id}"}

    def assert_failed(self, result, message):
        self.assertFalse(result["ok"])
        self.assertIn(message, result["error"])

    def test_newer_release_download_is_verified_and_never_installs_or_executes(self):
        result = worker.check_for_updates(self.request, self.github)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tag"], self.github.tag)
        update, = result["updates"]
        self.assertEqual({key: update[key] for key in ("id", "repo", "module")},
                         {key: self.request["targets"][0][key] for key in ("id", "repo", "module")})
        self.assertEqual(update["version"], "2.0.2")
        output = Path(update["file"])
        self.assertEqual(output.parent, self.directory)
        self.assertEqual(output.read_bytes(), self.github.archives["smart_empty"][1])
        self.assertEqual(update["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(len(list(self.directory.iterdir())), 1)
        self.assertEqual(sum(url == worker.LATEST_URL for url, _ in self.github.calls), 1)
        self.assertEqual(len(self.github.calls), 3)

    def test_numeric_versions_do_not_downgrade_or_download_equal_versions(self):
        for version in ("2.0.2", "2.0.10", "10.0.0"):
            with self.subTest(version=version):
                self.request["targets"] = [self.target(version=version)]
                github = FakeGitHub()
                result = worker.check_for_updates(self.request, github)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["updates"], [])
                self.assertEqual(len(github.calls), 2)
                self.assertFalse(self.directory.exists())
        self.assertGreater(worker.parse_version("2.0.10"), worker.parse_version("2.0.9"))
        for version in ("2.0.3-beta", "2.0", "02.0.0", "2.0.-1", True, None):
            with self.subTest(invalid=version), self.assertRaises(worker.UpdateError):
                worker.parse_version(version)

    def test_mixed_installed_tools_and_future_records_only_update_installed_newer_ids(self):
        github = FakeGitHub({"smart_empty": ("2.0.2", make_archive()),
                             "jump_by_time": ("2.0.3", make_archive("jump_by_time", "2.0.3"))})
        github.records.append({"id": "future_tool", "arbitrary_future_schema": {"version": "future"}})
        github.refresh_assets()
        self.request["targets"] = [self.target(), self.target("jump_by_time", "2.0.3"),
                                   self.target("camera_timeline_culler", "2.0.1")]
        result = worker.check_for_updates(self.request, github)
        self.assertTrue(result["ok"], result)
        self.assertEqual([record["id"] for record in result["updates"]], ["smart_empty"])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertEqual(len(github.calls), 3)

    def test_same_package_in_two_repositories_reuses_one_verified_download(self):
        self.request["targets"] += [self.target(repo="another_repo")]
        result = worker.check_for_updates(self.request, self.github)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["updates"]), 2)
        self.assertEqual(result["updates"][0]["file"], result["updates"][1]["file"])
        self.assertNotEqual(result["updates"][0]["module"], result["updates"][1]["module"])
        self.assertEqual(len(self.github.calls), 3)

    def test_suite_archive_uses_only_suite_target(self):
        data = make_archive("helix_tools", "1.0.2", extra=(("smart_empty/__init__.py", "# embedded\n"),))
        github = FakeGitHub({"helix_tools": ("1.0.2", data)})
        self.request["targets"] = [self.target("helix_tools", "1.0.1")]
        result = worker.check_for_updates(self.request, github)
        self.assertTrue(result["ok"], result)
        self.assertEqual([update["id"] for update in result["updates"]], ["helix_tools"])

    def test_untrusted_index_urls_and_identity_fields_cannot_choose_download_or_destination(self):
        self.github.records[0].update({"url": "https://evil.example/payload.zip", "repo": "evil",
                                       "module": "evil", "stable_file": "../payload.zip"})
        self.github.release["assets"][1]["browser_download_url"] = "https://evil.example/payload.zip"
        self.github.refresh_assets()
        result = worker.check_for_updates(self.request, self.github)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["updates"][0]["repo"], "user_default")
        self.assertEqual(result["updates"][0]["module"], "bl_ext.user_default.smart_empty")
        self.assertTrue(all(url.startswith((worker.LATEST_URL, worker.DOWNLOAD_ROOT)) for url, _ in self.github.calls))
        self.github.records[0]["file"] = "../payload.zip"
        self.github.refresh_assets()
        self.assert_failed(worker.check_for_updates(self.request, self.github), "invalid metadata")

    def test_release_tag_draft_prerelease_and_duplicate_assets_are_rejected(self):
        for change in ({"tag_name": "other-v1.0.2"}, {"tag_name": "helix-tools-v1.0.2-beta"},
                       {"draft": True}, {"prerelease": True}, {"draft": 0}):
            with self.subTest(change=change):
                github = FakeGitHub()
                github.release.update(change)
                self.assertFalse(worker.check_for_updates(self.request, github)["ok"])
                self.assertEqual(len(github.calls), 1)
        self.github.release["assets"].append(deepcopy(self.github.release["assets"][0]))
        self.assert_failed(worker.check_for_updates(self.request, self.github), "duplicate release assets")

    def test_index_and_archive_checksums_must_match_github_and_release_index(self):
        self.github.release["assets"][0]["digest"] = "sha256:" + "0" * 64
        self.assert_failed(worker.check_for_updates(self.request, self.github), "does not match GitHub")
        self.github.refresh_assets()
        self.github.records[0]["sha256"] = "0" * 64
        self.github.refresh_assets()
        self.assert_failed(worker.check_for_updates(self.request, self.github), "does not match the release")
        self.github.records[0]["sha256"] = hashlib.sha256(self.github.archives["smart_empty"][1]).hexdigest()
        self.github.refresh_assets()
        self.github.release["assets"][1]["digest"] = "sha256:" + "0" * 64
        self.assert_failed(worker.check_for_updates(self.request, self.github), "does not match GitHub")
        self.github.release["assets"][1]["digest"] = "md5:irrelevant"
        self.assert_failed(worker.check_for_updates(self.request, self.github), "invalid checksum")
        self.assertFalse(self.directory.exists())

    def test_absent_optional_github_digest_still_requires_index_archive_checksum(self):
        for asset in self.github.release["assets"]:
            del asset["digest"]
        result = worker.check_for_updates(self.request, self.github)
        self.assertTrue(result["ok"], result)

    def test_archive_identity_and_blender_compatibility_are_required(self):
        for data in (make_archive("jump_by_time"), make_archive(version="2.0.3"),
                     make_archive(kind="theme"), make_archive(minimum="5.2.3"),
                     make_archive(maximum="5.2.2"), make_archive(minimum="invalid")):
            with self.subTest(data_size=len(data)), self.assertRaises(worker.UpdateError):
                worker.validate_zip(data, "smart_empty", "2.0.2")
        self.assertEqual(worker.validate_zip(make_archive(), "smart_empty", "2.0.2")["id"], "smart_empty")

    def test_unsafe_archive_paths_case_collisions_links_and_conflicts_are_rejected(self):
        names = ("../evil.py", "/evil.py", "C:/evil.py", "a\\evil.py", "a//evil.py",
                 "a/./evil.py", "a/../evil.py", "a/file.", "NUL.txt", "a/file ", "a/evil\n.py", "a/*.py")
        for name in names:
            with self.subTest(name=name), self.assertRaises(worker.UpdateError):
                worker.validate_zip(make_archive(extra=((name, "bad"),)), "smart_empty", "2.0.2")
        for extra in ((("__init__.py", "duplicate"),), (("__INIT__.py", "case duplicate"),),
                      (("folder", "file"), ("folder/code.py", "file"))):
            with self.subTest(extra=extra), self.assertRaises(worker.UpdateError):
                worker.validate_zip(make_archive(extra=extra), "smart_empty", "2.0.2")
        symlink = zipfile.ZipInfo("link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(worker.UpdateError, "link or special file"):
            worker.validate_zip(make_archive(extra=((symlink, "../outside"),)), "smart_empty", "2.0.2")

    def test_bounded_metadata_downloads_archive_expansion_and_member_count(self):
        self.github.overrides[worker.LATEST_URL] = b"x" * (worker.JSON_LIMIT + 1)
        self.assert_failed(worker.check_for_updates(self.request, self.github), "exceeds the allowed size")
        with patch.object(worker, "EXPANDED_LIMIT", 100):
            with self.assertRaisesRegex(worker.UpdateError, "expands beyond"):
                worker.validate_zip(make_archive(extra=(("big.txt", "x" * 1000),)), "smart_empty", "2.0.2")
        with patch.object(worker, "FILE_LIMIT", 2):
            with self.assertRaisesRegex(worker.UpdateError, "expands beyond"):
                worker.validate_zip(make_archive(extra=(("extra.txt", "file"),)), "smart_empty", "2.0.2")
        self.github.overrides.clear()
        self.github.release["assets"][1]["size"] = worker.ZIP_LIMIT + 1
        self.assert_failed(worker.check_for_updates(self.request, self.github), "exceeds the allowed size")

    def test_corrupt_compressed_member_and_crc_are_reported_as_damaged_archives(self):
        original = make_archive()
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            init = archive.getinfo("__init__.py")
            start = init.header_offset
            name_size, extra_size = struct.unpack_from("<HH", original, start + 26)
            compressed_start = start + 30 + name_size + extra_size
        corrupted = bytearray(original)
        corrupted[compressed_start] = 255
        with self.assertRaisesRegex(worker.UpdateError, "damaged"):
            worker.validate_zip(bytes(corrupted), "smart_empty", "2.0.2")
        github = FakeGitHub({"smart_empty": ("2.0.2", bytes(corrupted))})
        self.assert_failed(worker.check_for_updates(self.request, github), "damaged")
        self.assertFalse(self.directory.exists())

    def test_malformed_metadata_missing_asset_and_duplicate_tools_fail_without_installation(self):
        for malformed in (b"not json", b"\xff", b"[]", b"null"):
            with self.subTest(malformed=malformed):
                github = FakeGitHub()
                github.overrides[worker.LATEST_URL] = malformed
                self.assertFalse(worker.check_for_updates(self.request, github)["ok"])
        self.github.records.append(deepcopy(self.github.records[0]))
        self.github.refresh_assets()
        self.assert_failed(worker.check_for_updates(self.request, self.github), "duplicate tools")
        self.github.records.pop()
        self.github.refresh_assets()
        self.github.release["assets"].pop()
        self.assert_failed(worker.check_for_updates(self.request, self.github), "missing smart_empty")

    def test_invalid_request_rejects_unknown_tool_version_repository_duplicates_before_network(self):
        invalid = []
        for field, value in (("id", "untrusted_tool"), ("version", "2.0.1-beta"),
                             ("repo", "../outside"), ("module", "bl_ext.other.smart_empty")):
            request = deepcopy(self.request)
            request["targets"][0][field] = value
            invalid.append(request)
        invalid += [dict(self.request, blender_version=[5, 2, 3]), dict(self.request, download_dir="relative"),
                    dict(self.request, targets=self.request["targets"] * 2), dict(self.request, targets=[])]
        for request in invalid:
            with self.subTest(request=request):
                github = FakeGitHub()
                self.assertFalse(worker.check_for_updates(request, github)["ok"])
                self.assertEqual(github.calls, [])

    def test_failure_after_one_download_removes_only_created_archive_and_all_part_files(self):
        github = FakeGitHub({"smart_empty": ("2.0.2", make_archive()),
                             "jump_by_time": ("2.0.3", make_archive("jump_by_time", "2.0.3"))})
        github.records[1]["sha256"] = "0" * 64
        github.refresh_assets()
        self.request["targets"] += [self.target("jump_by_time", "2.0.2")]
        self.directory.mkdir()
        sentinel = self.directory / "unrelated.txt"
        sentinel.write_text("preserve me")
        self.assert_failed(worker.check_for_updates(self.request, github), "does not match the release")
        self.assertEqual(list(self.directory.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "preserve me")

    def test_atomic_archive_write_failure_cleans_partial_file(self):
        with patch.object(worker.os, "replace", side_effect=OSError("injected disk failure")):
            self.assert_failed(worker.check_for_updates(self.request, self.github), "Could not prepare")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_trusted_redirects_reject_plain_http_unknown_hosts_credentials_and_bad_ports(self):
        handler = worker.TrustedRedirectHandler()
        request = urllib.request.Request(worker.LATEST_URL)
        for url in ("http://github.com/file", "https://evil.example/file", "file:///tmp/evil",
                    "https://github.com.evil.example/file", "https://user:password@github.com/file",
                    "https://github.com:444/file"):
            with self.subTest(url=url), self.assertRaises(worker.UpdateError):
                handler.redirect_request(request, None, 302, "Found", {}, url)
        redirected = handler.redirect_request(request, None, 302, "Found", {},
                                             "https://release-assets.githubusercontent.com/file?signature=secret")
        self.assertEqual(redirected.host, "release-assets.githubusercontent.com")

    def test_network_failures_are_simple_and_do_not_expose_signed_urls(self):
        secret_url = "https://release-assets.githubusercontent.com/file?signature=secret"
        for error in (urllib.error.HTTPError(secret_url, 403, "forbidden", {}, None),
                      urllib.error.HTTPError(secret_url, 404, "missing", {}, None),
                      urllib.error.URLError(secret_url), TimeoutError(secret_url)):
            with self.subTest(error=type(error).__name__), patch.object(worker.urllib.request, "build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(worker.UpdateError) as captured:
                    worker.fetch_bytes(worker.LATEST_URL, worker.JSON_LIMIT)
                self.assertNotIn("signature", str(captured.exception))
                self.assertNotIn("secret", str(captured.exception))

    def test_cli_reports_invalid_request_atomically_without_network(self):
        request = Path(self.temp.name) / "request.json"
        result = Path(self.temp.name) / "result.json"
        request.write_text("not-json", encoding="utf-8")
        process = subprocess.run([sys.executable, str(WORKER_PATH), "--request", str(request), "--result", str(result)],
                                 check=False, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(result.read_text())["ok"], False)
        self.assertFalse(result.with_suffix(".json.part").exists())


if __name__ == "__main__":
    unittest.main()
