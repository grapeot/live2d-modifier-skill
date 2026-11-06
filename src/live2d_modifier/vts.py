"""Install a model folder into VTube Studio as a separate model (self-contained copy, new Name and ModelID)."""
import json
import shutil
import uuid
from pathlib import Path

from .model import ModelDir
from .paths import VTS_MODELS


def install(model_dir, name, vts_dir=None, overwrite=False, template_vtube=None):
    """Copy model_dir (symlinks dereferenced) to <vts_dir>/<name>/ and rewrite *.vtube.json Name/ModelID.
    Refuses to overwrite an existing folder unless overwrite=True, and never overwrites a folder whose vtube.json
    carries a different Name (protects models installed by hand)."""
    vts_dir = Path(vts_dir or VTS_MODELS)
    if not vts_dir.is_dir():
        raise FileNotFoundError(f"VTube Studio model folder not found: {vts_dir}")
    md = ModelDir(model_dir)
    if not name or Path(name).name != name or name in (".", "..") or "\\" in name:
        raise ValueError("模型名称不能包含路径")
    vj = md.vtube_json() or (Path(template_vtube) if template_vtube else None)
    data = json.loads(Path(vj).read_text()) if vj else {}
    dst = vts_dir / name
    if dst.resolve() == md.path.resolve() or dst.resolve() in md.path.resolve().parents:
        raise ValueError("不能覆盖输入模型")
    if dst.exists():
        previous_configs = sorted(dst.glob("*.vtube.json"))
        prev = json.loads(previous_configs[0].read_text()).get("Name") if previous_configs else None
        if not overwrite or (prev is not None and prev != name):
            raise FileExistsError(f"{dst} exists (Name={prev!r}); pass overwrite=True only for folders this tool created")
        shutil.rmtree(dst)
    shutil.copytree(md.path, dst, symlinks=False,
                    ignore=shutil.ignore_patterns("transfer_sources.json", "recolor_sources.json", "*_conflicts.json"))
    target = dst / (vj.name if vj else md.stem + ".vtube.json")
    data["Name"] = name
    data["ModelID"] = uuid.uuid4().hex
    fr = data.setdefault("FileReferences", {})
    fr["Model"] = md.model3_path.name
    target.write_text(json.dumps(data, ensure_ascii=False, indent=4))
    return dst, data["ModelID"]
