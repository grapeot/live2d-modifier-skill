"""New texture pages: matting generated art drawn on a flat background, and test patterns.

GPT Image cannot emit transparency through the workspace CLI, so new art is generated on a known flat colour and
un-composited here: alpha comes from the colour distance to the background (soft only in a thin edge band, solid inside
the filled object mask), and colour is decontaminated with fg = (C - (1 - a) * bg) / a, so no background halo remains.
Pick a background close to whatever the art will sit on (e.g. the hair colour) to make residual errors invisible.
"""
import cv2
import numpy as np
from PIL import Image


def border_colour(rgb, width=8):
    b = np.concatenate([rgb[:width].reshape(-1, 3), rgb[-width:].reshape(-1, 3),
                        rgb[:, :width].reshape(-1, 3), rgb[:, -width:].reshape(-1, 3)])
    return np.median(b, 0)


def matte_flat_bg(img, bg=None, t_lo=8.0, t_hi=30.0, band=3):
    """img: RGB array or PIL image on a flat background -> RGBA uint8 array."""
    rgb = np.asarray(img.convert("RGB") if isinstance(img, Image.Image) else img).astype(np.float32)
    bg = border_colour(rgb) if bg is None else np.asarray(bg, np.float32)
    d = np.linalg.norm(rgb - bg, axis=2)
    obj = (d > (t_lo + t_hi) / 2).astype(np.uint8)
    # fill holes: background is what is connected to the border
    ff = (1 - obj).copy()
    h, w = ff.shape
    mask = np.zeros((h + 2, w + 2), np.uint8)
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if ff[y, x]:
            cv2.floodFill(ff, mask, (x, y), 2)
    solid = (ff != 2).astype(np.uint8)
    inner = cv2.erode(solid, np.ones((2 * band + 1, 2 * band + 1), np.uint8))
    edge = cv2.dilate(solid, np.ones((3, 3), np.uint8)) & ~inner.astype(bool)
    # local foreground colour F for edge pixels: interior colours spread outward (normalised blur)
    sig = 2.0 * band
    wi = inner.astype(np.float32)
    F = cv2.GaussianBlur(rgb * wi[..., None], (0, 0), sig) / np.maximum(cv2.GaussianBlur(wi, (0, 0), sig), 1e-4)[..., None]
    fb = F - bg
    # alpha = projection of (C - bg) onto (F - bg); falls back to the distance ramp where F is unknown
    proj = np.sum((rgb - bg) * fb, 2) / np.maximum(np.sum(fb * fb, 2), 1e-3)
    ramp = np.clip((d - t_lo) / (t_hi - t_lo), 0, 1)
    known = cv2.GaussianBlur(wi, (0, 0), sig) > 1e-3
    a_edge = np.where(known, np.clip(proj, 0, 1), ramp)
    a = np.where(inner > 0, 1.0, np.where(edge, a_edge, 0.0))
    fg = (rgb - (1 - a[..., None]) * bg) / np.maximum(a[..., None], 1e-3)
    out = np.dstack([np.clip(fg, 0, 255), a * 255]).astype(np.uint8)
    out[a <= 0.003] = 0
    return out


def test_pattern(size=512):
    """Quadrant pattern (red TL, green TR, blue BL, white BR) for checking page orientation on a mesh."""
    p = np.full((size, size, 4), 255, np.uint8)
    h = size // 2
    p[:h, :h, :3] = (255, 0, 0); p[:h, h:, :3] = (0, 200, 0); p[h:, :h, :3] = (0, 0, 255)
    return p
