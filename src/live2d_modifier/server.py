"""In-process static HTTP server for the viewer. Model folders are mounted under /m/<key>/ so they can live anywhere.
Defaults to port 8798 and falls back to a free port; never touches other servers."""
import functools
import http.server
import socket
import threading
import urllib.parse
from pathlib import Path

from .paths import DEFAULT_PORT, VIEWER, runtime_assets


class _Handler(http.server.SimpleHTTPRequestHandler):
    mounts: dict = {}
    assets: dict = {}

    def translate_path(self, path):
        p = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        if p.startswith("/vendor/"):
            asset = self.assets.get(p.removeprefix("/vendor/"))
            return str(asset) if asset else str(VIEWER / "__missing__")
        if p.startswith("/m/"):
            key, _, rest = p[3:].partition("/")
            base = self.mounts.get(key)
            if base is not None:
                # Generated capture models intentionally link original texture pages.
                # Reject URL traversal, but allow links explicitly present in a mounted model.
                relative = Path(rest)
                if not relative.is_absolute() and ".." not in relative.parts:
                    return str(Path(base) / relative)
            return str(VIEWER / "__missing__")
        if p in ("/", "/viewer/index.html"):
            return str(VIEWER / "index.html")
        return str(VIEWER / "__missing__")

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, *a):
        pass


class Server:
    def __init__(self, port=None):
        self.mounts = {}
        handler = type("H", (_Handler,), {"mounts": self.mounts, "assets": runtime_assets()})
        handler = functools.partial(handler, directory=str(VIEWER))
        port = DEFAULT_PORT if port is None else port
        try:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def mount(self, model_dir) -> str:
        """Return the URL prefix serving model_dir."""
        model_dir = Path(model_dir).resolve()
        key = str(abs(hash(str(model_dir))))
        self.mounts[key] = model_dir
        return f"{self.base}/m/{key}/"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def port_free(port) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0
