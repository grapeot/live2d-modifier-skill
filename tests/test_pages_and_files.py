import json

import numpy as np
import pytest

from live2d_modifier.model import ModelDir, apply_expression, load_expression
from live2d_modifier.pages import matte_flat_bg
from live2d_modifier.pages import test_pattern as quad_pattern
from live2d_modifier.poses import parse_params, pose
from live2d_modifier.variant import clone_model
from live2d_modifier.vts import install


def test_matte_recovers_alpha_and_colour():
    bg = np.array([28, 36, 25], np.float32)
    H = W = 64
    yy, xx = np.mgrid[0:H, 0:W]
    a = np.clip(20 - np.hypot(yy - 32, xx - 32), 0, 1)   # disc with a 1 px soft edge
    fg = np.array([230, 220, 200], np.float32)
    img = (a[..., None] * fg + (1 - a[..., None]) * bg).astype(np.uint8)
    out = matte_flat_bg(img)
    assert out[32, 32, 3] == 255 and out[0, 0, 3] == 0
    edge = (a > 0.3) & (a < 0.9)
    assert np.abs(out[edge, :3].astype(float) - fg).max() < 25   # decontaminated: no dark halo


def test_test_pattern_quadrants():
    p = quad_pattern(8)
    assert tuple(p[0, 0, :3]) == (255, 0, 0) and tuple(p[0, 7, :3]) == (0, 200, 0) and tuple(p[7, 0, :3]) == (0, 0, 255)


def test_model_dir_and_expressions(fake_model):
    md = ModelDir(fake_model)
    assert md.stem == "a" and md.textures[0].name == "texture_00.png"
    exp = load_expression(md.expressions()["smile"])
    out = apply_expression({"P2": 3}, {"P1": 1}, exp)
    assert out == {"P1": 1.5, "P2": 6, "P3": 7}


def test_parse_params_merges(tmp_path):
    f = tmp_path / "p.json"; f.write_text(json.dumps({"A": 1, "B": 2}))
    assert parse_params([str(f), '{"B": 3}', "C=0.5"]) == {"A": 1, "B": 3, "C": 0.5}
    prm, sim = pose("sway", {"A": 1})
    assert prm["A"] == 1 and sim and sim[0]["n"] > 0


def test_clone_links_textures_only(fake_model, tmp_path):
    dst = clone_model(fake_model, tmp_path / "c")
    assert (dst / "tex/texture_00.png").is_symlink() and not (dst / "a.moc3").is_symlink()


def test_vts_install_rewrites_identity_and_protects_foreign(fake_model, tmp_path):
    vts = tmp_path / "vts"; vts.mkdir()
    dst, mid = install(fake_model, "new_one", vts_dir=vts)
    d = json.loads((dst / "a.vtube.json").read_text())
    assert d["Name"] == "new_one" and d["ModelID"] == mid and len(mid) == 32
    assert not any(p.is_symlink() for p in dst.rglob("*"))
    with pytest.raises(FileExistsError):
        install(fake_model, "new_one", vts_dir=vts)
    install(fake_model, "new_one", vts_dir=vts, overwrite=True)
    foreign = vts / "theirs"; foreign.mkdir()
    (foreign / "x.vtube.json").write_text(json.dumps({"Name": "someone else"}))
    with pytest.raises(FileExistsError):
        install(fake_model, "theirs", vts_dir=vts, overwrite=True)
