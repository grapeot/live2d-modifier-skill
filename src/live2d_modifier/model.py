"""Model directory helpers: model3.json, textures, expressions, display names."""
import json
from functools import cached_property
from pathlib import Path


class ModelDir:
    def __init__(self, path):
        self.path = Path(path)
        cands = sorted(self.path.glob("*.model3.json"))
        if len(cands) != 1:
            raise ValueError(f"{self.path}: expected exactly one *.model3.json, found {len(cands)}")
        self.model3_path = cands[0]

    @cached_property
    def model3(self):
        return json.loads(self.model3_path.read_text())

    @property
    def stem(self):
        return self.model3_path.name[: -len(".model3.json")]

    @property
    def moc_path(self):
        return self.path / self.model3["FileReferences"]["Moc"]

    @property
    def textures(self):
        return [self.path / t for t in self.model3["FileReferences"]["Textures"]]

    @cached_property
    def cdi(self):
        ref = self.model3["FileReferences"].get("DisplayInfo")
        if ref and (self.path / ref).exists():
            return json.loads((self.path / ref).read_text())
        return {}

    @cached_property
    def param_names(self):
        return {p["Id"]: p.get("Name", "") for p in self.cdi.get("Parameters", [])}

    @cached_property
    def part_names(self):
        return {p["Id"]: p.get("Name", "") for p in self.cdi.get("Parts", [])}

    def expressions(self):
        """name -> path for every *.exp3.json in the folder (VTube Studio style models keep them loose)."""
        return {p.name[: -len(".exp3.json")]: p for p in sorted(self.path.glob("*.exp3.json"))}

    def vtube_json(self):
        c = sorted(self.path.glob("*.vtube.json"))
        return c[0] if c else None


def next_texture_name(model_dir):
    """Allocate an unused page name; symlinks, including dangling links, are occupied."""
    md = ModelDir(model_dir)
    textures = md.model3["FileReferences"]["Textures"]
    folder = Path(textures[0]).parent if textures else Path(".")
    index = len(textures)
    while True:
        relative = folder / f"page_{index:02d}.png"
        target = md.path / relative
        if str(relative) not in textures and not target.exists() and not target.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(relative)
        index += 1


def load_expression(path):
    """exp3.json -> list of (param_id, value, blend). Blend is Add / Multiply / Overwrite."""
    d = json.loads(Path(path).read_text())
    return [(p["Id"], float(p["Value"]), p.get("Blend", "Add")) for p in d.get("Parameters", [])]


def apply_expression(params, defaults, exp):
    """Combine an expression onto absolute params (Cubism semantics relative to the current value)."""
    out = dict(params)
    for pid, v, blend in exp:
        cur = out.get(pid, defaults.get(pid, 0.0))
        if blend == "Add":
            out[pid] = cur + v
        elif blend == "Multiply":
            out[pid] = cur * v
        else:
            out[pid] = v
    return out
