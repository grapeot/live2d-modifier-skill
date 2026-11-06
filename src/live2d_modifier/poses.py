"""Named pose presets for verification renders. Values are absolute parameter values on top of a base dict."""
import json
from pathlib import Path

ANGLE_L = {"ParamAngleX": 30, "ParamAngleY": 10, "ParamAngleZ": -8, "ParamBodyAngleX": 8}
ANGLE_R = {"ParamAngleX": -30, "ParamAngleY": -5, "ParamAngleZ": 8, "ParamBodyAngleX": -8}

POSES = {
    "front": ({}, None),
    "blink": ({"ParamEyeLOpen": 0, "ParamEyeROpen": 0}, None),
    "talk": ({"ParamMouthOpenY": 1, "ParamMouthForm": 1}, None),
    "smile": ({"ParamEyeLSmile": 1, "ParamEyeRSmile": 1, "ParamEyeLOpen": 0, "ParamEyeROpen": 0,
               "ParamMouthForm": 1, "ParamMouthOpenY": 0.6}, None),
    "turn_l": (ANGLE_L, None),
    "turn_r": (ANGLE_R, None),
    "look_up": ({"ParamAngleY": 30, "ParamEyeBallY": 0.6}, None),
    # physics: hold one side, snap to the other, capture mid-swing
    "sway": ({}, [{"params": {**ANGLE_R, "ParamBodyAngleX": -10, "ParamBodyAngleZ": -10}, "dt": 1 / 30, "n": 60},
                 {"params": {**ANGLE_L, "ParamBodyAngleX": 10, "ParamBodyAngleZ": 10}, "dt": 1 / 30, "n": 9}]),
}


def parse_params(specs, presets_dir=None):
    """Merge param specs: JSON files, inline JSON objects, or k=v pairs (later wins)."""
    out = {}
    for s in specs or []:
        s = s.strip()
        if s.startswith("{"):
            out.update(json.loads(s))
        elif "=" in s and not Path(s).exists():
            k, v = s.split("=", 1)
            out[k] = float(v)
        else:
            p = Path(s)
            if not p.exists() and presets_dir is not None:
                p = Path(presets_dir) / (s if s.endswith(".json") else s + ".json")
            out.update(json.loads(p.read_text()))
    return out


def pose(name, base=None):
    params, sim = POSES[name]
    merged = dict(base or {})
    merged.update(params)
    return merged, sim
