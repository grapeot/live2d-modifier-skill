"""Downsize model textures safely for both premultiplied and straight-alpha samplers."""
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .delivery import resources
from .model import ModelDir


EDGE_PAD = 16


def pad_transparent_rgb(rgba, radius=EDGE_PAD):
    """Give fully transparent texels near visible ones the nearest visible RGB.

    Straight-alpha bilinear sampling (VTube Studio) mixes the RGB of alpha-0
    neighbours into translucent edges, so black there draws a grey outline.
    Alpha is untouched; texels farther than ``radius`` stay black.
    """
    out = rgba.copy()
    alpha = out[..., 3]
    transparent = (alpha == 0).astype(np.uint8)
    if not transparent.any() or transparent.all():
        return out
    dist, labels = cv2.distanceTransformWithLabels(transparent, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    take = (transparent > 0) & (dist <= radius)
    out[take, :3] = out[alpha > 0, :3][labels[take] - 1]
    return out


def resize_rgba_float(image, size):
    """Area-filter premultiplied float32 RGBA, unpremultiply once, pad edges.

    A box filter cannot overshoot, unlike Lanczos, which leaves a pale rim on
    hard-edged translucent patches. Filtering happens in encoded RGB.
    """
    rgba = np.asarray(image.convert("RGBA")).astype(np.float32) / 255
    alpha = rgba[..., 3:]
    premultiplied = np.dstack([rgba[..., :3] * alpha, alpha])
    shrinking = size[0] < rgba.shape[1] or size[1] < rgba.shape[0]
    filtered = cv2.resize(premultiplied, tuple(size), interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR)
    filtered = np.clip(filtered.reshape(size[1], size[0], 4), 0, 1)
    alpha = filtered[..., 3:]
    straight = np.where(alpha > 1e-6, filtered[..., :3] / np.maximum(alpha, 1e-6), 0)
    result = np.rint(np.clip(np.dstack([straight, alpha]), 0, 1) * 255).astype(np.uint8)
    result[result[..., 3] == 0, :3] = 0
    return Image.fromarray(pad_transparent_rgb(result))


def _sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resize_model(model_dir, dest, max_size=4096):
    """Write a self-contained, referenced-resource copy to a new directory.

    Only oversized PNG textures are resampled. Small pages and all non-texture
    resources stay byte-identical. No Core or browser is needed for this step.
    """
    if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size < 1:
        raise ValueError("max_size 必须是正整数")
    md = ModelDir(Path(model_dir).resolve())
    requested_dest = Path(dest)
    if requested_dest.is_symlink():
        raise FileExistsError(f"目标已存在，请换一个新的输出目录: {requested_dest}")
    source, dest = md.path.resolve(), requested_dest.resolve()
    if source == dest or source.is_relative_to(dest) or dest.is_relative_to(source):
        raise ValueError("源和目标目录不能相同、包含或重叠")
    if dest.exists():
        raise FileExistsError(f"目标已存在，请换一个新的输出目录: {dest}")
    root, files = resources(source)
    reserved_name = "texture_resize_manifest.json"
    if any(path.relative_to(root).parts[0].casefold() == reserved_name for path in files):
        raise ValueError("模型资源与保留清单文件名冲突: texture_resize_manifest.json")
    plan = []
    # Validate every input before creating output; paths are checked by resources().
    for texture in dict.fromkeys(md.textures):
        with Image.open(texture) as image:
            image.verify()
        with Image.open(texture) as image:
            before = image.size
            scale = min(1, max_size / max(before))
            after = tuple(max(1, round(d * scale)) for d in before)
            if before != after and image.format != "PNG":
                raise ValueError(f"需要缩放的贴图必须是 PNG: {texture.name}")
        plan.append((texture.relative_to(root), before, after, _sha(texture)))
    dest.mkdir(parents=True, exist_ok=False)
    for path in sorted(files):
        target = dest / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    rows = []
    for relative, before, after, source_sha in plan:
        target = dest / relative
        if before != after:
            with Image.open(target) as image:
                resized = resize_rgba_float(image, after)
            resized.save(target, format="PNG")
        rows.append({"file": relative.as_posix(), "from": before, "to": after,
                     "source_sha256": source_sha, "sha256": _sha(target)})
    # Paths in this local manifest may be private; package excludes this record.
    manifest = {"source": str(source), "max_size": max_size,
                "method": "float32 premultiplied-alpha area filter (encoded RGB) + transparent RGB edge padding", "textures": rows}
    (dest / "texture_resize_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return dest
