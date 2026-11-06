"""Headless Chrome renderer (Playwright + pixi 8 + untitled-pixi-live2d-engine) for deterministic stills."""
import base64
import io
import os
import sys
import urllib.parse

from PIL import Image

from .model import ModelDir
from .server import Server

CHROME_ARGS = ["--use-angle=metal" if sys.platform == "darwin" else "--use-angle=swiftshader",
               "--enable-gpu", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"]


class Renderer:
    """Keeps one browser page open; load() a model, then render() many poses."""

    def __init__(self, port=None, headless=True):
        from playwright.sync_api import sync_playwright
        self.server = Server(port)
        self._pw = sync_playwright().start()
        channel = os.environ.get("L2D_CHROME_CHANNEL", "")
        try:
            self.browser = self._pw.chromium.launch(channel=channel or None, headless=headless, args=CHROME_ARGS)
        except Exception:
            self.browser = self._pw.chromium.launch(headless=headless, args=CHROME_ARGS)
        self.page = self.browser.new_page(viewport={"width": 800, "height": 800})
        self.console = []
        self.page.on("console", lambda m: self.console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        self.page.goto(f"{self.server.base}/viewer/index.html")
        self.page.wait_for_function("window.READY === true")
        self.info = None

    def load(self, model_dir, w, h, zoom=None, nearest=False, fit=None):
        md = ModelDir(model_dir)
        url = self.server.mount(md.path) + urllib.parse.quote(md.model3_path.name)
        opts = {"nearest": bool(nearest)}
        if fit:
            opts["fit"] = fit
        if zoom:
            opts.update({"zoom": zoom[0], "cx": zoom[1], "cy": zoom[2]})
        self.info = self.page.evaluate("([u,w,h,o]) => L2D.init(u,w,h,o)", [url, w, h, opts])
        self.size = (w, h)
        return self.info

    def render(self, params=None, sim=None, settle=60) -> Image.Image:
        """settle: physics steps (1/30 s) holding the pose; 0 disables physics entirely."""
        data = self.page.evaluate("([p,s,n]) => L2D.render(p,s,n)", [params or {}, sim, settle])
        return Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGBA")

    def drawables(self):
        return self.page.evaluate("() => L2D.drawables()")

    def params(self):
        return self.page.evaluate("() => L2D.listParams()")

    def close(self):
        try:
            self.browser.close()
            self._pw.stop()
        finally:
            self.server.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def composite(img: Image.Image, bg=(128, 128, 132)) -> Image.Image:
    base = Image.new("RGBA", img.size, tuple(bg) + (255,))
    base.alpha_composite(img.convert("RGBA"))
    return base.convert("RGB")
