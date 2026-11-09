"""Package only model-referenced resources, never working notes or captures."""
import json
from pathlib import Path
import zipfile

from .model import ModelDir


def resources(model_dir):
    md = ModelDir(model_dir)
    root = md.path.resolve()
    # Preserve the logical entry name when the definition itself is a symlink.
    paths = {root / md.model3_path.name}

    file_keys = {"Moc", "Textures", "Physics", "Pose", "DisplayInfo", "UserData", "File", "Sound", "Model", "Icon"}

    def walk(value, key=None):
        if isinstance(value, dict):
            for field, item in value.items():
                walk(item, field)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, str) and key in file_keys and value:
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"模型资源路径必须位于模型目录内: {value}")
            candidate = root / relative
            if candidate.is_file():
                paths.add(candidate)
            else:
                raise FileNotFoundError(f"模型引用的资源不存在: {value}")

    walk(md.model3.get("FileReferences", {}))
    paths.update(root.glob("*.exp3.json"))
    for config in root.glob("*.vtube.json"):
        paths.add(config)
        walk(json.loads(config.read_text()).get("FileReferences", {}))
    return root, paths


def package_model(model_dir, out):
    root, paths = resources(model_dir)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, Path(root.name) / path.relative_to(root))
    return out
