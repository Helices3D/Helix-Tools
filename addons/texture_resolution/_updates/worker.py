# SPDX-License-Identifier: GPL-3.0-or-later
"""Download verified Helix releases outside Blender's main process.

This module deliberately imports no Blender API and never installs or executes
downloaded code. The controller owns installation and the restart notice.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib


PACKAGE_IDS = frozenset((
    "jump_by_time", "smart_empty", "camera_timeline_culler",
    "area_light_shadow_control", "hair_contact_culler", "cloth_cache_manager", "texture_resolution",
    "helix_tools",
))
REPOSITORY = "Helices3D/Helix-Tools"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
DOWNLOAD_ROOT = f"https://github.com/{REPOSITORY}/releases/download"
JSON_LIMIT = 1024 * 1024
ZIP_LIMIT = 32 * 1024 * 1024
EXPANDED_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 2048
TIMEOUT = 15
TRUSTED_HOSTS = frozenset((
    "api.github.com", "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com", "github-releases.githubusercontent.com",
))
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
REPO_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


class UpdateError(ValueError):
    """A concise failure suitable for a Blender report or popup."""


def parse_version(value):
    """Accept stable numeric versions only; compare their integer components."""
    if not isinstance(value, str) or len(value) > 32 or not VERSION_PATTERN.fullmatch(value):
        raise UpdateError("The release contains an invalid stable version.")
    return tuple(int(part) for part in value.split("."))


def _trusted_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        trusted = (parsed.scheme == "https" and parsed.hostname in TRUSTED_HOSTS
                   and parsed.port in (None, 443) and not parsed.username
                   and not parsed.password and not parsed.fragment)
    except (TypeError, ValueError):
        trusted = False
    if not trusted:
        raise UpdateError("GitHub redirected the download to an untrusted location.")
    return url


class TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        _trusted_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch_bytes(url, limit):
    """Fetch bounded HTTPS bytes without authentication, cookies, or URL logging."""
    _trusted_url(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Helix-Tools-Updater",
        "Accept": "application/vnd.github+json" if url == LATEST_URL else "application/octet-stream",
    })
    opener = urllib.request.build_opener(
        TrustedRedirectHandler(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            _trusted_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    declared_size = int(length)
                except (TypeError, ValueError):
                    raise UpdateError("GitHub returned an invalid download size.") from None
                if declared_size < 0 or declared_size > limit:
                    raise UpdateError("The update download exceeds the allowed size.")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise UpdateError("The update download exceeds the allowed size.")
            return data
    except urllib.error.HTTPError as error:
        if error.code in (403, 429):
            raise UpdateError("GitHub refused the update request. Try again later.") from None
        if error.code == 404:
            raise UpdateError("The published update is unavailable on GitHub.") from None
        raise UpdateError(f"GitHub could not complete the update request (HTTP {error.code}).") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError):
        raise UpdateError("Could not reach GitHub. Check your connection and try again.") from None


def _fetch(fetch, url, limit):
    data = fetch(url, limit)
    if not isinstance(data, bytes):
        raise UpdateError("The update download returned invalid data.")
    if len(data) > limit:
        raise UpdateError("The update download exceeds the allowed size.")
    return data


def _json(data):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise UpdateError("GitHub returned invalid update metadata.") from None


def _asset_map(release):
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("GitHub did not provide a published stable release.")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("helix-tools-v"):
        raise UpdateError("GitHub returned an unexpected Helix release tag.")
    parse_version(tag[len("helix-tools-v"):])
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) > 1024:
        raise UpdateError("GitHub returned invalid release assets.")
    by_name = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise UpdateError("GitHub returned invalid release assets.")
        name = asset["name"]
        if name in by_name:
            raise UpdateError("GitHub returned duplicate release assets.")
        by_name[name] = asset
    return tag, by_name


def _check_digest(data, asset, name, expected=None):
    actual = hashlib.sha256(data).hexdigest()
    if expected is not None and actual != expected:
        raise UpdateError(f"The checksum for {name} does not match the release.")
    digest = asset.get("digest")
    if digest is not None:
        if not isinstance(digest, str) or not digest.startswith("sha256:") or not HASH_PATTERN.fullmatch(digest[7:]):
            raise UpdateError(f"GitHub returned an invalid checksum for {name}.")
        if actual != digest[7:]:
            raise UpdateError(f"The checksum for {name} does not match GitHub.")
    return actual


def _release_index(data):
    records = _json(data)
    if not isinstance(records, list) or len(records) > 256:
        raise UpdateError("The release index is invalid.")
    by_id = {}
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise UpdateError("The release index contains an invalid tool.")
        package_id = record["id"]
        if package_id in seen:
            raise UpdateError("The release index contains duplicate tools.")
        seen.add(package_id)
        # A future release may include tools this installed updater does not know.
        if package_id not in PACKAGE_IDS:
            continue
        version = record.get("version")
        parse_version(version)
        if (record.get("file") != f"{package_id}-{version}.zip"
                or not isinstance(record.get("sha256"), str)
                or not HASH_PATTERN.fullmatch(record["sha256"])
                or record.get("kind") != ("suite" if package_id == "helix_tools" else "standalone")):
            raise UpdateError(f"The release index contains invalid metadata for {package_id}.")
        by_id[package_id] = record
    return by_id


def _validate_request(request):
    if not isinstance(request, dict):
        raise UpdateError("The update request is invalid.")
    blender_version = request.get("blender_version")
    if (not isinstance(blender_version, (list, tuple)) or len(blender_version) != 3
            or any(type(part) is not int for part in blender_version)
            or tuple(blender_version) != (5, 2, 2)):
        raise UpdateError("Helix Tools updates require Blender 5.2.2 LTS.")
    targets = request.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 64:
        raise UpdateError("No valid installed Helix Tools were selected for updating.")
    seen = set()
    for target in targets:
        if not isinstance(target, dict) or target.get("id") not in PACKAGE_IDS:
            raise UpdateError("The update request contains an unknown tool.")
        parse_version(target.get("version"))
        repo = target.get("repo")
        module = target.get("module")
        if (not isinstance(repo, str) or len(repo) > 128 or not REPO_PATTERN.fullmatch(repo)
                or module != f"bl_ext.{repo}.{target['id']}"):
            raise UpdateError("The installed extension repository is invalid.")
        if module in seen:
            raise UpdateError("The update request contains the same installed tool twice.")
        seen.add(module)
    download_dir = request.get("download_dir")
    if not isinstance(download_dir, str) or not Path(download_dir).is_absolute():
        raise UpdateError("The update download folder is invalid.")
    return targets, Path(download_dir), tuple(blender_version)


def _safe_member(info):
    name = info.orig_filename
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not path or len(name) > 1024 or "\\" in name or any(ord(character) < 32 or character in ':<>"|?*' for character in name)
            or any(not part or part in (".", "..") or part.endswith((".", " "))
                   or len(part.encode("utf-8")) > 255 or part.split(".")[0].upper() in reserved
                   for part in parts)):
        raise UpdateError("The update archive contains an unsafe file path.")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not info.is_dir()):
        raise UpdateError("The update archive contains a link or special file.")
    if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise UpdateError("The update archive uses unsupported compression or encryption.")
    if info.is_dir() and info.file_size:
        raise UpdateError("The update archive contains an invalid directory.")
    return path.casefold()


def validate_zip(data, package_id, version, blender_version=(5, 2, 2)):
    """Check identity, compatibility, safe paths, bounded expansion, and every CRC."""
    if package_id not in PACKAGE_IDS:
        raise UpdateError("The update archive contains an unknown tool.")
    parse_version(version)
    if not isinstance(data, bytes) or len(data) > ZIP_LIMIT:
        raise UpdateError("The update archive exceeds the allowed size.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > FILE_LIMIT or sum(info.file_size for info in members) > EXPANDED_LIMIT:
                raise UpdateError("The update archive expands beyond the allowed size.")
            seen = set()
            files = set()
            for info in members:
                folded = _safe_member(info)
                if folded in seen:
                    raise UpdateError("The update archive contains duplicate file paths.")
                seen.add(folded)
                if not info.is_dir():
                    files.add(folded)
            for path in seen:
                pieces = path.split("/")
                if any("/".join(pieces[:index]) in files for index in range(1, len(pieces))):
                    raise UpdateError("The update archive contains conflicting file paths.")
            names = {info.filename for info in members if not info.is_dir()}
            if not {"__init__.py", "blender_manifest.toml"} <= names:
                raise UpdateError("The update archive is missing its root extension files.")
            manifest_info = archive.getinfo("blender_manifest.toml")
            if manifest_info.file_size > JSON_LIMIT:
                raise UpdateError("The update archive contains an oversized manifest.")
            manifest = tomllib.loads(archive.read(manifest_info).decode("utf-8"))
            if manifest.get("id") != package_id or manifest.get("version") != version or manifest.get("type") != "add-on":
                raise UpdateError("The update archive does not match the requested tool and version.")
            minimum = parse_version(manifest.get("blender_version_min"))
            maximum = manifest.get("blender_version_max")
            if minimum > tuple(blender_version) or (maximum is not None and parse_version(maximum) <= tuple(blender_version)):
                raise UpdateError("The update is not compatible with Blender 5.2.2 LTS.")
            expanded = 0
            for info in members:
                if info.is_dir():
                    continue
                read_size = 0
                with archive.open(info) as member:
                    while chunk := member.read(1024 * 1024):
                        read_size += len(chunk)
                        expanded += len(chunk)
                        if expanded > EXPANDED_LIMIT or read_size > info.file_size:
                            raise UpdateError("The update archive expands beyond the allowed size.")
                if read_size != info.file_size:
                    raise UpdateError("The update archive contains a damaged file.")
            return manifest
    except (zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeError, tomllib.TOMLDecodeError,
            RuntimeError, EOFError, OSError, zlib.error):
        raise UpdateError("The update archive is damaged or has an invalid manifest.") from None


def _write_archive(directory, filename, data):
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise UpdateError("The update download folder is unsafe.")
    destination = directory / filename
    if destination.exists() or destination.is_symlink():
        raise UpdateError("The update download folder already contains this archive.")
    part = None
    try:
        descriptor, part_name = tempfile.mkstemp(prefix=f".{filename}.", suffix=".part", dir=directory)
        part = Path(part_name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(part, destination)
        return destination
    finally:
        if part is not None:
            part.unlink(missing_ok=True)


def check_for_updates(request, fetch=None):
    """Fetch one release snapshot and return only newer, verified installed tools."""
    created = []
    try:
        targets, directory, blender_version = _validate_request(request)
        fetch = fetch or fetch_bytes
        release = _json(_fetch(fetch, LATEST_URL, JSON_LIMIT))
        tag, assets = _asset_map(release)
        if "releases.json" not in assets:
            raise UpdateError("The published release has no update index.")
        index_data = _fetch(fetch, f"{DOWNLOAD_ROOT}/{tag}/releases.json", JSON_LIMIT)
        _check_digest(index_data, assets["releases.json"], "releases.json")
        index = _release_index(index_data)
        updates, skipped, downloaded = [], [], {}
        for target in targets:
            package_id = target["id"]
            record = index.get(package_id)
            if record is None:
                skipped.append(f"{package_id} is not included in the latest release.")
                continue
            if parse_version(record["version"]) <= parse_version(target["version"]):
                skipped.append(f"{package_id} is already up to date.")
                continue
            filename = record["file"]
            asset = assets.get(filename)
            if asset is None:
                raise UpdateError(f"The published release is missing {filename}.")
            size = asset.get("size")
            if size is not None and (type(size) is not int or size < 0 or size > ZIP_LIMIT):
                raise UpdateError("The update download exceeds the allowed size.")
            if filename not in downloaded:
                data = _fetch(fetch, f"{DOWNLOAD_ROOT}/{tag}/{filename}", ZIP_LIMIT)
                digest = _check_digest(data, asset, filename, record["sha256"])
                validate_zip(data, package_id, record["version"], blender_version)
                path = _write_archive(directory, filename, data)
                created.append(path)
                downloaded[filename] = (str(path), digest)
            path, digest = downloaded[filename]
            updates.append({"id": package_id, "version": record["version"],
                            "repo": target["repo"], "module": target["module"],
                            "file": path, "sha256": digest})
        return {"ok": True, "tag": tag, "updates": updates, "skipped": skipped}
    except UpdateError as error:
        message = str(error)
    except (OSError, ValueError, TypeError, KeyError):
        message = "Could not prepare the update download. Try again."
    for path in created:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": False, "error": message}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.request.open("rb") as source:
            request_bytes = source.read(JSON_LIMIT + 1)
        if len(request_bytes) > JSON_LIMIT:
            raise UpdateError("The update request exceeds the allowed size.")
        result = check_for_updates(_json(request_bytes))
    except (OSError, UpdateError):
        result = {"ok": False, "error": "Could not read the update request."}
    part = args.result.with_suffix(args.result.suffix + ".part")
    try:
        with part.open("x", encoding="utf-8") as output:
            json.dump(result, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(part, args.result)
        return 0
    except OSError:
        return 1
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
