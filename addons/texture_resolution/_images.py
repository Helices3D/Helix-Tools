# SPDX-License-Identifier: GPL-3.0-or-later
"""Create independent half-resolution files without altering their source images."""

from array import array
from pathlib import Path
import re
import uuid

import bpy


def supported_image(image):
    """Return whether one still-image pixel buffer can be safely reduced.

    Animated, tiled and render-result images contain more than one independently
    meaningful buffer. Processing only their active buffer would silently lose
    frames, tiles, views or render passes, so leave them alone.
    """
    if image is None:
        return False, "Missing image"
    if image.source not in {"FILE", "GENERATED"}:
        return False, {
            "TILED": "UDIM images are not supported",
            "SEQUENCE": "Image sequences are not supported",
            "MOVIE": "Movies are not supported",
            "VIEWER": "Render and viewer results are not supported",
        }.get(image.source, "Only still image files and generated images are supported")
    if image.type not in {"IMAGE", "UV_TEST"}:
        return False, "Multilayer, render and viewer images are not supported"
    if getattr(image, "is_multiview", False) or getattr(image, "use_multiview", False):
        return False, "Multiview images are not supported"
    try:
        width, height = image.size
        if width <= 0 or height <= 0 or not image.has_data:
            return False, "Image pixels could not be loaded; check the source file"
        if len(image.pixels) != width * height * 4:
            return False, "Image does not expose a complete RGBA pixel buffer"
    except (ReferenceError, RuntimeError, TypeError) as error:
        return False, f"Image pixels could not be read: {error}"
    return True, ""


def _output_path(directory, source_name, suffix):
    """Reserve a new file so cleanup and saves can only touch our own output."""
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_name).strip("._")[:80] or "image"
    for _attempt in range(10):
        # The fixed prefix also keeps names such as CON or AUX from becoming
        # Windows device filenames, even when an Image datablock uses one.
        path = directory / f"helix-{stem}.half-{uuid.uuid4().hex}.{suffix}"
        try:
            with path.open("xb"):
                pass
            return path
        except FileExistsError:
            continue
    raise OSError("Could not reserve a unique texture file")


def create_half_image(image, output_directory):
    """Save and load a new half-resolution image; keep all source state intact.

    Blender's Image.copy() reloads the file/generated definition instead of
    copying unsaved pixel edits. Copy the live pixel buffer explicitly so paint,
    packed images and generated textures all use the pixels the user sees.
    """
    supported, reason = supported_image(image)
    if not supported:
        raise ValueError(reason)

    width, height = image.size
    half_size = max(1, width // 2), max(1, height // 2)
    floating = image.is_float
    source_color_space = image.colorspace_settings.name
    source_is_data = image.colorspace_settings.is_data
    source_alpha = image.alpha_mode
    # Float buffers are already scene-linear and premultiplied; byte buffers
    # expose the file's encoded RGB with straight alpha. Preserve that meaning
    # in the saved representation rather than reapplying the source conversion.
    if source_alpha in {"CHANNEL_PACKED", "NONE"}:
        output_alpha = source_alpha
    else:
        output_alpha = "PREMUL" if floating else "STRAIGHT"

    scratch = None
    variant = None
    path = None
    complete = False
    try:
        scratch = bpy.data.images.new(
            f"{image.name} · Preparing Half",
            width=width,
            height=height,
            alpha=True,
            float_buffer=floating,
        )
        linear_color_space = scratch.colorspace_settings.name
        # Setting color space after editing generated pixels resets their buffer.
        # Non-Color also prevents Image.save() applying a second color transform.
        scratch.colorspace_settings.name = "Non-Color"
        scratch.alpha_mode = output_alpha
        pixels = array("f", [0.0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        scratch.pixels.foreach_set(pixels)
        del pixels
        scratch.scale(*half_size)

        # Native Image.save() writes float EXR channels at 32-bit precision.
        # PNG keeps the original 8-bit representation for byte-backed textures.
        scratch.file_format = "OPEN_EXR" if floating else "PNG"
        path = _output_path(output_directory, image.name, "exr" if floating else "png")
        scratch.filepath_raw = str(path)
        scratch.save()
        if path.stat().st_size == 0:
            raise RuntimeError("Blender did not write the half-resolution image")

        variant = bpy.data.images.load(str(path), check_existing=False)
        variant.colorspace_settings.name = (
            "Non-Color" if source_is_data else linear_color_space
        ) if floating else source_color_space
        variant.alpha_mode = output_alpha
        variant.use_half_precision = image.use_half_precision
        variant.use_view_as_render = image.use_view_as_render
        if tuple(variant.size) != half_size or not variant.has_data:
            raise RuntimeError("Blender could not reload the half-resolution image")
        if variant.is_float != floating:
            raise RuntimeError("The half-resolution image lost its original pixel precision")
        variant.name = f"{image.name} · Half"
        complete = True
        return variant
    finally:
        if scratch is not None:
            bpy.data.images.remove(scratch)
        if not complete:
            if variant is not None:
                bpy.data.images.remove(variant)
            if path is not None:
                path.unlink(missing_ok=True)
