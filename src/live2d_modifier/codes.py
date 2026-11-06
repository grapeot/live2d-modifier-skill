"""Coordinate-coded textures: every texel's RGB encodes (page, x, y) so a nearest-filtered render tells, per pixel,
which texel is visible. Two passes keep 8-bit channels robust:
  hi: R = x >> 5, G = y >> 5, B = 28 * k + 14      lo: R = (x & 31) * 8 + 4, G = (y & 31) * 8 + 4, B = 28 * k + 14
Alpha is copied from the source page so masks and coverage behave exactly like the real model.
Pages up to 8192 px and up to 9 pages are supported by this encoding.
"""
import hashlib
import os
from pathlib import Path

import numpy as np
from PIL import Image

from .model import ModelDir
from .paths import WORK
from .variant import clone_model

Image.MAX_IMAGE_PIXELS = None
MAX_PAGES = 9
ENCODING = 2   # bump when the encoding changes (part of the cache key)


def encode_page(alpha: np.ndarray, k: int, mode: str) -> np.ndarray:
    H, W = alpha.shape
    if mode in ("white", "whitebin"):   # flat white with the page's alpha (binarised at 0.5 for "whitebin")
        out = np.full((H, W, 4), 255, np.uint8)
        out[..., 3] = alpha if mode == "white" else np.where(alpha >= 128, 255, 0).astype(np.uint8)
        return out
    ys, xs = np.mgrid[0:H, 0:W]
    out = np.empty((H, W, 4), np.uint8)
    if mode == "hi":
        out[..., 0] = xs >> 5
        out[..., 1] = ys >> 5
    else:
        out[..., 0] = (xs & 31) * 8 + 4
        out[..., 1] = (ys & 31) * 8 + 4
    out[..., 2] = 28 * k + 14
    out[..., 3] = alpha
    return out


def decode(hi: np.ndarray, lo: np.ndarray):
    """hi/lo: (..., 4) int arrays of the two coded renders -> k, x, y, consistent mask (before alpha checks)."""
    hi = hi.astype(np.int32); lo = lo.astype(np.int32)
    k_hi = np.rint((hi[..., 2] - 14) / 28).astype(int)
    k_lo = np.rint((lo[..., 2] - 14) / 28).astype(int)
    x = hi[..., 0] * 32 + np.clip(np.rint((lo[..., 0] - 4) / 8), 0, 31).astype(int)
    y = hi[..., 1] * 32 + np.clip(np.rint((lo[..., 1] - 4) / 8), 0, 31).astype(int)
    ok = (k_hi == k_lo) & (k_hi >= 0) & (k_hi < MAX_PAGES)
    return k_hi, x, y, ok


def _page_key(path: Path, k: int) -> str:
    st = Path(path).resolve().stat()
    return hashlib.sha1(f"{Path(path).resolve()}|{st.st_size}|{st.st_mtime_ns}|{k}|{ENCODING}".encode()).hexdigest()[:16]


def code_page_paths(tex_path, k, cache=None, modes=("hi", "lo")):
    cache = Path(cache or WORK / "codes")
    key = _page_key(tex_path, k)
    out = {m: cache / f"{key}_{m}.png" for m in modes}
    if not all(p.exists() for p in out.values()):
        cache.mkdir(parents=True, exist_ok=True)
        alpha = np.asarray(Image.open(tex_path).convert("RGBA"))[..., 3]
        for m, p in out.items():
            tmp = p.with_suffix(".tmp.png")
            Image.fromarray(encode_page(alpha, k, m), "RGBA").save(tmp, compress_level=1)
            os.replace(tmp, p)
    return out


def build_code_models(model_dir, dst_root=None, log=print):
    """Return {'hi': dir, 'lo': dir}: clones of model_dir whose pages are the coded pages (cached by page content)."""
    md = ModelDir(model_dir)
    if len(md.textures) > MAX_PAGES:
        raise ValueError(f"code encoding supports {MAX_PAGES} pages, model has {len(md.textures)}")
    if dst_root is None:
        import tempfile
        cache_root = WORK / "code_models"
        cache_root.mkdir(parents=True, exist_ok=True)
        dst_root = Path(tempfile.mkdtemp(prefix="capture_", dir=cache_root))
    else:
        dst_root = Path(dst_root)
    dirs = {}
    pages = [code_page_paths(t, k) for k, t in enumerate(md.textures)]
    for m in ("hi", "lo"):
        d = dst_root / m
        clone_model(md.path, d, link_textures=True)
        for k, t in enumerate(md.model3["FileReferences"]["Textures"]):
            p = d / t
            if p.exists() or p.is_symlink():
                p.unlink()
            os.symlink(pages[k][m], p)
        dirs[m] = d
    log("code models", dirs)
    return dirs
