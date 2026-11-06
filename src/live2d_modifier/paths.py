"""Portable package resources and user-owned runtime inputs."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get("L2D_LOCAL", Path.cwd() / "local")).expanduser().resolve()
MODELS = LOCAL / "models"
VARIANTS = LOCAL / "variants"
WORK = LOCAL / "work"
VIEWER = ROOT / "web"
DEFAULT_PORT = int(os.environ.get("L2D_PORT", "8798"))
VTS_MODELS = Path(os.environ.get(
    "L2D_VTS_DIR",
    Path.home() / "Library/Application Support/Steam/steamapps/common/VTube Studio/VTubeStudio.app"
    "/Contents/Resources/Data/StreamingAssets/Live2DModels"))


def core_path():
    value = os.environ.get("L2D_CORE_PATH") or os.environ.get("L2D_CORE")
    if not value or not Path(value).expanduser().is_file():
        raise RuntimeError("设置 L2D_CORE_PATH，指向你按 Live2D 官方许可取得的 live2dcubismcore.min.js")
    return Path(value).expanduser().resolve()


def runtime_assets():
    """Runtime JS are user-installed npm dependencies, never model assets."""
    node_modules = Path(os.environ.get("L2D_NODE_MODULES", Path.cwd() / "node_modules")).resolve()
    assets = {
        "live2dcubismcore.min.js": core_path(),
        "pixi8.min.js": node_modules / "pixi.js/dist/pixi.min.js",
        "upl_cubism.min.js": node_modules / "untitled-pixi-live2d-engine/dist/cubism.min.js",
    }
    for name, path in assets.items():
        if not path.is_file():
            raise RuntimeError(f"运行依赖缺失 {name}: {path}；在项目根运行 npm ci 或设置 L2D_NODE_MODULES")
    return assets


def resolve_model(name_or_path) -> Path:
    for candidate in (Path(name_or_path).expanduser(), MODELS / str(name_or_path), VARIANTS / str(name_or_path)):
        if candidate.is_dir():
            return candidate.resolve()
    raise FileNotFoundError(f"model directory not found: {name_or_path}")
