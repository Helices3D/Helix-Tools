# SPDX-License-Identifier: GPL-3.0-or-later
"""Reversible scene image references and explicit texture-payload estimates."""

from array import array
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import bpy

from ._images import create_half_image, supported_image
from ._scan import scan_scene


SCENE_PROPERTY = "helix_texture_resolution"
ORIGINAL_KEY = "_helix_texture_original"
SIGNATURE_KEY = "_helix_texture_signature"


@dataclass
class BatchResult:
    processed: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    changed_refs: int = 0
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryEstimate:
    ram_bytes: int
    vram_bytes: int


def eligible_image(image, threshold):
    """Use the longest edge, inclusively; preserve aspect by halving both axes."""
    return image is not None and max(image.size, default=0) >= threshold


def original_image(image):
    """Recognize managed alternatives without guessing from filenames or names."""
    visited = set()
    while image is not None and image.as_pointer() not in visited:
        visited.add(image.as_pointer())
        original = image.get(ORIGINAL_KEY)
        if not isinstance(original, bpy.types.Image):
            return image
        image = original
    return image


def _image_available(image):
    if image is None:
        return False, "Image datablock is missing"
    if image.source == "FILE" and not image.packed_file and not image.packed_files:
        filename = bpy.path.abspath(image.filepath, library=image.library)
        if not filename or not Path(filename).is_file():
            return False, "Image file is missing; restore it or rerun Setup and Run"
    return supported_image(image)


def _signature(image):
    """Compare live pixels, including edits that do not change is_dirty again.

    A file timestamp cannot detect a reload after earlier stale in-memory data,
    and generated images have no timestamp. Hash one image buffer at a time.
    No pixel arrays are retained between images or while resizing the scratch.
    """
    digest = hashlib.sha256(json.dumps((
        tuple(image.size), image.is_float, image.colorspace_settings.name,
        image.alpha_mode, image.use_half_precision,
    )).encode("utf-8"))
    pixels = array("f", [0.0]) * len(image.pixels)
    image.pixels.foreach_get(pixels)
    digest.update(memoryview(pixels))
    return digest.hexdigest()


def _output_directory(settings):
    if settings.output_directory.strip():
        if settings.output_directory.startswith("//") and not bpy.data.filepath:
            raise ValueError("Save the .blend first or choose an absolute output folder")
        return Path(bpy.path.abspath(settings.output_directory)).expanduser().resolve()
    if bpy.data.filepath:
        return Path(bpy.data.filepath).parent / "helix_texture_variants"
    return Path(bpy.utils.user_resource(
        "DATAFILES", path="helix_tools/texture_variants", create=True,
    ))


def _record_for(settings, original):
    return next((record for record in settings.records if record.original == original), None)


def _allowed_slots(slots, settings, result):
    allowed = []
    reasons = set()
    for slot in slots:
        if not slot.editable:
            reasons.add("linked or read-only references")
        elif slot.shared and not settings.include_shared:
            reasons.add("references shared with other scenes (Include Shared Resources is off)")
        else:
            allowed.append(slot)
    if reasons:
        result.details.append("Left unchanged: " + "; ".join(sorted(reasons)))
    return allowed


def _replace(slots, target, result, expected_source):
    changed = 0
    errors = []
    for slot in slots:
        try:
            # A modal setup yields between images. Respect a manual rewire
            # made after its initial scan instead of overwriting the new link.
            if original_image(slot.image) != expected_source:
                continue
            if slot.image != target:
                slot.set(target)
                changed += 1
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as error:
            errors.append(f"{slot.label}: {error}")
    result.changed_refs += changed
    result.details.extend(errors)
    return changed, errors


def _finish(settings, result, verb, *, scene, slots):
    settings.last_report = (
        f"{verb}: {result.processed} images; {result.unchanged} unchanged; "
        f"{result.skipped} skipped; {result.failed} failed"
    )
    try:
        refresh_statistics(scene, slots=slots)
    except Exception as error:
        # Texture changes already succeeded. An optional display calculation
        # must not turn that operation into a misleading failure or roll it back.
        try:
            settings.statistics_json = ""
        except Exception:
            pass
        result.details.append(f"Statistics unavailable: {error}; use Refresh Statistics to retry")
    settings.details = "\n".join(dict.fromkeys(result.details))
    return result


