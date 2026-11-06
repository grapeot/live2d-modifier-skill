"""In-place moc3 editing.

Edits never change an array length, so every section offset (including the v4.02/v5 sections that py-moc3 does not
model) stays valid and Cubism Core keeps loading the file. py-moc3 is used only as a reader for ids and index tables;
its own writer drops the v4.02+/v5 sections and must not be used to save.

Slot numbers of the v4.02/v5 colour tables follow Umamo's SectionLayout.kt.
"""
import fnmatch
import struct
from functools import cached_property
from pathlib import Path

import numpy as np

DT_NAMES = {"i32": "<i4", "bool": "<i4", "f32": "<f4", "i16": "<i2", "u8": "u1"}
EXTRA_SLOTS = {
    "art_mesh.color_base": (107, "<i4"),
    "color.mul_r": (108, "<f4"), "color.mul_g": (109, "<f4"), "color.mul_b": (110, "<f4"),
    "color.scr_r": (111, "<f4"), "color.scr_g": (112, "<f4"), "color.scr_b": (113, "<f4"),
    "art_mesh_keyform.mul_row": (141, "<i4"), "art_mesh_keyform.scr_row": (142, "<i4"),
}
# py-moc3 0.1.0 swaps these two names; expose the real meaning
SWAP = {"art_mesh.vertex_counts": "art_mesh.position_index_counts",
        "art_mesh.position_index_counts": "art_mesh.vertex_counts"}


