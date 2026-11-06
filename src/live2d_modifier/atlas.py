"""Atlas-level analysis and surgery: texel sharing between drawables, and moving drawables to their own page.

Artists sometimes let two meshes sample the same texels (for example a white credit text packed inside a white dress
region). Recolouring one of them then shows up on the other. find_conflicts() lists such pairs (only texels with
alpha > 0 count); split_to_page() copies the meshes' atlas boxes into a new page and remaps their UVs, so the two
sides can be painted independently.
"""
import json

import cv2
import numpy as np
from PIL import Image

from .model import ModelDir, next_texture_name

Image.MAX_IMAGE_PIXELS = None


def _uv_px(d, TW, TH):
    uv = np.asarray(d["uv"], np.float64).reshape(-1, 2)
    return np.stack([uv[:, 0] * TW, (1 - uv[:, 1]) * TH], 1)


def find_conflicts(model_dir, drawables, include_hidden=False, min_texels=200):
    """Pairs of drawables that sample the same opaque texels. Returns [(a, b, texels)] sorted by size."""
    md = ModelDir(model_dir)
    D = drawables
    pairs = {}
    for k, tf in enumerate(md.textures):
        alpha = np.asarray(Image.open(tf).convert("RGBA"))[..., 3] > 10
        TH, TW = alpha.shape
        masks = []
        for i, d in enumerate(D):
            if d["tex"] != k or (not include_hidden and d["op"] <= 0.01):
                continue
            P = _uv_px(d, TW, TH)
            tri = np.asarray(d["idx"], np.int64).reshape(-1, 3)
            x0, y0 = np.maximum(np.floor(P.min(0)).astype(int), 0)
            x1, y1 = np.minimum(np.ceil(P.max(0)).astype(int) + 1, [TW, TH])
            if x1 <= x0 or y1 <= y0:
                continue
            m = np.zeros((y1 - y0, x1 - x0), np.uint8)
            cv2.fillPoly(m, list(np.rint((P[tri] - [x0, y0]) * 16).astype(np.int32)), 1, shift=4)
            m = m.astype(bool) & alpha[y0:y1, x0:x1]
            for j, (px0, py0, px1, py1), previous in masks:
                ax, ay = max(x0, px0), max(y0, py0)
                bx, by = min(x1, px1), min(y1, py1)
                if bx > ax and by > ay:
                    count = np.count_nonzero(m[ay-y0:by-y0, ax-x0:bx-x0] &
                                             previous[ay-py0:by-py0, ax-px0:bx-px0])
                    if count:
                        pairs[(j, i)] = int(count)
            masks.append((i, (x0, y0, x1, y1), m))
    out = [(a, b, n) for (a, b), n in pairs.items() if n >= min_texels]
    return sorted(out, key=lambda t: -t[2])


def split_to_page(moc, model_dir, meshes, page_size=None, pad=8, log=print):
    """Copy each mesh group's atlas box into one new page (shelf packing) and remap UVs. Returns new page index.
    meshes: list of mesh-index lists; each inner list is packed as one box (keep meshes that share art together)."""
    md = ModelDir(model_dir)
    texs = md.model3["FileReferences"]["Textures"]
    pages = {}
    boxes = []
    ti = moc.table("art_mesh.texture_indices")
    for group in meshes:
        k = int(ti[group[0]])
        assert all(int(ti[m]) == k for m in group), "a group must sit on one page"
        if k not in pages:
            pages[k] = np.asarray(Image.open(md.path / texs[k]).convert("RGBA"))
        TH, TW = pages[k].shape[:2]
        u0, v0, u1, v1 = moc.uv_bbox(group)
        x0, y0 = max(int(u0 * TW) - pad, 0), max(int(v0 * TH) - pad, 0)
        x1, y1 = min(int(np.ceil(u1 * TW)) + pad, TW), min(int(np.ceil(v1 * TH)) + pad, TH)
        boxes.append((group, k, x0, y0, x1, y1))
    S = page_size or max(p.shape[0] for p in pages.values())
    out = np.zeros((S, S, 4), np.uint8)
    cx = cy = shelf = 0
    uv = moc.view("uv.xys")
    for group, k, x0, y0, x1, y1 in sorted(boxes, key=lambda b: -(b[5] - b[3])):
        w, h = x1 - x0, y1 - y0
        if cx + w > S:
            cx, cy, shelf = 0, cy + shelf, 0
        if cy + h > S:
            raise ValueError("boxes do not fit in one page; split into several calls")
        out[cy:cy + h, cx:cx + w] = pages[k][y0:y1, x0:x1]
        TH, TW = pages[k].shape[:2]
        for m in group:
            b, n = moc.mesh_uv_span(m)
            us = uv[b:b + n:2] * TW; vs = uv[b + 1:b + n:2] * TH
            uv[b:b + n:2] = (us - x0 + cx) / S
            uv[b + 1:b + n:2] = (vs - y0 + cy) / S
        cx += w
        shelf = max(shelf, h)
    new_index = len(texs)
    name = next_texture_name(model_dir)
    with open(md.path / name, "xb") as target:
        Image.fromarray(out, "RGBA").save(target, format="PNG", compress_level=3)
    texs.append(name)
    md.model3_path.write_text(json.dumps(md.model3, ensure_ascii=False, indent="\t"))
    tiv = moc.view("art_mesh.texture_indices")
    for group, *_ in boxes:
        for m in group:
            tiv[m] = new_index
    log("split", sum(len(g) for g, *_ in boxes), "meshes to", name, "used height", cy + shelf)
    return new_index
