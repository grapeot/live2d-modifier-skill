import numpy as np

from live2d_modifier.texels import _hue_weight, texel_sets


def test_hue_weight_wraps_and_softens():
    h = np.array([350.0, 5.0, 20.0, 30.0, 180.0])
    w = _hue_weight(h, 340, 10, 15)
    assert w[0] == 1 and w[1] == 1
    assert 0 < w[2] < 1          # 10 degrees outside, inside the soft band
    assert w[3] == 0 and w[4] == 0


def test_texel_sets_union_counts_shared_texels_once():
    quad = {"tex": 0, "uv": [0.0, 1.0, 0.5, 1.0, 0.5, 0.5, 0.0, 0.5], "idx": [0, 1, 2, 0, 2, 3]}
    twin = dict(quad)            # a mirrored partner sharing the same texels
    s = texel_sets([quad, twin], [0, 1], [(16, 16)])
    ys, xs, ids = s[0]
    assert len(ys) == len(set(zip(ys.tolist(), xs.tolist())))
    assert 40 <= len(ys) <= 100  # roughly the 8x8 quarter of the page
