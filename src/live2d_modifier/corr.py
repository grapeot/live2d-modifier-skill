"""Pixel <-> texel correspondence for one framing and pose.

capture() writes, for a prefix P:
  P.png               normal render (RGBA, transparent background)
  P_code_hi.png/_lo   coded renders (nearest filtering), same framing
  P_drawables.json    raw drawable data at that pose (ids, uv, triangles, screen-independent positions, opacity, ...)
  P_meta.json         model path, framing, params
correspond() turns those into P_corr.npz: for every opaque pixel the page k, texel (x, y), owning drawable and whether
the pixel is a single opaque layer (render colour == texel colour), which is what back-projection can trust.
"""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .codes import build_code_models, decode
from .model import ModelDir
from .paths import WORK

Image.MAX_IMAGE_PIXELS = None
CONSISTENT_DIFF = 28


def capture(renderer, model_dir, prefix, w, h, params=None, zoom=None, sim=None, fit=None, log=print):
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    renderer.load(model_dir, w, h, zoom=zoom, fit=fit)
    renderer.render(params, sim).save(f"{prefix}.png")
    Path(f"{prefix}_drawables.json").write_text(json.dumps(renderer.drawables()))
    codes = build_code_models(model_dir, log=log)
    for m, d in codes.items():
        renderer.load(d, w, h, zoom=zoom, nearest=True, fit=fit)
        renderer.render(params, sim).save(f"{prefix}_code_{m}.png")
    meta = {"model": str(Path(model_dir).resolve()), "w": w, "h": h, "zoom": zoom, "fit": fit, "params": params or {}, "sim": sim}
    Path(f"{prefix}_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return prefix


def owner_maps(model_dir, drawables, cache_root=None, log=print):
    """Per page int16 label image: which drawable owns each texel. Visible drawables (opacity > 0.01) win shared
    texels over hidden ones; among equals, later render order wins."""
    md = ModelDir(model_dir)
    D = drawables
    h = hashlib.sha1(b"owner-map-v2")
    sizes = []
    for texture in md.textures:
        with Image.open(texture) as image:
            sizes.append(image.size)
    h.update(json.dumps(sizes).encode())
    for d in D:
        h.update(np.asarray(d["uv"], np.float64).tobytes())
        h.update(np.asarray(d["idx"], np.int64).tobytes())
        h.update(json.dumps([d["tex"], d["op"] > 0.01, d["order"]]).encode())
    key = h.hexdigest()[:16]
    cache = Path(cache_root or WORK / "owners") / key
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    order = sorted(range(len(D)), key=lambda i: (D[i]["op"] > 0.01, D[i]["order"]))
    for k, tf in enumerate(md.textures):
        f = cache / f"owner_{k}.npy"
        if f.exists():
            cached = np.load(f, mmap_mode="r")
            if cached.shape == (sizes[k][1], sizes[k][0]):
                out.append(cached)
                continue
        with Image.open(tf) as im0:
            TW, TH = im0.size
        lab = np.full((TH, TW), -1, np.int32)
        for i in order:
            d = D[i]
            if d["tex"] != k:
                continue
            uv = np.asarray(d["uv"], np.float64).reshape(-1, 2)
            px = np.stack([uv[:, 0] * TW, (1 - uv[:, 1]) * TH], 1)
            tri = np.asarray(d["idx"], np.int64).reshape(-1, 3)
            polys = np.rint(px[tri] * 16).astype(np.int32)   # 4-bit subpixel
            cv2.fillPoly(lab, list(polys), int(i), lineType=cv2.LINE_8, shift=4)
        arr = lab.astype(np.int16)
        np.save(f, arr)
        out.append(arr)
        log("owner map", k, flush=True) if log is print else log("owner map", k)
    return out


def load_capture(prefix):
    P = lambda s: Path(f"{prefix}{s}")
    meta = json.loads(P("_meta.json").read_text())
    D = json.loads(P("_drawables.json").read_text())
    return meta, D


def correspond(prefix, log=print):
    meta, dd = load_capture(prefix)
    D = dd["drawables"]
    md = ModelDir(meta["model"])
    orig = np.asarray(Image.open(f"{prefix}.png").convert("RGBA")).astype(np.int32)
    hi = np.asarray(Image.open(f"{prefix}_code_hi.png").convert("RGBA")).astype(np.int32)
    lo = np.asarray(Image.open(f"{prefix}_code_lo.png").convert("RGBA")).astype(np.int32)
    k, x, y, ok0 = decode(hi, lo)
    loose = ok0 & (hi[..., 3] > 20) & (lo[..., 3] > 20) & (k < len(md.textures))
    ok = ok0 & (orig[..., 3] > 250) & (hi[..., 3] > 250) & (lo[..., 3] > 250) & (k < len(md.textures))
    owners = owner_maps(md.path, D, log=log)
    pages = [np.asarray(Image.open(t).convert("RGBA")) for t in md.textures]
    kk, xx, yy = k[ok], x[ok], y[ok]
    drw = np.full(kk.shape, -1, np.int32)
    tex_rgb = np.zeros((kk.size, 3), np.int32)
    for p in range(len(pages)):
        m = kk == p
        H, W = pages[p].shape[:2]
        xs, ys = np.clip(xx[m], 0, W - 1), np.clip(yy[m], 0, H - 1)
        drw[m] = owners[p][ys, xs]
        tex_rgb[m] = pages[p][ys, xs, :3]
    diff = np.abs(tex_rgb - orig[..., :3][ok]).max(1)
    # loose set: any visible coded pixel (semi-transparent layers over background included), for picking only
    lk, lx, ly = k[loose], x[loose], y[loose]
    ldrw = np.full(lk.shape, -1, np.int32)
    for p in range(len(pages)):
        m = lk == p
        H, W = pages[p].shape[:2]
        ldrw[m] = owners[p][np.clip(ly[m], 0, H - 1), np.clip(lx[m], 0, W - 1)]
    lpy, lpx = np.nonzero(loose)
    valid = (drw >= 0) & (diff < CONSISTENT_DIFF)
    idf = Path(f"{prefix}_ids.npy")
    if idf.exists():   # strong check: the top drawable in the ID render must be the texel's owner
        ids = np.load(idf).astype(np.int32)
        valid &= ids[ok] == drw
    else:
        log("warning: no P_ids.npy; correspondences over blend layers may be wrong (run capture_ids first)")
    py, px = np.nonzero(ok)
    np.savez_compressed(f"{prefix}_corr.npz", py=py, px=px, k=kk, x=xx, y=yy, drw=drw, valid=valid, diff=diff,
                        lpy=lpy, lpx=lpx, ldrw=ldrw)
    stats = {"opaque": int((orig[..., 3] > 250).sum()), "coded": int(ok.sum()),
             "in_drawable": float((drw >= 0).mean()) if drw.size else 0.0, "consistent": float(valid.mean()) if drw.size else 0.0}
    log("corr", stats)
    return stats


def drawable_map(prefix, loose=False):
    """HxW int array: drawable index of the top opaque layer per pixel (-1 where unknown).
    loose=True also includes semi-transparent top layers (useful for picking, not for colour fitting)."""
    meta, _ = load_capture(prefix)
    C = np.load(f"{prefix}_corr.npz")
    out = np.full((meta["h"], meta["w"]), -1, np.int32)
    if loose:
        out[C["lpy"], C["lpx"]] = C["ldrw"]
    out[C["py"], C["px"]] = C["drw"]
    return out


def pick(prefix, box, inv, top=12):
    """Drawables visible inside box=(x0, y0, x1, y1) ranked by pixel count, with part ids and names.
    Uses P_ids.npy (from capture_ids) when present, else the code correspondence (unreliable over blend layers).
    inv: Moc.inventory() dict (drawable index == art mesh index)."""
    idf = Path(f"{prefix}_ids.npy")
    dm = np.load(idf) if idf.exists() else drawable_map(prefix, loose=True)
    x0, y0, x1, y1 = box
    sub = dm[y0:y1, x0:x1]
    ids, counts = np.unique(sub[sub >= 0], return_counts=True)
    pname = {p["id"]: p["name"] for p in inv["parts"]}
    rows = []
    for i, c in sorted(zip(ids.tolist(), counts.tolist()), key=lambda t: -t[1])[:top]:
        m = inv["meshes"][i]
        rows.append({"drawable": i, "id": m["id"], "pixels": c, "part": m["part"], "part_name": pname.get(m["part"], ""),
                     "blend": m["blend"]})
    return rows


def highlight(prefix, drawable_ids, out, bg=(128, 128, 132)):
    """Save the render with the given drawables' visible pixels tinted red (debug view for pick)."""
    from .render import composite
    src = Path(f"{prefix}.png")
    im = np.asarray(composite(Image.open(src), bg)).copy()
    idf = Path(f"{prefix}_ids.npy")
    dm = np.load(idf) if idf.exists() else drawable_map(prefix, loose=True)
    m = np.isin(dm, list(drawable_ids))
    im[m] = (0.4 * im[m] + 0.6 * np.array([255, 0, 0])).astype(np.uint8)
    Image.fromarray(im).save(out)
    return int(m.sum())


def build_id_model(model_dir, dst=None, skip_blend=False, log=print):
    """Debug clone where each art mesh draws as a flat unique colour: white pages (alpha binarised at 0.5), normal blend,
    keyform opacities binarised,
    multiply = id colour, screen = 0. Rendered, every pixel names its top drawable, including layers that are
    multiply/additive in the real model. (A screen colour cannot be used: with premultiplied blending it leaks
    additively even where opacity or alpha is 0.)"""
    import os
    from .codes import code_page_paths
    from .moc import Moc
    from .variant import clone_model
    md = ModelDir(model_dir)
    tag = hashlib.sha1(str(md.path).encode()).hexdigest()[:12] + ("_base" if skip_blend else "")
    if dst is None:
        import tempfile
        cache_root = WORK / "id_models"
        cache_root.mkdir(parents=True, exist_ok=True)
        dst = Path(tempfile.mkdtemp(prefix=tag + "_", dir=cache_root)) / "model"
    else:
        dst = Path(dst)
    clone_model(md.path, dst)
    for k, t in enumerate(md.model3["FileReferences"]["Textures"]):
        white = code_page_paths(md.path / t, k, modes=("whitebin",))["whitebin"]
        p = dst / t
        if p.exists() or p.is_symlink():
            p.unlink()
        os.symlink(white, p)
    moc = Moc(md.moc_path)
    flags = moc.view("art_mesh.drawable_flags")
    n = len(moc.mesh_ids)
    blend = [k for k in range(n) if flags[k] & 3]
    if skip_blend:   # base-layer map: what is under multiply/additive layers
        moc.hide(blend)
    flags[:n] = flags[:n] & ~np.uint8(3)
    op = moc.view("art_mesh_keyform.opacities")
    nk = len(moc.table("art_mesh_keyform.opacities"))
    op[:nk] = np.where(op[:nk] >= 0.5, 1.0, 0.0)   # top-most *substantially opaque* layer wins
    mrow, srow = moc.view("art_mesh_keyform.mul_row"), moc.view("art_mesh_keyform.scr_row")
    R, G, B = (moc.view(f"color.mul_{c}") for c in "rgb")
    SR, SG, SB = (moc.view(f"color.scr_{c}") for c in "rgb")
    for k in range(n):
        c = id_colour(k)
        for f in moc.mesh_keyforms(k):
            r = mrow[f]; R[r], G[r], B[r] = c[0] / 255, c[1] / 255, c[2] / 255
            s = srow[f]; SR[s] = SG[s] = SB[s] = 0.0
    out = ModelDir(dst).moc_path
    if out.is_symlink():
        out.unlink()
    moc.save(out)
    return dst


def id_colour(k):
    """mesh index -> RGB with a fixed check channel so background/blends are rejected."""
    return ((k >> 8) * 16 + 8, k & 255, 0xA5 ^ (k & 255))


def decode_ids(rgba):
    a = rgba.astype(np.int32)
    hi = np.rint((a[..., 0] - 8) / 16).astype(np.int32)
    k = hi * 256 + a[..., 1]
    ok = (a[..., 3] > 128) & (a[..., 2] == (0xA5 ^ a[..., 1])) & (np.abs(a[..., 0] - (hi * 16 + 8)) <= 3)
    return np.where(ok, k, -1)


def capture_ids(renderer, model_dir, prefix, w, h, params=None, zoom=None, sim=None, fit=None):
    """Write P_ids.npy (top drawable per pixel, blend layers included) and P_baseids.npy (top drawable ignoring
    multiply/additive layers). Drawable index order == art mesh index order."""
    out = {}
    for name, skip in (("ids", False), ("baseids", True)):
        d = build_id_model(model_dir, skip_blend=skip)
        renderer.load(d, w, h, zoom=zoom, nearest=True, fit=fit)
        ids = decode_ids(np.asarray(renderer.render(params, sim)))
        np.save(f"{prefix}_{name}.npy", ids.astype(np.int16))
        out[name] = ids
    return out["ids"]
