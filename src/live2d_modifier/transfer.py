"""Back-project edited renders onto a Live2D model's texture pages.

Two layers per drawable:
  1. colour map: a luminance-binned Lab delta fitted on (original render, edit) pixel pairs where the drawable is the
     single opaque layer. Applied to every texel the drawable owns, so parts hidden in this pose change consistently
     and the original high-resolution line work and shading survive.
  2. residual (optional): for texels whose mesh position is visible in the capture, add (edit - colour-mapped render)
     sampled through the mesh (UV triangle -> screen triangle). This carries new surface patterns, makeup and
     redesigned details that a pure colour map cannot express. The residual is at render resolution, so render the
     capture at least at the size the model is shown.

Drawables with too few samples inherit the colour map of their nearest part ancestor (never a root part).
Multiply/additive drawables never get their own fit (their composite colour is not their texel colour).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .corr import load_capture, owner_maps
from .model import ModelDir
from .variant import clone_model

Image.MAX_IMAGE_PIXELS = None
NB = 24
EDGES = np.linspace(0, 100, NB + 1)
CENTERS = (EDGES[:-1] + EDGES[1:]) / 2


def lab(rgb):
    return cv2.cvtColor((np.clip(rgb, 0, 255) / 255).astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3)


def rgb_from_lab(L):
    return np.clip(cv2.cvtColor(L.astype(np.float32).reshape(-1, 1, 3), cv2.COLOR_LAB2RGB).reshape(-1, 3) * 255, 0, 255)


def fit_lut(src_lab, dst_lab, min_bin=8):
    """Median Lab delta per source-luminance bin; empty bins interpolated. None if nothing fits."""
    b = np.clip(np.digitize(src_lab[:, 0], EDGES) - 1, 0, NB - 1)
    delta = dst_lab - src_lab
    tab = np.full((NB, 3), np.nan, np.float32)
    for i in range(NB):
        m = b == i
        if m.sum() >= min_bin:
            tab[i] = np.median(delta[m], 0)
    good = ~np.isnan(tab[:, 0])
    if not good.any():
        return None
    for c in range(3):
        tab[:, c] = np.interp(CENTERS, CENTERS[good], tab[good, c])
    return tab


def apply_lut(L, tab):
    return L + np.stack([np.interp(L[:, 0], CENTERS, tab[:, c]) for c in range(3)], 1)


def bilinear(img, x, y):
    """Sample img (H, W[, C]) at float coords (no bounds handling beyond clipping)."""
    H, W = img.shape[:2]
    x = np.clip(x, 0, W - 1.001); y = np.clip(y, 0, H - 1.001)
    x0 = x.astype(int); y0 = y.astype(int); fx = x - x0; fy = y - y0
    if img.ndim == 3:
        fx = fx[:, None]; fy = fy[:, None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy)
            + img[y0 + 1, x0] * (1 - fx) * fy + img[y0 + 1, x0 + 1] * fx * fy)


def align_edit(orig_rgb, edit_rgb, scale=0.25, log=print):
    """Affine ECC alignment of the edit onto the render, on gradient magnitude (robust to large colour changes)."""
    def grad(img):
        g = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        g = cv2.GaussianBlur(g, (0, 0), 1.0)
        m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
        return m / (m.max() + 1e-6)
    g0, g1 = grad(orig_rgb), grad(edit_rgb)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, w = cv2.findTransformECC(cv2.resize(g0, None, fx=scale, fy=scale), cv2.resize(g1, None, fx=scale, fy=scale),
                                    warp.copy(), cv2.MOTION_AFFINE,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 300, 1e-6), None, 5)
        warp = w.copy(); warp[:, 2] /= scale
    except cv2.error as e:
        log("ECC failed, using unaligned edit:", e)
    out = cv2.warpAffine(edit_rgb, warp, (edit_rgb.shape[1], edit_rgb.shape[0]),
                         flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
    log("alignment", np.round(warp, 4).tolist())
    return out, warp


@dataclass
class Capture:
    prefix: str
    edit: str
    residual: bool = True
    meta: dict = field(default_factory=dict)

    def load(self, bg, log=print):
        self.meta, dd = load_capture(self.prefix)
        self.D, self.PARTS = dd["drawables"], dd["parts"]
        o = np.asarray(Image.open(f"{self.prefix}.png").convert("RGBA")).astype(np.float32)
        self.alpha = o[..., 3]
        a = self.alpha[..., None] / 255
        self.orig_rgb = o[..., :3] * a + np.array(bg, np.float32) * (1 - a)
        e = np.asarray(Image.open(self.edit).convert("RGB")).astype(np.float32)
        if e.shape[:2] != self.orig_rgb.shape[:2]:
            e = cv2.resize(e, (self.orig_rgb.shape[1], self.orig_rgb.shape[0]), interpolation=cv2.INTER_AREA)
        self.edit_rgb, self.warp = align_edit(self.orig_rgb, e, log=log)
        self.C = np.load(f"{self.prefix}_corr.npz")
        bf = Path(f"{self.prefix}_baseids.npy")
        self.base = np.load(bf).astype(np.int32) if bf.exists() else None
        return self


def _samples_by_drawable(drw):
    order = np.argsort(drw, kind="stable")
    uniq, starts = np.unique(drw[order], return_index=True)
    ends = list(starts[1:]) + [len(order)]
    return {int(d): order[s0:s1] for d, s0, s1 in zip(uniq, starts, ends) if d >= 0}


class Transfer:
    def __init__(self, src_model, captures, bg=(128, 128, 132), min_samples=120, log=print):
        self.src = ModelDir(src_model)
        self.log = log
        self.caps = [c.load(bg, log) for c in captures]
        self.min_samples = min_samples
        self.D = self.caps[0].D
        self.PARTS = self.caps[0].PARTS
        self.part_parent = [p["parent"] for p in self.PARTS]

    # ---- colour maps ----
    def sharing_pairs(self):
        if not hasattr(self, "_pairs"):
            from .atlas import find_conflicts
            self._pairs = find_conflicts(self.src.path, self.D)
        return self._pairs

    def fit_maps(self, skip=(), own_only=False, share=True):
        per_cap = []
        for c in self.caps:
            v = c.C["valid"]
            py, px, drw = c.C["py"], c.C["px"], c.C["drw"].copy()
            if c.base is not None:
                # tier 2: translucent top layers (decoded owner == top id, colour not a single layer); used only for
                # drawables without enough tier-1 samples
                top = np.load(f"{c.prefix}_ids.npy").astype(np.int32)[py, px]
                tier2 = (~v) & (drw >= 0) & (top == drw)
                S1 = _samples_by_drawable(np.where(v, drw, -1))
                weak = {d for d in np.unique(drw[tier2]).tolist() if len(S1.get(d, ())) < self.min_samples}
                use = v | (tier2 & np.isin(drw, list(weak)))
            else:
                use = v
            drw = np.where(use, drw, -1)
            per_cap.append((lab(c.orig_rgb[py, px]), lab(c.edit_rgb[py, px]), _samples_by_drawable(drw)))
        maps, source = {}, {}
        part_pool = {}
        best = {}
        for ci, (sl, dl, S) in enumerate(per_cap):
            for d, ix in S.items():
                if len(ix) > len(best.get(d, (None, ()))[1]):
                    best[d] = (ci, ix)
        for d, (ci, ix) in best.items():
            if self.D[d]["flags"] & 3:
                continue
            p = self.D[d]["parent"]
            while p is not None and p >= 0:
                part_pool.setdefault(p, []).append((ci, ix))
                p = self.part_parent[p]
        # own fits first, so texel-sharing partners (mirrored L/R meshes) can borrow them
        own_maps = {}
        for d, (ci, ix) in best.items():
            if not (self.D[d]["flags"] & 3) and len(ix) >= self.min_samples:
                t = fit_lut(per_cap[ci][0][ix], per_cap[ci][1][ix])
                if t is not None:
                    own_maps[d] = t
        partners = {}
        if share:
            ranked = {}
            for a, b, n in self.sharing_pairs():
                for x, y in ((a, b), (b, a)):
                    if y in own_maps and x not in own_maps and n > ranked.get(x, 0):
                        ranked[x] = n
                        partners[x] = (own_maps[y], f"share:{self.D[y]['id']}")
        for d in range(len(self.D)):
            if d in skip:
                continue
            blended = bool(self.D[d]["flags"] & 3)
            if d in best and not blended and len(best[d][1]) >= self.min_samples:
                ci, ix = best[d]
                t = fit_lut(per_cap[ci][0][ix], per_cap[ci][1][ix])
                if t is not None:
                    maps[d], source[d] = t, f"own:{ci}"
                    continue
            if own_only:
                continue
            if share and d in partners:
                maps[d], source[d] = partners[d]
                continue
            p = self.D[d]["parent"]
            while p is not None and p >= 0 and self.part_parent[p] >= 0:
                if p in part_pool:
                    pool = part_pool[p]
                    if sum(len(ix) for _, ix in pool) >= self.min_samples * 3:
                        sl = np.concatenate([per_cap[ci][0][ix] for ci, ix in pool])
                        dl = np.concatenate([per_cap[ci][1][ix] for ci, ix in pool])
                        t = fit_lut(sl, dl)
                        if t is not None:
                            maps[d], source[d] = t, f"part:{self.PARTS[p]['id']}"
                            break
                p = self.part_parent[p]
        self.maps, self.source = maps, source
        own = sum(1 for s in source.values() if s.startswith("own"))
        self.log(f"colour maps: own {own}, inherited {len(maps) - own}, unchanged {len(self.D) - len(maps)}")
        return maps

    def apply_overrides(self, ov):
        """ov: {"borrow": {dst_mesh_id: src_mesh_id}, "keep": [mesh_id], "alpha": {mesh_id: scale},
        "no_residual": [mesh_id], "fill_hidden": [mesh_id], "affine_trim": percentile,
        "residual_holes": {"ref": capture name, "polys": [[[x, y], ...], ...], "dilate": px, "feather": px}}.
        Mesh ids or drawable indices. residual_holes: screen polygons (in the ref capture's pixels, e.g. "head" for
        prefix .../head) where the edit's residual is ignored in every capture, so those texels keep the colour-mapped
        source art. For details the image model painted across several meshes (flowers on hair and ribbon) that tear
        apart when the meshes move. Colour-map fits are unaffected."""
        idx = {d["id"]: i for i, d in enumerate(self.D)}
        res = lambda m: m if isinstance(m, int) else idx[m]
        for dst, src in (ov.get("borrow") or {}).items():
            s_ = res(src)
            if s_ in self.maps:
                self.maps[res(dst)] = self.maps[s_]
                self.source[res(dst)] = f"borrow:{self.D[s_]['id']}"
        for m in ov.get("keep") or []:
            self.maps.pop(res(m), None); self.source.pop(res(m), None)
        self.alpha_scale = {res(m): float(v) for m, v in (ov.get("alpha") or {}).items()}
        self.no_residual = {res(m) for m in ov.get("no_residual") or []}
        self.fill_hidden = {res(m) for m in ov.get("fill_hidden") or []}
        if "affine_trim" in ov:
            self.affine_trim = float(ov["affine_trim"])
        if ov.get("residual_holes"):
            self.residual_holes = ov["residual_holes"]

    @staticmethod
    def _inpaint_hidden(page, ys, xs, seen):
        """Replace texels never seen in any capture by a smooth continuation of the seen ones (Telea inpaint).
        Removes hidden luminance detail (e.g. text painted under another layer) that a colour map would reveal."""
        if seen.all() or not seen.any():
            return
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        sub = page[y0:y1, x0:x1, :3].copy()
        mask = np.zeros(sub.shape[:2], np.uint8)
        mask[ys[~seen] - y0, xs[~seen] - x0] = 255
        s = 4 if max(sub.shape[:2]) > 1500 else 1   # inpaint at reduced size for big islands, then upsample
        small = cv2.resize(sub, None, fx=1 / s, fy=1 / s, interpolation=cv2.INTER_AREA) if s > 1 else sub
        msmall = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST) if s > 1 else mask
        filled = cv2.inpaint(small, msmall, 6, cv2.INPAINT_TELEA)
        if s > 1:
            filled = cv2.resize(filled, (sub.shape[1], sub.shape[0]), interpolation=cv2.INTER_LINEAR)
        page[ys[~seen], xs[~seen], :3] = filled[ys[~seen] - y0, xs[~seen] - x0]

    # ---- residual fields ----
    def _hole_mask(self, c, M, Ms):
        """Soft [0, 1] screen mask of residual_holes for capture c (1 = ignore the residual), or None.
        Polygons are given in the ref capture's pixels and carried over through model space (both captures share the
        pose, so ref pixel -> model = inverse of the ref screen affine, then this capture's affine)."""
        hs = getattr(self, "residual_holes", None)
        if not hs:
            return None
        ref = Ms.get(hs.get("ref", "head"))
        if ref is None:
            raise ValueError(f"residual_holes ref capture {hs.get('ref')!r} not among {list(Ms)}")
        A, t = ref[:2].T, ref[2]                      # pixel = A @ model + t
        H, W = c.alpha.shape
        scale = float(np.sqrt(abs(np.linalg.det(M[:2]))) / np.sqrt(abs(np.linalg.det(ref[:2]))))
        m = np.zeros((H, W), np.uint8)
        for poly in hs["polys"]:
            q = np.asarray(poly, np.float64)
            model = np.linalg.solve(A, (q - t).T).T
            pix = np.hstack([model, np.ones((len(model), 1))]) @ M
            cv2.fillPoly(m, [np.rint(pix * 16).astype(np.int32)], 1, shift=4)
        dil = max(0.0, float(hs.get("dilate", 0)) * scale)
        fea = max(0.5, float(hs.get("feather", 4)) * scale)
        dist = cv2.distanceTransform((1 - m).astype(np.uint8), cv2.DIST_L2, 5)
        return np.clip(1 - (dist - dil) / fea, 0, 1).astype(np.float32)

    def _residual_fields(self, c, hole=None):
        """Screen-space Lab residual (edit - colour-mapped render) and weight, per base-layer drawable."""
        H, W = c.alpha.shape
        base = c.base if c.base is not None else np.full((H, W), -1, np.int32)
        if c.base is None:
            base[c.C["py"], c.C["px"]] = c.C["drw"]
        R = np.zeros((H, W, 3), np.float32)
        Wt = np.zeros((H, W), np.float32)
        ys, xs = np.nonzero((base >= 0) & (c.alpha > 250))
        ds = base[ys, xs]
        src_l, dst_l = lab(c.orig_rgb[ys, xs]), lab(c.edit_rgb[ys, xs])
        for d, ix in _samples_by_drawable(ds).items():
            tab = self.maps.get(d)
            if tab is None:
                continue
            R[ys[ix], xs[ix]] = dst_l[ix] - apply_lut(src_l[ix], tab)
            Wt[ys[ix], xs[ix]] = 1
        # interior only: a pixel counts if its 3x3 neighbourhood is the same base drawable
        same = np.ones((H, W), bool)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                same &= np.roll(np.roll(base, dy, 0), dx, 1) == base
        Wt *= same
        if hole is not None:
            Wt *= 1 - hole
        Rb = cv2.GaussianBlur(R * Wt[..., None], (0, 0), 0.6)
        Wb = cv2.GaussianBlur(Wt, (0, 0), 0.6)
        Rn = Rb / np.maximum(Wb, 1e-3)[..., None]
        return Rn, Wb, base

    def _screen_affine(self, c, rasters):
        """Fit model-space -> pixel affine from valid correspondences (texel -> triangle -> barycentric -> position)."""
        v = c.C["valid"]
        py, px, drw, tx, ty = (c.C[k][v] for k in ("py", "px", "drw", "x", "y"))
        S = _samples_by_drawable(drw)
        P, Q = [], []
        for d in sorted(S, key=lambda d: -len(S[d]))[:40]:
            ix = S[d][:: max(1, len(S[d]) // 400)]
            pos = self._texel_to_pos(c, d, tx[ix], ty[ix], rasters)
            ok = ~np.isnan(pos[:, 0])
            P.append(pos[ok]); Q.append(np.stack([px[ix][ok], py[ix][ok]], 1))
        P, Q = np.concatenate(P), np.concatenate(Q).astype(np.float64)
        A = np.hstack([P, np.ones((len(P), 1))])
        keep = np.ones(len(P), bool)
        # trim outliers (stray correspondences) and refit. affine_trim (overrides) lowers the kept percentile and
        # iterates to the inlier set: some large meshes (legs, skirt) decode wrong texels for most of their pixels, and
        # the 90th-percentile trim lets them shear/scale the fit by up to ~10 px, so residuals land on the wrong texels.
        pct = getattr(self, "affine_trim", 50)
        for _ in range(3 if pct >= 90 else 8):
            M, *_ = np.linalg.lstsq(A[keep], Q[keep], rcond=None)
            e = np.abs(A @ M - Q).max(1)
            keep = e < max(3.0, np.percentile(e[keep] if pct < 90 else e, pct))
        err = np.abs(A[keep] @ M - Q[keep]).mean()
        self.log(f"screen affine fit on {len(P)} samples, mean error {err:.2f}px")
        return M  # (3, 2)

    def _raster(self, c, d, cache):
        if (id(c), d) in cache:
            return cache[(id(c), d)]
        dr = c.D[d]
        TW, TH = self.page_size[dr["tex"]]
        uv = np.asarray(dr["uv"], np.float64).reshape(-1, 2)
        T = np.stack([uv[:, 0] * TW, (1 - uv[:, 1]) * TH], 1)
        tri = np.asarray(dr["idx"], np.int64).reshape(-1, 3)
        x0, y0 = np.floor(T.min(0)).astype(int) - 1
        x1, y1 = np.ceil(T.max(0)).astype(int) + 2
        x0, y0 = max(x0, 0), max(y0, 0)
        lab_img = np.full((y1 - y0, x1 - x0), -1, np.int32)
        for t, f in enumerate(tri):
            cv2.fillPoly(lab_img, [np.rint((T[f] - [x0, y0]) * 16).astype(np.int32)], t, lineType=cv2.LINE_8, shift=4)
        r = (lab_img, x0, y0, T, tri, np.asarray(dr["pos"], np.float64).reshape(-1, 2))
        cache[(id(c), d)] = r
        return r

    def _texel_to_pos(self, c, d, xs, ys, cache):
        lab_img, x0, y0, T, tri, P = self._raster(c, d, cache)
        lx, ly = xs - x0, ys - y0
        inside = (lx >= 0) & (ly >= 0) & (lx < lab_img.shape[1]) & (ly < lab_img.shape[0])
        out = np.full((len(xs), 2), np.nan)
        t = np.full(len(xs), -1)
        t[inside] = lab_img[ly[inside], lx[inside]]
        ok = t >= 0
        if not ok.any():
            return out
        f = tri[t[ok]]
        a, b, cc = T[f[:, 0]], T[f[:, 1]], T[f[:, 2]]
        p = np.stack([xs[ok] + 0.5, ys[ok] + 0.5], 1)
        v0, v1, v2 = b - a, cc - a, p - a
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        l1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
        l2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
        l0 = 1 - l1 - l2
        out[ok] = l0[:, None] * P[f[:, 0]] + l1[:, None] * P[f[:, 1]] + l2[:, None] * P[f[:, 2]]
        return out

    # ---- write ----
    def _candidate_texels(self, d, own, alpha):
        """Texels inside drawable d's UV triangles with alpha > 0, and whether d owns each of them."""
        dr = self.D[d]
        TW, TH = self.page_size[dr["tex"]]
        uv = np.asarray(dr["uv"], np.float64).reshape(-1, 2)
        T = np.stack([uv[:, 0] * TW, (1 - uv[:, 1]) * TH], 1)
        tri = np.asarray(dr["idx"], np.int64).reshape(-1, 3)
        x0, y0 = np.maximum(np.floor(T.min(0)).astype(int) - 1, 0)
        x1, y1 = np.minimum(np.ceil(T.max(0)).astype(int) + 2, [TW, TH])
        if x1 <= x0 or y1 <= y0:
            return None
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, list(np.rint((T[tri] - [x0, y0]) * 16).astype(np.int32)), 1, shift=4)
        sub_own = np.asarray(own[y0:y1, x0:x1]) == d
        m = (m.astype(bool) | sub_own) & (alpha[y0:y1, x0:x1] > 0)
        ys, xs = np.nonzero(m)
        return ys + y0, xs + x0, sub_own[ys, xs]

    def write(self, dst, residual_gain=1.0, residual_drawables=None, seen_threshold=0.3):
        """Write dst model.

        Texel assignment: a texel shared by several drawables goes to the drawable that shows it in a capture
        (highest residual weight); texels nobody sees go to the owner map (visible drawables, later render order).
        Colours are always computed from the source pages, so shared texels are never mapped twice.
        residual_drawables: optional set limiting where residuals apply."""
        dst = Path(dst)
        clone_model(self.src.path, dst, link_textures=True)
        src_pages = [np.asarray(Image.open(t).convert("RGBA")) for t in self.src.textures]
        pages = [p.copy() for p in src_pages]
        self.page_size = [(p.shape[1], p.shape[0]) for p in pages]
        owners = owner_maps(self.src.path, self.D, log=self.log)
        best = {}
        fields, Ms = [], {}
        for c in self.caps:
            if not c.residual:
                continue
            cache = {}
            M = self._screen_affine(c, cache)
            Ms[Path(c.prefix).name] = M
            fields.append([c, None, None, None, M, cache])
        for f in fields:
            hole = self._hole_mask(f[0], f[4], Ms)
            f[1], f[2], f[3] = self._residual_fields(f[0], hole)
            if hole is not None:
                self.log(f"residual holes in {Path(f[0].prefix).name}: {int((hole > 0.5).sum())} px")
        stats = {"recoloured": 0, "residual": 0, "reassigned": 0}
        no_res = getattr(self, "no_residual", set())
        for d, tab in self.maps.items():
            k = self.D[d]["tex"]
            cand = self._candidate_texels(d, owners[k], src_pages[k][..., 3])
            if cand is None or len(cand[0]) == 0:
                continue
            ys, xs, owned = cand
            L = lab(src_pages[k][ys, xs, :3].astype(np.float32))
            newL = apply_lut(L, tab)
            wacc = np.zeros(len(ys), np.float32); wmax = np.zeros(len(ys), np.float32)
            if (residual_drawables is None or d in residual_drawables) and d not in no_res:
                acc = np.zeros_like(newL)
                for ci, (c, Rn, Wb, base, M, cache) in enumerate(fields):
                    prio = 4.0 ** ci   # later captures (usually closer views) dominate where they see the texel
                    pos = self._texel_to_pos(c, d, xs, ys, cache)
                    ok = ~np.isnan(pos[:, 0])
                    if not ok.any():
                        continue
                    sc_ = np.hstack([pos[ok], np.ones((ok.sum(), 1))]) @ M
                    sx, sy = sc_[:, 0], sc_[:, 1]
                    H, W = Wb.shape
                    inb = (sx >= 0) & (sy >= 0) & (sx < W - 1) & (sy < H - 1)
                    idx = np.nonzero(ok)[0][inb]
                    sx, sy = sx[inb], sy[inb]
                    vis = base[np.rint(sy).astype(int), np.rint(sx).astype(int)] == d
                    idx, sx, sy = idx[vis], sx[vis], sy[vis]
                    if len(idx) == 0:
                        continue
                    w = bilinear(Wb, sx, sy)
                    acc[idx] += bilinear(Rn, sx, sy) * (w * prio)[:, None]
                    wacc[idx] += w * prio
                    wmax[idx] = np.maximum(wmax[idx], w)
                m = wacc > 0
                newL[m] += residual_gain * (acc[m] / wacc[m][:, None]) * np.minimum(1.0, wmax[m])[:, None]
            if k not in best:
                best[k] = np.full(src_pages[k].shape[:2], -1.0, np.float16)
            b = best[k][ys, xs].astype(np.float32)
            write = (owned & (wmax >= b)) | ((wmax > seen_threshold) & (wmax > b))
            if not write.any():
                continue
            stats["reassigned"] += int((write & ~owned).sum())
            stats["recoloured"] += int(write.sum())
            stats["residual"] += int((write & (wacc > 0)).sum())
            ys, xs, newL, wacc = ys[write], xs[write], newL[write], wacc[write]
            best[k][ys, xs] = wmax[write]
            newL[:, 0] = np.clip(newL[:, 0], 0, 100)
            pages[k][ys, xs, :3] = rgb_from_lab(newL).astype(np.uint8)
            if d in getattr(self, "fill_hidden", ()) and fields:
                self._inpaint_hidden(pages[k], ys, xs, wacc > 0)
            sc = getattr(self, "alpha_scale", {}).get(d)
            if sc is not None:
                pages[k][ys, xs, 3] = np.clip(src_pages[k][ys, xs, 3].astype(np.float32) * sc, 0, 255).astype(np.uint8)
        md = ModelDir(dst)
        for k, t in enumerate(md.textures):
            if t.is_symlink():
                t.unlink()
            Image.fromarray(pages[k], "RGBA").save(t, compress_level=3)
        (dst / "transfer_sources.json").write_text(json.dumps({str(k): v for k, v in self.source.items()}, indent=0))
        self.log("transfer", stats)
        return stats


def map_disagreement(tab_a, tab_b):
    """Mean Lab distance between two colour maps over the luminance bins (both None-safe)."""
    z = np.zeros((NB, 3), np.float32)
    tab_a = z if tab_a is None else tab_a
    tab_b = z if tab_b is None else tab_b
    return float(np.linalg.norm(tab_a - tab_b, axis=1).mean())


def conflict_report(tr, model_dir=None, threshold=8.0, log=print):
    """Texel-sharing pairs whose colour maps disagree by more than threshold (Lab units): these need a page split."""
    from .atlas import find_conflicts
    model_dir = model_dir or tr.src.path
    out = []
    for a, b, n in (tr.sharing_pairs() if model_dir == tr.src.path else find_conflicts(model_dir, tr.D)):
        dis = map_disagreement(tr.maps.get(a), tr.maps.get(b))
        if dis > threshold:
            out.append({"a": a, "b": b, "a_id": tr.D[a]["id"], "b_id": tr.D[b]["id"], "texels": n, "disagreement": round(dis, 1),
                        "a_src": tr.source.get(a), "b_src": tr.source.get(b)})
    log(f"{len(out)} conflicting pairs above {threshold}")
    return out
