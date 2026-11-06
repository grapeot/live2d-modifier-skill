import numpy as np

from live2d_modifier.codes import MAX_PAGES, decode, encode_page
from live2d_modifier.corr import decode_ids, id_colour


def test_code_roundtrip_all_pages():
    alpha = np.full((64, 8192), 255, np.uint8)[:, :64]
    for k in range(MAX_PAGES):
        hi, lo = encode_page(alpha, k, "hi"), encode_page(alpha, k, "lo")
        kk, x, y, ok = decode(hi, lo)
        assert ok.all() and (kk == k).all()
        ys, xs = np.mgrid[0:64, 0:64]
        assert (x == xs).all() and (y == ys).all()


def test_code_roundtrip_large_coords():
    # sample the far corner of an 8192 page without allocating it
    xs = np.array([8191, 4097, 31, 32]); ys = np.array([8191, 5, 8000, 33])
    hi = np.stack([xs >> 5, ys >> 5, np.full(4, 28 * 3 + 14), np.full(4, 255)], -1)
    lo = np.stack([(xs & 31) * 8 + 4, (ys & 31) * 8 + 4, np.full(4, 28 * 3 + 14), np.full(4, 255)], -1)
    k, x, y, ok = decode(hi, lo)
    assert ok.all() and (k == 3).all() and (x == xs).all() and (y == ys).all()


def test_code_tolerates_small_noise():
    alpha = np.full((8, 8), 255, np.uint8)
    hi, lo = encode_page(alpha, 2, "hi").astype(int), encode_page(alpha, 2, "lo").astype(int)
    lo[..., :3] += 3   # filtering/rounding noise below half a step
    hi[..., 2] -= 5
    k, x, y, ok = decode(hi, lo)
    assert ok.all() and (k == 2).all() and (x == np.arange(8)[None]).all()


def test_white_pages_keep_or_binarise_alpha():
    a = np.array([[0, 100, 128, 255]], np.uint8)
    assert (encode_page(a, 0, "white")[..., 3] == a).all()
    assert encode_page(a, 0, "whitebin")[..., 3].tolist() == [[0, 0, 255, 255]]


def test_id_colour_roundtrip_and_rejects_background():
    ks = np.arange(0, 2000, 7)
    rgba = np.array([list(id_colour(k)) + [255] for k in ks])[None]
    assert (decode_ids(rgba)[0] == ks).all()
    bg = np.array([[[128, 128, 132, 255], [0, 0, 0, 0]]])
    assert (decode_ids(bg) == -1).all()
