"""Validate a moc3 with the real Cubism Core (Node): consistency check, load, optional diff against a reference."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .paths import core_path

JS = Path(__file__).with_name("js") / "check.js"


def check_moc(path, ref=None, timeout=300):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found on PATH (needed to run Cubism Core checks)")
    path = Path(path)
    if path.is_dir():
        from .model import ModelDir
        md = ModelDir(path)
        references = []
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("Moc", "Textures", "Physics", "Pose", "DisplayInfo", "UserData", "File", "Sound"):
                        if isinstance(item, str):
                            references.append(item)
                        elif isinstance(item, list):
                            references.extend(x for x in item if isinstance(x, str))
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(md.model3.get("FileReferences", {}))
        for item in references:
            if Path(item).is_absolute() or ".." in Path(item).parts or not (md.path / item).is_file():
                raise ValueError(f"缺失或越界的模型资源: {item}")
        path = md.moc_path
    cmd = [node, "--max-old-space-size=8192", str(JS), str(path)] + ([str(ref)] if ref else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "L2D_CORE_PATH": str(core_path())})
    lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if r.returncode != 0 or not lines:
        raise RuntimeError(f"check.js failed (exit {r.returncode}): {r.stderr[-2000:]} {r.stdout[-1000:]}")
    result = json.loads(lines[-1])
    if not result.get("consistency") or not result.get("loaded"):
        raise RuntimeError(f"Cubism validation failed: {result}")
    return result
