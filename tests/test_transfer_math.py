import numpy as np

from live2d_modifier.transfer import Transfer, apply_lut, bilinear, fit_lut, lab, map_disagreement, rgb_from_lab


def test_lab_roundtrip():
    rgb = np.random.default_rng(0).integers(0, 256, (500, 3)).astype(np.float32)
    assert np.abs(rgb_from_lab(lab(rgb)) - rgb).max() < 1.5


def test_lut_recovers_luminance_dependent_shift():
    rng = np.random.default_rng(1)
    src = np.stack([rng.uniform(5, 95, 4000), rng.uniform(-10, 10, 4000), rng.uniform(-10, 10, 4000)], 1).astype(np.float32)
    dst = src.copy(); dst[:, 0] = 100 - src[:, 0]; dst[:, 1] += 20   # invert luminance, push a* toward red
    tab = fit_lut(src, dst)
    out = apply_lut(src, tab)
    assert np.abs(out[:, 0] - dst[:, 0]).mean() < 3 and np.abs(out[:, 1] - dst[:, 1]).mean() < 1


def test_lut_none_without_samples():
    assert fit_lut(np.zeros((3, 3), np.float32), np.zeros((3, 3), np.float32)) is None


def test_map_disagreement_treats_missing_as_identity():
    t = np.zeros((24, 3), np.float32); t[:, 1] = 10
    assert map_disagreement(t, None) == 10 and map_disagreement(None, None) == 0


def test_bilinear():
    img = np.arange(16, dtype=np.float32).reshape(4, 4)
    assert np.isclose(bilinear(img, np.array([1.5]), np.array([2.0]))[0], 9.5)


def test_texel_to_pos_is_barycentric():
    """A single triangle: texel centres map to the affine image of the UV triangle in model space."""
    tr = Transfer.__new__(Transfer)
    tr.page_size = [(100, 100)]
    d = {"tex": 0, "uv": [0.1, 0.9, 0.9, 0.9, 0.1, 0.1], "idx": [0, 1, 2], "pos": [0, 0, 2, 0, 0, 4]}
    c = type("C", (), {"D": [d]})()
    xs, ys = np.array([10, 50, 20]), np.array([10, 30, 60])
    pos = tr._texel_to_pos(c, 0, xs, ys, {})
    # uv (u, 1-v)*100 -> texel; texel (10,10) is the first vertex; x scales 2/80 per texel, y 4/80
    exp = np.stack([(xs + 0.5 - 10) * 2 / 80, (ys + 0.5 - 10) * 4 / 80], 1)
    assert np.allclose(pos, exp, atol=1e-6)


def test_residual_hole_mask_carries_polygon_between_captures():
    # ref capture: pixel = 2 * model + 10; other capture: pixel = 1 * model + 0 (half the zoom, shifted)
    tr = Transfer.__new__(Transfer)
    tr.residual_holes = {"ref": "head", "polys": [[[30, 30], [50, 30], [50, 50], [30, 50]]], "dilate": 0, "feather": 2}
    M_ref = np.array([[2, 0], [0, 2], [10, 10]], np.float64)
    M_body = np.array([[1, 0], [0, 1], [0, 0]], np.float64)

    class C:
        alpha = np.zeros((40, 40), np.float32)

    hole = tr._hole_mask(C, M_body, {"head": M_ref, "body": M_body})
    # ref square (30..50) is model 10..20, so the body mask covers pixels 10..20 and fades out within ~1 px
    assert hole[15, 15] == 1 and hole[5, 5] == 0 and hole[15, 25] == 0
    tr.residual_holes = None
    assert tr._hole_mask(C, M_body, {"head": M_ref}) is None
