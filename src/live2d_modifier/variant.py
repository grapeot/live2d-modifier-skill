"""Build a model variant folder from a source model plus a JSON recipe of in-place moc3 edits.

Recipe schema (all keys optional, applied in this order):
{
  "params":  [{"id": "ParamExample", "default": 1, "min": 0, "max": 1}],
  "hide":    {"parts": ["PartExample"], "meshes": ["ArtMeshExample"], "patterns": ["MeshGroup*"]},
  "affine":  [{"parts": [...], "meshes": [...], "sx": 1, "sy": 1.3, "dx": 0, "dy": 0, "pivot": "top"}],
  "warp":    [{"deformer": "WarpExample", "sx": 1, "sy": 1.2, "dx": 0, "dy": 0, "pivot": "top"}],
  "unmask":  {"meshes": [...]},                       # drop clipping masks
  "blend":   [{"meshes": [...], "mode": 0}],          # 0 normal, 1 additive, 2 multiply
  "tint":    [{"parts": [...], "mul": [r, g, b], "scr": [r, g, b]}],
  "texture": [{"parts": [...], "page": "pages/acc.png", "uv_box": "auto" | [u0, v0, u1, v1] | null}],
  "split":   [{"groups": [{"meshes": [...]}, {"parts": [...]}]}]   # copy each group's atlas box to one new page
}
Unchanged texture files are symlinked; use `vts install` (which dereferences) to get a self-contained folder.
"""
import json
import os
import shutil
from pathlib import Path

import numpy as np

from .model import ModelDir, next_texture_name
from .moc import Moc


def clone_model(src, dst, link_textures=True):
    """Copy a model folder; texture pages are symlinked (to the resolved original) unless link_textures=False."""
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if src == dst or dst.is_relative_to(src) or src.is_relative_to(dst):
        raise ValueError("源和目标目录不能相同、包含或重叠")
    if dst.exists():
        raise FileExistsError(f"目标已存在，请换一个新的输出目录: {dst}")
    md = ModelDir(src)
    tex = {t.resolve() for t in md.textures}
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        out = dst / rel
        if f.is_dir():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if link_textures and f.resolve() in tex:
            os.symlink(f.resolve(), out)
        else:
            shutil.copy2(f.resolve(), out)
            os.chmod(out, 0o644)
    return dst


def _sel(moc, spec):
    return moc.select_meshes(spec.get("meshes", ()), spec.get("parts", ()), spec.get("patterns", ()))


def apply_recipe(moc: Moc, recipe: dict, model_dir=None, log=print):
    """Apply recipe edits to an open Moc. Texture-page ops also need model_dir (the destination folder)."""
    for p in recipe.get("params", []):
        log("param", moc.set_param(p["id"], p.get("default"), p.get("min"), p.get("max")))
    if "hide" in recipe:
        ms = _sel(moc, recipe["hide"])
        log("hide", moc.hide(ms), "meshes")
    for a in recipe.get("affine", []):
        ms = _sel(moc, a)
        g = moc.affine_meshes(ms, a.get("sx", 1), a.get("sy", 1), a.get("dx", 0), a.get("dy", 0),
                              tuple(a["pivot"]) if isinstance(a.get("pivot"), list) else a.get("pivot", "center"),
                              a.get("per_deformer", True))
        log("affine", len(ms), "meshes in", len(g), "deformer spaces")
    for w in recipe.get("warp", []):
        blocks = moc.warp_position_blocks(w["deformer"])
        pos = moc.view("keyform_position.xys")
        xs = np.concatenate([pos[o:o + n:2] for o, n in blocks]); ys = np.concatenate([pos[o + 1:o + n:2] for o, n in blocks])
        piv = w.get("pivot", "center")
        if isinstance(piv, list):
            px, py = piv
        else:
            px = float(xs.mean()); py = {"center": float(ys.mean()), "top": float(ys.min()), "bottom": float(ys.max())}[piv]
        sx, sy, dx, dy = w.get("sx", 1), w.get("sy", 1), w.get("dx", 0), w.get("dy", 0)
        moc.transform_blocks(blocks, lambda xy: np.stack([px + (xy[:, 0] - px) * sx + dx, py + (xy[:, 1] - py) * sy + dy], 1))
        log("warp", w["deformer"], len(blocks), "keyforms")
    if "unmask" in recipe:
        log("unmask", moc.unmask(_sel(moc, recipe["unmask"])), "meshes")
    for b in recipe.get("blend", []):   # 0 normal, 1 additive, 2 multiply
        ms = _sel(moc, b)
        f = moc.view("art_mesh.drawable_flags")
        for k in ms:
            f[k] = (int(f[k]) & ~3) | int(b["mode"])
        log("blend", len(ms), "meshes ->", b["mode"])
    for t in recipe.get("tint", []):
        ms = _sel(moc, t)
        shared = moc.tint(ms, t.get("mul"), t.get("scr"))
        log("tint", len(ms), "meshes", shared)
    for t in recipe.get("texture", []):
        if model_dir is None:
            raise ValueError("texture ops need model_dir")
        add_texture_page(moc, model_dir, _sel(moc, t), t["page"], t.get("uv_box", "auto"), log)
    for sp in recipe.get("split", []):
        from .atlas import split_to_page
        if model_dir is None:
            raise ValueError("split ops need model_dir")
        split_to_page(moc, model_dir, [_sel(moc, g) for g in sp["groups"]], log=log)


def add_texture_page(moc, model_dir, meshes, page_src, uv_box="auto", log=print):
    """Append a texture page to model3.json and point meshes at it.
    uv_box='auto': the meshes' current atlas bbox becomes the whole new page (the page should be drawn for that box,
    e.g. from `l2d-mod page-template`). None keeps UVs (page must share the atlas layout)."""
    md = ModelDir(model_dir)
    m3 = md.model3
    texs = m3["FileReferences"]["Textures"]
    name = next_texture_name(model_dir)
    out = Path(model_dir) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(page_src, "rb") as source, open(out, "xb") as target:
        shutil.copyfileobj(source, target)
    texs.append(name)
    md.model3_path.write_text(json.dumps(m3, ensure_ascii=False, indent="\t"))
    box = moc.uv_bbox(meshes) if uv_box == "auto" else uv_box
    moc.set_texture(meshes, len(texs) - 1, box)
    log("texture page", name, "meshes", len(meshes), "uv_box", box)
    return len(texs) - 1, box


def build_variant(src, dst, recipe, log=print):
    src = Path(src)
    if isinstance(recipe, (str, Path)):
        recipe = json.loads(Path(recipe).read_text())
    clone_model(src, dst)
    md = ModelDir(dst)
    moc = Moc(ModelDir(src).moc_path)
    apply_recipe(moc, recipe, dst, log)
    moc_dst = md.moc_path
    if moc_dst.is_symlink():
        moc_dst.unlink()
    moc.save(moc_dst)
    return dst
