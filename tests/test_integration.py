"""Integration tests against a real model (opt-in: L2D_TEST_MODEL=<model dir>, L2D_TEST_BROWSER=1)."""


from conftest import browser, model, real_model


@model
def test_moc_inventory_and_inplace_edits_load(tmp_path):
    from live2d_modifier.check import check_moc
    from live2d_modifier.model import ModelDir
    from live2d_modifier.moc import Moc
    md = ModelDir(real_model())
    m = Moc(md.moc_path)
    inv = m.inventory(md.part_names, md.param_names)
    assert inv["meshes"] and inv["params"]
    k = 0
    m.hide([k]); m.affine_meshes([1], 1.1, 1.2); m.set_param(inv["params"][0]["id"], default=inv["params"][0]["min"])
    if m.version >= 4:
        m.tint([2], mul=(0.5, 0.6, 1.0), scr=(0.1, 0, 0))
    out = tmp_path / "x.moc3"; m.save(out)
    r = check_moc(out, md.moc_path)
    assert r["consistency"] and r["loaded"] and r["changed"] >= 2


@model
@browser
def test_render_and_correspondence(tmp_path):
    from live2d_modifier.corr import capture, capture_ids, correspond
    from live2d_modifier.render import Renderer
    with Renderer() as r:
        capture(r, real_model(), tmp_path / "c", 300, 500, log=lambda *a: None)
        ids = capture_ids(r, real_model(), tmp_path / "c", 300, 500)
    st = correspond(tmp_path / "c", log=lambda *a: None)
    assert (ids >= 0).sum() > 1000 and st["consistent"] > 0.2