def iter_setup(context):
    """Prepare and apply one image per iteration, retaining completed work.

    Both image pointers are stored before reference changes, so a failed slot
    can be retried and partially applied changes remain reversible. Canceling
    between iterations keeps the completed alternatives and their tracking.
    """
    scene = context.scene
    settings = getattr(scene, SCENE_PROPERTY)
    result = BatchResult()
    scan = scan_scene(scene)
    grouped = {}
    for slot in scan.slots:
        source = original_image(slot.image)
        grouped.setdefault(source, []).append(slot)
    candidates = [
        (source, slots) for source, slots in grouped.items()
        if eligible_image(source, settings.threshold) or not all(source.size)
    ]
    total = len(candidates)
    try:
        for index, (source, slots) in enumerate(candidates, 1):
            try:
                available, reason = _image_available(source)
                if not available:
                    result.skipped += 1
                    result.details.append(f"{source.name}: {reason}")
                else:
                    allowed = _allowed_slots(
                        [slot for slot in slots if original_image(slot.image) == source],
                        settings, result,
                    )
                    if not allowed:
                        result.skipped += 1
                    else:
                        signature = _signature(source)
                        record = _record_for(settings, source)
                        previous = record.alternative if record else None
                        alternative = previous
                        valid = (
                            record is not None and record.source_signature == signature
                            and _image_available(alternative)[0]
                            and tuple(alternative.size) == tuple(max(1, side // 2) for side in source.size)
                        )
                        if not valid:
                            # Another scene may already track this exact snapshot.
                            alternative = next((slot.image for slot in allowed
                                if slot.image != source
                                and slot.image.get(SIGNATURE_KEY) == signature
                                and _image_available(slot.image)[0]
                                and tuple(slot.image.size) == tuple(max(1, side // 2) for side in source.size)), None)
                            if alternative is None:
                                alternative = create_half_image(source, _output_directory(settings))
                            alternative[ORIGINAL_KEY] = source
                            alternative[SIGNATURE_KEY] = signature
                            if previous is not None and previous != alternative and previous.is_dirty:
                                # A user may have painted the smaller image. Keep
                                # those unsaved edits even after the pair points
                                # at a refreshed alternative and all slots move.
                                previous.use_fake_user = True
                                result.details.append(
                                    f"Preserved edited alternative: {previous.name}; save or pack its image edits"
                                )
                            if record is None:
                                record = settings.records.add()
                            record.original = source
                            record.alternative = alternative
                            record.source_signature = signature
                        record.original_width, record.original_height = source.size
                        record.channels = max(1, source.channels)
                        record.is_float = source.is_float
                        record.half_precision = source.use_half_precision
                        changed, errors = _replace(allowed, alternative, result, source)
                        if errors:
                            result.failed += 1
                        elif changed or not valid:
                            result.processed += 1
                        else:
                            result.unchanged += 1
                        # Only remove our unreferenced old datablock. Its file is
                        # retained; never delete potentially user-edited assets.
                        if (previous is not None and previous != alternative
                                and not previous.is_dirty and previous.users == 0):
                            bpy.data.images.remove(previous)
            except (OSError, AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as error:
                result.failed += 1
                result.details.append(f"{source.name}: {error}")
            yield index, total, source.name
    except GeneratorExit:
        _finish(settings, result, "Setup stopped", scene=scene, slots=scan.slots)
        raise
    if not candidates:
        result.details.append(f"No scene images reach {settings.threshold} px on either side")
    return _finish(settings, result, "Setup", scene=scene, slots=scan.slots)


def setup_scene(context):
    iterator = iter_setup(context)
    while True:
        try:
            next(iterator)
        except StopIteration as finished:
            return finished.value


def switch_scene(context, mode):
    """Switch tracked live references; leave unrelated manual rewires alone."""
    if mode not in {"ORIGINAL", "HALF"}:
        raise ValueError("Expected ORIGINAL or HALF resolution")
    scene = context.scene
    settings = getattr(scene, SCENE_PROPERTY)
    result = BatchResult()
    grouped = {}
    live_slots = scan_scene(scene).slots
    for slot in live_slots:
        grouped.setdefault(original_image(slot.image), []).append(slot)
    for record in settings.records:
        source = record.original
        if source is None and any(slot.image == record.alternative for slot in live_slots):
            result.failed += 1
            result.details.append("Original image was deleted; the current alternative was left unchanged")
            continue
        slots = grouped.get(source, ()) if source is not None else ()
        if not slots:
            result.unchanged += 1
            continue
        allowed = _allowed_slots(slots, settings, result)
        if not allowed:
            result.skipped += 1
            continue
        target = source if mode == "ORIGINAL" else record.alternative
        available, reason = _image_available(target)
        if not available:
            result.failed += 1
            result.details.append(f"{source.name}: {reason}")
            continue
        if mode == "HALF" and tuple(target.size) != tuple(max(1, side // 2) for side in source.size):
            result.failed += 1
            result.details.append(f"{source.name}: source dimensions changed; rerun Setup and Run")
            continue
        changed, errors = _replace(allowed, target, result, source)
        if errors:
            result.failed += 1
        elif changed:
            result.processed += 1
        else:
            result.unchanged += 1
    return _finish(settings, result, "Originals" if mode == "ORIGINAL" else "Half resolution",
                   scene=scene, slots=live_slots)


def _estimate_dimensions(width, height, channels, floating, half_precision, include_mipmaps=True):
    ram = width * height * channels * (4 if floating else 1)
    gpu_component_size = (2 if half_precision else 4) if floating else 1
    texels = width * height
    if include_mipmaps and width and height:
        mip_width, mip_height = width, height
        while mip_width > 1 or mip_height > 1:
            mip_width, mip_height = max(1, mip_width // 2), max(1, mip_height // 2)
            texels += mip_width * mip_height
    return MemoryEstimate(ram, texels * 4 * gpu_component_size)


def estimate_image_memory(image, width=None, height=None, include_mipmaps=True):
    """Estimate decoded pixels and uncompressed GPU texture payload, not RSS.

    GPU textures are estimated as RGBA8, RGBA16F or RGBA32F. Include the exact
    complete mip chain when requested. Driver layouts, compression, Cycles,
    retained buffers and all non-image allocations can change actual usage.
    """
    actual_width, actual_height = image.size
    width = actual_width if width is None else max(0, int(width))
    height = actual_height if height is None else max(0, int(height))
    channels = max(1, image.channels)
    return _estimate_dimensions(width, height, channels, image.is_float,
                                image.use_half_precision, include_mipmaps)


_MEMORY_KEYS = (
    "original_ram", "half_ram", "current_ram",
    "original_vram", "half_vram", "current_vram",
)


def _empty_statistics():
    return dict.fromkeys(_MEMORY_KEYS, 0) | {"count": 0, "current_mode": "NONE"}


def memory_estimates(scene, *, slots=None):
    """Compute a fresh snapshot during an explicit action, never during draw.

    Callers that already scanned references reuse those slots. Read-only
    statistics do not need the other-scene scans used to protect shared writes.
    Canonicalize each live image once instead of once per tracked pair.
    """
    settings = getattr(scene, SCENE_PROPERTY)
    totals = _empty_statistics()
    if not settings.records:
        return totals
    if slots is None:
        slots = scan_scene(scene, check_shared=False).slots
    live_images = set()
    for slot in slots:
        try:
            image = slot.image
            if image is not None:
                live_images.add(image)
        except (AttributeError, ReferenceError, RuntimeError):
            # A reference may have been deleted while modal setup yielded.
            continue
    by_original = {}
    for image in live_images:
        by_original.setdefault(original_image(image), set()).add(image)
    modes = set()
    counted_images = set()
    count = 0
    for record in settings.records:
        source = record.original
        relevant = by_original.get(source, ())
        if source is None or not relevant:
            continue
        count += 1
        # Reading Image.size can reload an otherwise freed full-resolution
        # buffer. A sidebar redraw must not load all originals just to display
        # their saved dimensions and memory estimates.
        width, height = record.original_width, record.original_height
        half_width, half_height = max(1, width // 2), max(1, height // 2)
        full = _estimate_dimensions(width, height, record.channels, record.is_float, record.half_precision)
        half = _estimate_dimensions(half_width, half_height, record.channels, record.is_float, record.half_precision)
        totals["original_ram"] += full.ram_bytes
        totals["original_vram"] += full.vram_bytes
        totals["half_ram"] += half.ram_bytes
        totals["half_vram"] += half.vram_bytes
        for image in relevant:
            if image is not None:
                modes.add("ORIGINAL" if image == source else "HALF")
                if image not in counted_images:
                    estimate = full if image == source else half
                    totals["current_ram"] += estimate.ram_bytes
                    totals["current_vram"] += estimate.vram_bytes
                    counted_images.add(image)
    totals["count"] = count
    totals["current_mode"] = next(iter(modes)) if len(modes) == 1 else "MIXED" if modes else "NONE"
    return totals


def refresh_statistics(scene, *, slots=None):
    """Save a small display snapshot without retaining Blender references.

    Keeping this alongside scene settings lets Blender restore it on undo and
    file load, and keeps standalone/suite migration independent of runtime
    caches or per-frame handlers. Manual material edits need an explicit refresh.
    """
    totals = memory_estimates(scene, slots=slots)
    getattr(scene, SCENE_PROPERTY).statistics_json = json.dumps(totals, separators=(",", ":"))
    return totals


def statistics_snapshot(scene):
    """Read constant-size display data; never inspect scene references/pixels."""
    value = getattr(scene, SCENE_PROPERTY).statistics_json
    if value and len(value) <= 4096:
        try:
            totals = json.loads(value)
            if (isinstance(totals, dict)
                    and all(type(totals.get(key)) is int and 0 <= totals[key] < 2 ** 64
                            for key in (*_MEMORY_KEYS, "count"))
                    and totals.get("current_mode") in {"NONE", "ORIGINAL", "HALF", "MIXED"}):
                return {key: totals[key] for key in (*_MEMORY_KEYS, "count", "current_mode")} | {"available": True}
        except (RecursionError, TypeError, ValueError):
            pass
    return _empty_statistics() | {"available": False}
