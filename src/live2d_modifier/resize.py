"""Downsize model textures without an 8-bit premultiply/unmultiply round trip."""
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .delivery import resources
from .model import ModelDir


def resize_rgba_float(image, size):
    """Filter alpha and premultiplied RGB as float32, then unpremultiply once.

    Lanczos can overshoot; divide by the un-clipped filtered alpha, then clamp
    the final straight channels. Filtering happens in encoded RGB, as in Pillow.
    """
    rgba = np.asarray(image.convert("RGBA"))
    alpha = rgba[..., 3].astype(np.float32) / 255
    filtered_alpha = np.asarray(Image.fromarray(alpha).resize(size, Image.Resampling.LANCZOS))
    result = np.empty((size[1], size[0], 4), np.uint8)
    for c in range(3):
        channel = rgba[..., c].astype(np.float32) / 255 * alpha
        filtered = np.asarray(Image.fromarray(channel).resize(size, Image.Resampling.LANCZOS))
        straight = np.divide(filtered, filtered_alpha, out=np.zeros_like(filtered), where=filtered_alpha > 1e-6)
        result[..., c] = np.rint(np.clip(straight, 0, 1) * 255).astype(np.uint8)
    result[..., 3] = np.rint(np.clip(filtered_alpha, 0, 1) * 255).astype(np.uint8)
    result[result[..., 3] == 0, :3] = 0
    return Image.fromarray(result)


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
    source, dest = md.path.resolve(), Path(dest).resolve()
    if source == dest or source.is_relative_to(dest) or dest.is_relative_to(source):
        raise ValueError("源和目标目录不能相同、包含或重叠")
    if dest.exists():
        raise FileExistsError(f"目标已存在，请换一个新的输出目录: {dest}")
    root, files = resources(source)
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
                "method": "float32 premultiplied-alpha Lanczos (encoded RGB)", "textures": rows}
    (dest / "texture_resize_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return dest
