"""Deterministic texel edits on a finished model: recolour by hue, boost saturation, smooth away baked detail.

Recipe (JSON): {"ops": [op, ...]}, applied in order. Every op selects texels through drawables
("meshes" / "parts" / "patterns", as in variant recipes): the texels inside those drawables' UV triangles.
  {"op": "recolor", "meshes": [...], "hue": [lo, hi], "soft": 12, "min_chroma": 6,
   "to": {"L": 80, "C": 25, "h": 80}, "contrast": 1.0, "L_range": [lo, hi]}
      texels whose LCh hue is in [lo, hi] (degrees, may wrap, soft edges) and chroma above min_chroma move to the
      target colour; shading survives because L and C keep their offsets/ratios to the selection's median.
  {"op": "saturate", "parts": [...], "scale": 1.4, "min_chroma": 8, "full_chroma": 18, "L_shift": -2}
      chroma x scale (and L shift) weighted by smoothstep(min_chroma, full_chroma, C): near-neutral texels (white
      hair, line art) stay as they are, coloured ones get stronger.
  {"op": "smooth", "meshes": [...], "sigma": 16}
      normalised Gaussian inside each drawable's own texel set; texels within ~3 sigma of the island edge fade back
      to the original so contour lines survive. For baked-in high-frequency detail (stocking gloss, a sock line).
  {"op": "match", "meshes": [...], "ref": [...]}
      shift the selection's mean Lab to the mean of the ref drawables' texels.
Pages that an op touches are rewritten; the others stay symlinks to the source.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .model import ModelDir
from .moc import Moc
from .variant import clone_model

Image.MAX_IMAGE_PIXELS = None


def _lab(rgb):
    return cv2.cvtColor((rgb / 255).astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3)


def _rgb(L):
    return np.clip(cv2.cvtColor(L.astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_LAB2RGB).reshape(-1, 3) * 255, 0, 255)


def _lch(L):
    C = np.hypot(L[:, 1], L[:, 2])
    h = np.degrees(np.arctan2(L[:, 2], L[:, 1])) % 360
    return C, h


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def _hue_weight(h, lo, hi, soft):
    """1 inside [lo, hi] (wrapping), falling to 0 over `soft` degrees outside."""
    width = (hi - lo) % 360
    d = (h - lo) % 360
    outside = np.minimum(np.where(d <= width, 0, d - width), np.where(d <= width, 0, 360 - d))
    return 1 - _smoothstep(0, max(soft, 1e-6), outside)


def texel_sets(drawables, mesh_idx, page_sizes):
    """{page: (ys, xs, island_id)} for the union of the drawables' UV triangles (island_id = drawable index)."""
    out = {}
    for i in mesh_idx:
        d = drawables[i]
        k = d["tex"]
        TW, TH = page_sizes[k]
        uv = np.asarray(d["uv"], np.float64).reshape(-1, 2)
        T = np.stack([uv[:, 0] * TW, (1 - uv[:, 1]) * TH], 1)
        tri = np.asarray(d["idx"], np.int64).reshape(-1, 3)
        x0, y0 = np.maximum(np.floor(T.min(0)).astype(int) - 1, 0)
        x1, y1 = np.minimum(np.ceil(T.max(0)).astype(int) + 2, [TW, TH])
        if x1 <= x0 or y1 <= y0:
            continue
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, list(np.rint((T[tri] - [x0, y0]) * 16).astype(np.int32)), 1, shift=4)
        ys, xs = np.nonzero(m)
        out.setdefault(k, []).append((ys + y0, xs + x0, np.full(len(ys), i)))
    res = {}
    for k, parts in out.items():
        ys = np.concatenate([p[0] for p in parts]); xs = np.concatenate([p[1] for p in parts])
        ids = np.concatenate([p[2] for p in parts])
        key = ys.astype(np.int64) * page_sizes[k][0] + xs
        _, first = np.unique(key, return_index=True)   # shared texels counted once
        res[k] = (ys[first], xs[first], ids[first])
    return res


def _smooth_island(page, ys, xs, sigma):
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    sub = page[y0:y1, x0:x1, :3].astype(np.float32)
    m = np.zeros(sub.shape[:2], np.float32)
    m[ys - y0, xs - x0] = 1
    s = 4 if max(sub.shape[:2]) > 1500 else 1
    small = (lambda a: cv2.resize(a, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA)) if s > 1 else (lambda a: a)
    big = (lambda a: cv2.resize(a, (sub.shape[1], sub.shape[0]), interpolation=cv2.INTER_LINEAR)) if s > 1 else (lambda a: a)
    ms, cs = small(m), small(sub)
    num = cv2.GaussianBlur(cs * ms[..., None], (0, 0), sigma / s)
    den = cv2.GaussianBlur(ms, (0, 0), sigma / s)
    blur = big(num / np.maximum(den, 1e-4)[..., None])
    dist = cv2.distanceTransform((m > 0).astype(np.uint8), cv2.DIST_L2, 5)
    w = np.clip(dist / (3 * sigma), 0, 1)[..., None]
    return (sub * (1 - w) + blur * w)[ys - y0, xs - x0]


