"""End-to-end texture writer tests with synthetic captures, no model/SDK assets."""
from types import SimpleNamespace

import numpy as np
from PIL import Image

from live2d_modifier.model import ModelDir
from live2d_modifier.transfer import Transfer


def test_spatial_gradient_pattern_and_hole_roundtrip(fake_model, tmp_path, monkeypatch):
    import live2d_modifier.transfer as module
    size = 64
    y, x = np.mgrid[:size, :size]
    original = np.full((size, size, 3), 180, dtype=np.float32)
    edit = original.copy()
    edit[..., 0] = 30 + x * 3
    edit[..., 1] = 160 - y
    edit[20:32, 18:42] = [240, 190, 70]
    md = ModelDir(fake_model)
    Image.fromarray(np.dstack([original.astype(np.uint8), np.full((size, size), 255, np.uint8)])).save(md.textures[0])
    drawable = {"tex": 0, "op": 1, "order": 0, "uv": [0, 0, 1, 0, 1, 1, 0, 1],
                "idx": [0, 1, 2, 0, 2, 3], "pos": [0, size, size, size, size, 0, 0, 0]}
    capture = SimpleNamespace(
        prefix="body", D=[drawable], residual=True, alpha=np.full((size, size), 255),
        base=np.zeros((size, size), np.int32), orig_rgb=original, edit_rgb=edit,
        C={"valid": np.ones(size * size, bool), "py": y.ravel(), "px": x.ravel(),
           "x": x.ravel(), "y": y.ravel(), "drw": np.zeros(size * size, int)})
    tr = Transfer.__new__(Transfer)
    tr.src, tr.D, tr.caps = md, [drawable], [capture]
    tr.maps, tr.source = {0: np.zeros((24, 3), np.float32)}, {}
    tr.log = lambda *a: None
    monkeypatch.setattr(module, "owner_maps", lambda *a, **kw: [np.zeros((size, size), np.int16)])
    first, second = tmp_path / "first", tmp_path / "second"
    stats = tr.write(first)
    tr.write(second)
    a = np.asarray(Image.open(ModelDir(first).textures[0]))
    assert stats["residual"] > 3000
    assert a[40, 50, 0] > a[40, 10, 0] + 80  # position-dependent gradient survived
    assert a[26, 28, 0] > 220 and a[26, 28, 2] < 90  # gold motif survived
    assert ModelDir(first).textures[0].read_bytes() == ModelDir(second).textures[0].read_bytes()
    tr.residual_holes = {"ref": "body", "polys": [[[16,18],[44,18],[44,34],[16,34]]], "feather": 1}
    holes = tmp_path / "holes"
    tr.write(holes)
    result = np.asarray(Image.open(ModelDir(holes).textures[0]))
    assert np.abs(result[26, 28, :3].astype(int) - 180).max() <= 2
    assert np.array_equal(a[45, 45], result[45, 45])
