import json
from pathlib import Path
import zipfile

import pytest

from live2d_modifier.cli import build_parser
from live2d_modifier.delivery import package_model
from live2d_modifier.variant import clone_model
from live2d_modifier.vts import install


@pytest.mark.parametrize("command", ["inventory", "render", "sheet", "capture", "pick", "conflicts",
                                    "transfer", "variant", "texels", "page", "moc", "vts", "serve",
                                    "motion", "package"])
def test_command_help(command):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args([command, "--help"])
    assert error.value.code == 0


def test_clone_refuses_source_overlap_and_existing_directory(fake_model, tmp_path):
    for dst in (fake_model, fake_model / "nested", fake_model.parent):
        with pytest.raises(ValueError):
            clone_model(fake_model, dst)
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "retain.txt"
    sentinel.write_text("retain")
    with pytest.raises(FileExistsError):
        clone_model(fake_model, target)
    assert sentinel.read_text() == "retain"


def test_package_excludes_work_logs_and_dereferences_textures(fake_model, tmp_path):
    (fake_model / "private.log").write_text("not a resource")
    dst = clone_model(fake_model, tmp_path / "copy")
    archive = package_model(dst, tmp_path / "delivery.zip")
    with zipfile.ZipFile(archive) as f:
        assert not any("private.log" in name for name in f.namelist())
        assert "copy/tex/texture_00.png" in f.namelist()
        assert f.read("copy/a.moc3") == (fake_model / "a.moc3").read_bytes()


def test_vts_supports_model_without_existing_vts_config(fake_model, tmp_path):
    (fake_model / "a.vtube.json").unlink()
    vts = tmp_path / "vts"
    vts.mkdir()
    dst, _ = install(fake_model, "Example", vts_dir=vts)
    assert json.loads((dst / "a.vtube.json").read_text())["Name"] == "Example"
    with pytest.raises(ValueError):
        install(fake_model, "../outside", vts_dir=vts)


def test_wheel_package_does_not_depend_on_checkout():
    import live2d_modifier.paths as paths
    assert paths.VIEWER == Path(paths.__file__).parent / "web"
    assert (paths.VIEWER / "index.html").is_file()