class Moc:
    def __init__(self, src):
        from moc3 import Moc3
        self.src = Path(src)
        self.buf = bytearray(self.src.read_bytes())
        self.m = Moc3.from_bytes(bytes(self.buf))
        self.sot = struct.unpack_from("<160I", self.buf, 64)
        self.version = self.buf[4]
        self.slot = {}
        for i, e in enumerate(self.m._build_layout()):
            dt = DT_NAMES.get(str(e.elem_type).lower())
            self.slot[e.name] = (i + 2, dt)
        if self.version >= 4:
            self.slot.update(EXTRA_SLOTS)

    # ---------- raw access ----------
    def table(self, name):
        """Read-only list/array from py-moc3 with corrected naming."""
        return self.m[SWAP.get(name, name)]

    def view(self, name):
        """Writable numpy view over a section (from its start to the next section start)."""
        s, dt = self.slot[SWAP.get(name, name)]
        if dt is None:
            raise KeyError(f"{name} is not a numeric section")
        start = self.sot[s]
        nxt = min([o for o in self.sot if o > start] + [len(self.buf)])
        n = (nxt - start) // np.dtype(dt).itemsize
        return np.frombuffer(self.buf, dtype=dt, count=n, offset=start)

    def save(self, dst):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(bytes(self.buf))

    # ---------- structure ----------
    @cached_property
    def part_ids(self):
        return list(self.table("part.ids"))

    @cached_property
    def part_parent(self):
        return list(self.table("part.parent_part_indices"))

    @cached_property
    def mesh_ids(self):
        return list(self.table("art_mesh.ids"))

    @cached_property
    def param_ids(self):
        return list(self.table("parameter.ids"))

    @cached_property
    def deformer_ids(self):
        return list(self.table("deformer.ids"))

    def part_descendants(self, part_idx):
        kids = {part_idx}
        changed = True
        while changed:
            changed = False
            for i, p in enumerate(self.part_parent):
                if p in kids and i not in kids:
                    kids.add(i)
                    changed = True
        return kids

    def meshes_in_parts(self, part_ids, recursive=True):
        idx = set()
        for pid in part_ids:
            pi = self.part_ids.index(pid)
            idx |= self.part_descendants(pi) if recursive else {pi}
        par = self.table("art_mesh.parent_part_indices")
        return [k for k, p in enumerate(par) if p in idx]

    def select_meshes(self, ids=(), parts=(), patterns=()):
        """Union of explicit mesh ids, meshes under parts (recursive) and fnmatch patterns on mesh ids."""
        out = set(self.meshes_in_parts(parts)) if parts else set()
        for i in ids:
            out.add(self.mesh_ids.index(i))
        for pat in patterns:
            out |= {k for k, m in enumerate(self.mesh_ids) if fnmatch.fnmatch(m, pat)}
        return sorted(out)

    def mesh_keyforms(self, mi):
        b = self.table("art_mesh.keyform_begin_indices")[mi]
        return range(b, b + self.table("art_mesh.keyform_counts")[mi])

    def mesh_position_blocks(self, mi):
        """[(float_offset, n_floats)] per keyform of art mesh mi inside keyform_position.xys."""
        nv = self.table("art_mesh.vertex_counts")[mi]
        kb = self.view("art_mesh_keyform.keyform_position_begin_indices")
        return [(int(kb[k]), 2 * nv) for k in self.mesh_keyforms(mi)]

    def warp_position_blocks(self, deformer_id):
        """[(float_offset, n_floats)] per keyform of a warp deformer's control grid."""
        di = self.deformer_ids.index(deformer_id)
        if self.table("deformer.types")[di] != 0:
            raise ValueError(f"{deformer_id} is not a warp deformer")
        wi = self.table("deformer.specific_indices")[di]
        b = self.table("warp_deformer.keyform_begin_indices")[wi]
        n = self.table("warp_deformer.keyform_counts")[wi]
        nv = self.table("warp_deformer.vertex_counts")[wi]
        kb = self.view("warp_deformer_keyform.keyform_position_begin_indices")
        return [(int(kb[k]), 2 * nv) for k in range(b, b + n)]

    def mesh_uv_span(self, mi):
        return int(self.table("art_mesh.uv_begin_indices")[mi]), 2 * int(self.table("art_mesh.vertex_counts")[mi])

    # ---------- edits ----------
    def hide(self, meshes):
        """Opacity 0 in every keyform: the mesh never draws, whatever the parameters."""
        op = self.view("art_mesh_keyform.opacities")
        for k in meshes:
            for f in self.mesh_keyforms(k):
                op[f] = 0.0
        return len(meshes)

    def unmask(self, meshes):
        """Drop clipping masks from meshes (mask count -> 0; the mask index table keeps its length)."""
        mc = self.view("art_mesh.mask_counts")
        for k in meshes:
            mc[k] = 0
        return len(meshes)

    def opacity_scale(self, meshes, s):
        op = self.view("art_mesh_keyform.opacities")
        for k in meshes:
            for f in self.mesh_keyforms(k):
                op[f] = np.clip(op[f] * s, 0, 1)

    def transform_blocks(self, blocks, fn):
        """Apply fn(xy[N,2]) -> xy to each keyform block in place."""
        pos = self.view("keyform_position.xys")
        for o, n in blocks:
            xy = pos[o:o + n].reshape(-1, 2)
            xy[:] = fn(xy.copy())

    def affine_meshes(self, meshes, sx=1.0, sy=1.0, dx=0.0, dy=0.0, pivot="center", per_deformer=True):
        """Scale about a pivot then translate, identically in every keyform. Mesh coordinates live in the parent
        deformer's space, so pivots are computed per parent deformer group. pivot: center|top|bottom|(x, y)."""
        par = self.table("art_mesh.parent_deformer_indices")
        groups = {}
        for k in meshes:
            groups.setdefault(par[k] if per_deformer else 0, []).append(k)
        pos = self.view("keyform_position.xys")
        for g, ms in groups.items():
            blocks = [b for k in ms for b in self.mesh_position_blocks(k)]
            xs = np.concatenate([pos[o:o + n:2] for o, n in blocks])
            ys = np.concatenate([pos[o + 1:o + n:2] for o, n in blocks])
            if isinstance(pivot, (tuple, list)):
                px, py = pivot
            else:
                px = float(xs.mean())
                py = {"center": float(ys.mean()), "top": float(ys.min()), "bottom": float(ys.max())}[pivot]
            self.transform_blocks(blocks, lambda xy: np.stack([px + (xy[:, 0] - px) * sx + dx,
                                                               py + (xy[:, 1] - py) * sy + dy], 1))
        return groups

    def set_texture(self, meshes, tex_index, uv_box=None):
        """Point meshes to another texture page. uv_box=(u0, v0, u1, v1): the old atlas box that becomes the full new
        page (UVs remapped into 0..1). Without uv_box UVs are kept (the new page must share the atlas layout)."""
        ti = self.view("art_mesh.texture_indices")
        uv = self.view("uv.xys")
        for k in meshes:
            ti[k] = tex_index
            if uv_box is not None:
                b, n = self.mesh_uv_span(k)
                u0, v0, u1, v1 = uv_box
                uv[b:b + n:2] = (uv[b:b + n:2] - u0) / (u1 - u0)
                uv[b + 1:b + n:2] = (uv[b + 1:b + n:2] - v0) / (v1 - v0)

    def uv_bbox(self, meshes):
        uv = self.view("uv.xys")
        us, vs = [], []
        for k in meshes:
            b, n = self.mesh_uv_span(k)
            us.append(uv[b:b + n:2]); vs.append(uv[b + 1:b + n:2])
        us, vs = np.concatenate(us), np.concatenate(vs)
        return float(us.min()), float(vs.min()), float(us.max()), float(vs.max())

    def tint(self, meshes, mul=None, scr=None):
        """Set multiply (RGB, 1 = neutral) and/or screen (RGB, 0 = neutral) colour in every keyform.
        Colour rows can be shared between meshes, so each touched keyform gets its own row only if rows are unique;
        shared rows are reported and edited (affecting all sharers)."""
        if self.version < 4:
            raise ValueError("multiply/screen colours need moc3 v4.02+")
        mrow, srow = self.view("art_mesh_keyform.mul_row"), self.view("art_mesh_keyform.scr_row")
        R, G, B = (self.view(f"color.mul_{c}") for c in "rgb")
        SR, SG, SB = (self.view(f"color.scr_{c}") for c in "rgb")
        rows_m, rows_s = set(), set()
        for k in meshes:
            for f in self.mesh_keyforms(k):
                if mul is not None:
                    r = mrow[f]; R[r], G[r], B[r] = mul; rows_m.add(int(r))
                if scr is not None:
                    r = srow[f]; SR[r], SG[r], SB[r] = scr; rows_s.add(int(r))
        return self.shared_colour_rows(meshes, rows_m, rows_s)

    def shared_colour_rows(self, meshes, rows_m, rows_s):
        mrow, srow = self.view("art_mesh_keyform.mul_row"), self.view("art_mesh_keyform.scr_row")
        mine = {f for k in meshes for f in self.mesh_keyforms(k)}
        nkf = len(self.table("art_mesh_keyform.opacities"))
        others = [f for f in range(nkf) if f not in mine]
        om, os_ = set(mrow[others].tolist()), set(srow[others].tolist())
        return {"mul_rows_shared": sorted(rows_m & om), "scr_rows_shared": sorted(rows_s & os_)}

    def param_index(self, pid):
        return self.param_ids.index(pid)

    def set_param(self, pid, default=None, lo=None, hi=None):
        """Change a parameter's default and/or range. Keys outside a narrowed range simply become unreachable."""
        i = self.param_index(pid)
        if default is not None:
            self.view("parameter.default_values")[i] = default
        if lo is not None:
            self.view("parameter.min_values")[i] = lo
        if hi is not None:
            self.view("parameter.max_values")[i] = hi
        return self.param_info(pid)

    def param_info(self, pid):
        i = self.param_index(pid)
        return {"id": pid, "default": float(self.view("parameter.default_values")[i]),
                "min": float(self.view("parameter.min_values")[i]), "max": float(self.view("parameter.max_values")[i])}

    # ---------- inventory ----------
    def inventory(self, part_names=None, param_names=None):
        part_names = part_names or {}
        param_names = param_names or {}
        mpar = self.table("art_mesh.parent_part_indices")
        tex = self.table("art_mesh.texture_indices")
        nv = self.table("art_mesh.vertex_counts")
        nkf = self.table("art_mesh.keyform_counts")
        dpar = self.table("art_mesh.parent_deformer_indices")
        flags = self.table("art_mesh.drawable_flags")
        meshes = [{"index": k, "id": self.mesh_ids[k], "part": self.part_ids[mpar[k]] if mpar[k] >= 0 else None,
                   "texture": int(tex[k]), "vertices": int(nv[k]), "keyforms": int(nkf[k]),
                   "deformer": self.deformer_ids[dpar[k]] if dpar[k] >= 0 else None, "blend": int(flags[k]) & 3}
                  for k in range(len(self.mesh_ids))]
        counts = {}
        for m in meshes:
            counts[m["part"]] = counts.get(m["part"], 0) + 1
        parts = [{"index": i, "id": p, "name": part_names.get(p, ""),
                  "parent": self.part_ids[self.part_parent[i]] if self.part_parent[i] >= 0 else None,
                  "meshes": counts.get(p, 0)} for i, p in enumerate(self.part_ids)]
        dv, lo, hi = (self.view(f"parameter.{n}") for n in ("default_values", "min_values", "max_values"))
        params = [{"index": i, "id": p, "name": param_names.get(p, ""), "default": float(dv[i]),
                   "min": float(lo[i]), "max": float(hi[i])} for i, p in enumerate(self.param_ids)]
        return {"version": int(self.version), "parts": parts, "params": params, "meshes": meshes}


def part_tree_text(inv):
    """Indented part tree with names and mesh counts."""
    kids = {}
    for p in inv["parts"]:
        kids.setdefault(p["parent"], []).append(p)
    lines = []

    def walk(pid, depth):
        for p in kids.get(pid, []):
            lines.append(f"{'  ' * depth}{p['id']}  {p['name']}  [{p['meshes']}]")
            walk(p["id"], depth + 1)
    walk(None, 0)
    return "\n".join(lines)