def apply_ops(model_dir, dst, recipe, drawables, log=print):
    model_dir, dst = Path(model_dir), Path(dst)
    clone_model(model_dir, dst, link_textures=True)
    md = ModelDir(dst)
    moc = Moc(md.moc_path)
    pages, dirty = {}, set()
    sizes = []
    for t in md.textures:
        with Image.open(t) as im:
            sizes.append(im.size)

    def page(k):
        if k not in pages:
            pages[k] = np.asarray(Image.open(md.textures[k]).convert("RGBA")).copy()
        return pages[k]

    def select(op, key=None):
        spec = op if key is None else {"meshes": op[key]}
        idx = moc.select_meshes(spec.get("meshes", ()), spec.get("parts", ()), spec.get("patterns", ()))
        return texel_sets(drawables, idx, sizes)

    for op in recipe["ops"]:
        kind = op["op"]
        sets = select(op)
        if kind == "match":
            ref = select(op, "ref")
            mean = lambda S: np.concatenate([_lab(page(k)[ys, xs, :3].astype(np.float32)) for k, (ys, xs, _) in S.items()]).mean(0)
            delta = mean(ref) - mean(sets)
        n = 0
        for k, (ys, xs, ids) in sets.items():
            pg = page(k)
            if kind == "smooth":
                for i in np.unique(ids):
                    m = ids == i
                    pg[ys[m], xs[m], :3] = np.rint(_smooth_island(pg, ys[m], xs[m], float(op["sigma"]))).astype(np.uint8)
                n += len(ys); dirty.add(k)
                continue
            vis = pg[ys, xs, 3] > 0
            yv, xv = ys[vis], xs[vis]
            L = _lab(pg[yv, xv, :3].astype(np.float32))
            C, h = _lch(L)
            if kind == "recolor":
                w = _hue_weight(h, op["hue"][0], op["hue"][1], op.get("soft", 12))
                w *= _smoothstep(op.get("min_chroma", 6), op.get("min_chroma", 6) + 6, C)
                if "L_range" in op:   # e.g. keep pale hair highlights of the same hue out of a ribbon recolour
                    lo, hi = op["L_range"]
                    w *= _smoothstep(lo - 4, lo, L[:, 0]) * (1 - _smoothstep(hi, hi + 4, L[:, 0]))
                sel = w > 0.5
                if not sel.any():
                    continue
                refL, refC = np.median(L[sel, 0]), max(np.median(C[sel]), 1e-3)
                to = op["to"]
                nL = to["L"] + (L[:, 0] - refL) * op.get("contrast", 1.0)
                nC = np.clip(to["C"] * C / refC, 0, to["C"] * 2)
                hr = np.radians(to["h"])
                new = np.stack([np.clip(nL, 0, 100), nC * np.cos(hr), nC * np.sin(hr)], 1)
                L = L * (1 - w[:, None]) + new * w[:, None]
                n += int(sel.sum())
            elif kind == "saturate":
                w = _smoothstep(op.get("min_chroma", 8), op.get("full_chroma", 18), C)
                if "exclude_hue" in op:
                    w *= 1 - _hue_weight(h, op["exclude_hue"][0], op["exclude_hue"][1], op.get("soft", 12))
                s = 1 + (op.get("scale", 1.3) - 1) * w
                L = np.stack([np.clip(L[:, 0] + op.get("L_shift", 0) * w, 0, 100), L[:, 1] * s, L[:, 2] * s], 1)
                n += int((w > 0.5).sum())
            elif kind == "match":
                L = L + delta
                n += len(L)
            else:
                raise ValueError(f"unknown op {kind}")
            pg[yv, xv, :3] = np.rint(_rgb(L)).astype(np.uint8)
            dirty.add(k)
        log(kind, n, "texels")
    for k in sorted(dirty):
        t = md.textures[k]
        if t.is_symlink():
            t.unlink()
        Image.fromarray(pages[k], "RGBA").save(t, compress_level=3)
    (dst / "texel_ops.json").write_text(json.dumps(recipe, ensure_ascii=False, indent=1))
    return dst
