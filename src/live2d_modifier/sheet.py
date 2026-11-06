"""Comparison sheets: rows = models, columns = poses, rendered with the same framing."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .poses import pose
from .render import Renderer, composite


def _font(size=20):
    for f in ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def compare_sheet(models, poses, out, w=600, h=1000, zoom=None, base=None, labels=None, renderer=None, bg=(128, 128, 132)):
    """models: list of model dirs; base: optional {model_dir: params} applied under each pose (e.g. hide the original's
    watermark). Returns the output path."""
    own = renderer is None
    r = renderer or Renderer()
    rows = []
    try:
        for i, m in enumerate(models):
            r.load(m, w, h, zoom=zoom)
            row = []
            for p in poses:
                prm, sim = pose(p, (base or {}).get(str(m), {}))
                im = composite(r.render(prm, sim), bg)
                name = (labels[i] if labels else Path(m).name)
                ImageDraw.Draw(im).text((8, 8), f"{name} | {p}", fill=(255, 255, 0), font=_font(max(14, w // 28)))
                row.append(im)
            rows.append(row)
    finally:
        if own:
            r.close()
    S = Image.new("RGB", (w * len(poses), h * len(models)))
    for i, row in enumerate(rows):
        for j, im in enumerate(row):
            S.paste(im, (j * w, i * h))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    S.save(out, quality=92)
    return out
