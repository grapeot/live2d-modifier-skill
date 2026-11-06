import argparse
import json

import numpy as np
import pytest
from PIL import Image

from live2d_modifier.atlas import find_conflicts, split_to_page
from live2d_modifier.cli import _params
from live2d_modifier.corr import owner_maps
from live2d_modifier.delivery import package_model
from live2d_modifier.model import ModelDir
from live2d_modifier.transfer import Transfer
from live2d_modifier.variant import add_texture_page, clone_model


def quad(order=0):
    return {"id": "MeshExample", "tex": 0, "op": 1, "order": order,
            "uv": [0, 0, 1, 0, 1, 1, 0, 1], "idx": [0, 1, 2, 0, 2, 3],
            "pos": [0, 0, 1, 0, 1, 1, 0, 1]}


def test_new_page_never_follows_original_texture_link(fake_model, tmp_path):
    config = fake_model / "a.model3.json"
    data = json.loads(config.read_text())
    texture = fake_model / "page_01.png"
    Image.new("RGBA", (16, 16), (20, 50, 100, 255)).save(texture)
    data["FileReferences"]["Textures"] = [texture.name]
    config.write_text(json.dumps(data))
    before = texture.read_bytes()
    dst = clone_model(fake_model, tmp_path / "copy")

    class MocStub:
        uv = np.array(quad()["uv"], np.float32)
        texture_indices = np.array([0], np.int32)
        def set_texture(self, *args): pass
        def table(self, name): return self.texture_indices
        def uv_bbox(self, group): return (0, 0, 1, 1)
        def view(self, name): return self.uv if name == "uv.xys" else self.texture_indices
        def mesh_uv_span(self, mesh): return (0, 8)

    moc = MocStub()
    add_texture_page(moc, dst, [0], texture, uv_box=None, log=lambda *a: None)
    split_to_page(moc, dst, [[0]], page_size=32, pad=0, log=lambda *a: None)
    assert texture.read_bytes() == before
    names = ModelDir(dst).model3["FileReferences"]["Textures"]
    assert len(names) == len(set(names)) == 3


def test_owner_cache_tracks_size_order_and_topology(fake_model, tmp_path):
    md = ModelDir(fake_model)
    texture = md.textures[0]
    Image.new("RGBA", (16, 16), "white").save(texture)
    a = owner_maps(fake_model, [quad()], tmp_path / "cache")[0]
    Image.new("RGBA", (32, 32), "white").save(texture)
    b = owner_maps(fake_model, [quad()], tmp_path / "cache")[0]
    assert a.shape == (16, 16) and b.shape == (32, 32)
    c = owner_maps(fake_model, [quad(1), quad(0)], tmp_path / "cache")[0]
    d = owner_maps(fake_model, [quad(0), quad(1)], tmp_path / "cache")[0]
    assert c[15, 15] == 0 and d[15, 15] == 1
    triangle = quad()
    triangle["idx"] = [0, 1, 2]
    e = owner_maps(fake_model, [triangle], tmp_path / "cache")[0]
    assert np.count_nonzero(e == -1) > 0


def test_conflicts_lists_all_three_shared_pairs(fake_model):
    Image.new("RGBA", (16, 16), "white").save(ModelDir(fake_model).textures[0])
    pairs = find_conflicts(fake_model, [quad(), quad(), quad()], min_texels=1)
    assert {(a, b) for a, b, _ in pairs} == {(0, 1), (0, 2), (1, 2)}


@pytest.mark.parametrize("scale,expected", [(0, 0), (0.5, 127), (1, 255)])
def test_alpha_zero_is_valid(fake_model, tmp_path, monkeypatch, scale, expected):
    import live2d_modifier.transfer as module
    tr = Transfer.__new__(Transfer)
    tr.src, tr.D, tr.caps = ModelDir(fake_model), [quad()], []
    Image.new("RGBA", (16, 16), "white").save(tr.src.textures[0])
    tr.maps, tr.source, tr.alpha_scale = {0: np.zeros((24, 3))}, {}, {0: scale}
    tr.log = lambda *args: None
    w, h = Image.open(tr.src.textures[0]).size
    monkeypatch.setattr(module, "owner_maps", lambda *a, **k: [np.zeros((h, w), np.int16)])
    out = tmp_path / f"alpha_{scale}"
    tr.write(out)
    result = np.asarray(Image.open(ModelDir(out).textures[0]))
    assert np.all(result[..., 3] == expected)


def test_package_missing_resource_fails_before_creating_archive(fake_model, tmp_path):
    config = fake_model / "a.model3.json"
    data = json.loads(config.read_text())
    data["FileReferences"]["Physics"] = "missing.physics3.json"
    config.write_text(json.dumps(data))
    out = tmp_path / "bad.zip"
    with pytest.raises(FileNotFoundError):
        package_model(fake_model, out)
    assert not out.exists()


def test_cli_expression_uses_model_defaults(fake_model, tmp_path, monkeypatch):
    from live2d_modifier.moc import Moc
    monkeypatch.setattr(Moc, "__init__", lambda *a: None)
    monkeypatch.setattr(Moc, "inventory", lambda self: {"params": [{"id": "P", "default": 1}]})
    exp = tmp_path / "exp.json"
    exp.write_text(json.dumps({"Parameters": [{"Id": "P", "Value": 0.5, "Blend": "Add"}]}))
    assert _params(argparse.Namespace(params=[], expr=[str(exp)]), ModelDir(fake_model)) == {"P": 1.5}
