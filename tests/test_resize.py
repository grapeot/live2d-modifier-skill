import json

import numpy as np
import pytest
from PIL import Image

from live2d_modifier.cli import main
from live2d_modifier.delivery import package_model
from live2d_modifier.resize import resize_model, resize_rgba_float


def test_low_alpha_constant_color_survives_filtering():
    # Skin-colored patch with alpha too low for accurate 8-bit premultiplication.
    rgba = np.zeros((32, 32, 4), np.uint8)
    rgba[..., :3] = [237, 183, 201]
    rgba[..., 3] = np.tile(np.arange(1, 33, dtype=np.uint8), (32, 1))
    out = np.asarray(resize_rgba_float(Image.fromarray(rgba), (13, 13)))
    visible = out[..., 3] > 0
    assert np.max(np.abs(out[visible, :3].astype(int) - [237, 183, 201])) <= 1
    ordinary = np.asarray(Image.fromarray(rgba).resize((13, 13), Image.Resampling.LANCZOS))
    assert np.max(np.abs(ordinary[visible, :3].astype(int) - [237, 183, 201])) > 5


def test_hidden_rgb_cannot_bleed_into_visible_pixels():
    rgba = np.zeros((16, 16, 4), np.uint8)
    rgba[..., :3] = [0, 255, 0]
    rgba[4:12, 4:12] = [230, 180, 200, 100]
    out = np.asarray(resize_rgba_float(Image.fromarray(rgba), (9, 9)))
    assert np.max(np.abs(out[out[..., 3] > 1, :3].astype(int) - [230, 180, 200])) <= 1
    assert (out[out[..., 3] == 0, :3] == 0).all()


def test_resize_cli_self_contained_and_package_excludes_manifest(fake_model, tmp_path, monkeypatch):
    texture = fake_model / 'tex/texture_00.png'
    external = tmp_path / 'external.png'
    Image.new('RGBA', (24, 12), (237, 183, 201, 30)).save(external)
    texture.unlink()
    texture.symlink_to(external)
    (fake_model / 'private_notes.txt').write_text('not a model resource')
    source_bytes = {p: p.read_bytes() for p in fake_model.rglob('*') if p.is_file()}
    monkeypatch.chdir(tmp_path)
    main(['resize', str(fake_model.relative_to(tmp_path)), 'resized', '--max-size', '8'])
    dest = tmp_path / 'resized'
    assert Image.open(dest / 'tex/texture_00.png').size == (8, 4)
    assert not any(p.is_symlink() for p in dest.rglob('*'))
    assert not (dest / 'private_notes.txt').exists()
    assert all(p.read_bytes() == raw for p, raw in source_bytes.items())
    assert (dest / 'a.moc3').read_bytes() == (fake_model / 'a.moc3').read_bytes()
    assert (dest / 'a.model3.json').read_bytes() == (fake_model / 'a.model3.json').read_bytes()
    manifest = json.loads((dest / 'texture_resize_manifest.json').read_text())
    assert manifest['textures'][0]['to'] == [8, 4]
    import zipfile
    with zipfile.ZipFile(package_model(dest, tmp_path / 'out.zip')) as z:
        assert not any('manifest' in n or 'private_notes' in n for n in z.namelist())
        assert z.testzip() is None


def test_small_pages_are_byte_identical_and_never_upscaled(fake_model, tmp_path):
    original = (fake_model / 'tex/texture_00.png').read_bytes()
    dest = resize_model(fake_model, tmp_path / 'small', 4096)
    assert (dest / 'tex/texture_00.png').read_bytes() == original


@pytest.mark.parametrize('maximum', [0, -1, True, 3.5])
def test_invalid_size_has_no_output(fake_model, tmp_path, maximum):
    with pytest.raises(ValueError):
        resize_model(fake_model, tmp_path / 'out', maximum)
    assert not (tmp_path / 'out').exists()


def test_reject_overlap_existing_and_escaping_resources(fake_model, tmp_path):
    for dest in [fake_model, fake_model / 'nested', tmp_path]:
        with pytest.raises(ValueError):
            resize_model(fake_model, dest)
    existing = tmp_path / 'existing'
    existing.mkdir()
    with pytest.raises(FileExistsError):
        resize_model(fake_model, existing)
    model3 = fake_model / 'a.model3.json'
    data = json.loads(model3.read_text())
    data['FileReferences']['Textures'] = ['../external.png']
    model3.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        resize_model(fake_model, tmp_path / 'escape')
    assert not (tmp_path / 'escape').exists()


def test_thin_page_minimum_dimension_and_duplicate_reference(fake_model, tmp_path):
    texture = fake_model / 'tex/texture_00.png'
    Image.new('RGBA', (100, 1), (120, 90, 80, 10)).save(texture)
    p = fake_model / 'a.model3.json'
    data = json.loads(p.read_text())
    data['FileReferences']['Textures'] *= 2
    p.write_text(json.dumps(data))
    dest = resize_model(fake_model, tmp_path / 'thin', 4)
    assert Image.open(dest / 'tex/texture_00.png').size == (4, 1)
    assert len(json.loads((dest / 'texture_resize_manifest.json').read_text())['textures']) == 1
