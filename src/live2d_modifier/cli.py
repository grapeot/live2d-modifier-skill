"""l2d: command line for live2d_modifier. Run `l2d-mod <command> -h` for details."""
import argparse
import json
import sys
from pathlib import Path

from .paths import ROOT, resolve_model


def _zoom(s):
    return [float(v) for v in s.split(",")] if s else None


def _box(s):
    return [int(v) for v in s.split(",")]


def _params(args, md=None):
    from .model import apply_expression, load_expression
    from .poses import parse_params
    p = parse_params(args.params or [], ROOT / "presets")
    defaults = {}
    if md is not None and getattr(args, "expr", None):
        from .moc import Moc
        defaults = {item["id"]: item["default"] for item in Moc(md.moc_path).inventory()["params"]}
    for e in getattr(args, "expr", None) or []:
        path = Path(e) if Path(e).exists() else md.expressions().get(e)
        if path is None:
            raise SystemExit(f"expression not found: {e}")
        p = apply_expression(p, defaults, load_expression(path))
    return p


def cmd_render(a):
    from .model import ModelDir
    from .poses import pose
    from .render import Renderer, composite
    md = ModelDir(resolve_model(a.model))
    base = _params(a, md)
    prm, sim = pose(a.pose, base)
    with Renderer(port=a.port) as r:
        r.load(md.path, a.size[0], a.size[1], zoom=_zoom(a.zoom), nearest=a.nearest)
        im = r.render(prm, sim, settle=0 if a.no_physics else 60)
        (im if a.transparent else composite(im)).save(a.out)
        if a.dump:
            Path(a.out).with_suffix(".drawables.json").write_text(json.dumps(r.drawables()))
            Path(a.out).with_suffix(".params.json").write_text(json.dumps(r.params(), ensure_ascii=False))
        for line in r.console:
            print(line, file=sys.stderr)
    print(a.out)


def cmd_sheet(a):
    from .sheet import compare_sheet
    models = [resolve_model(m) for m in a.models]
    base = {str(m): json.loads(Path(a.base).read_text()) for m in models} if a.base else None
    print(compare_sheet(models, a.poses.split(","), a.out, a.size[0], a.size[1], zoom=_zoom(a.zoom), base=base,
                        labels=a.labels.split(",") if a.labels else None))


def cmd_inventory(a):
    from .model import ModelDir
    from .moc import Moc, part_tree_text
    md = ModelDir(resolve_model(a.model))
    inv = Moc(md.moc_path).inventory(md.part_names, md.param_names)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=0))
    (out / "parts.txt").write_text(part_tree_text(inv))
    with open(out / "params.tsv", "w") as f:
        f.write("id\tname\tdefault\tmin\tmax\n")
        for p in inv["params"]:
            f.write(f"{p['id']}\t{p['name']}\t{p['default']}\t{p['min']}\t{p['max']}\n")
    exps = {n: [list(t) for t in __import__("live2d_modifier.model", fromlist=["x"]).load_expression(p)] for n, p in md.expressions().items()}
    (out / "expressions.json").write_text(json.dumps(exps, ensure_ascii=False, indent=0))
    print(f"moc v{inv['version']}: {len(inv['parts'])} parts, {len(inv['params'])} params, {len(inv['meshes'])} meshes -> {out}")


def cmd_capture(a):
    from .corr import capture, capture_ids, correspond
    from .render import Renderer, composite
    from PIL import Image
    m = resolve_model(a.model)
    prm = _params(a)
    with Renderer(port=a.port) as r:
        capture(r, m, a.prefix, a.size[0], a.size[1], params=prm, zoom=_zoom(a.zoom))
        capture_ids(r, m, a.prefix, a.size[0], a.size[1], params=prm, zoom=_zoom(a.zoom))
    correspond(a.prefix)
    composite(Image.open(f"{a.prefix}.png")).save(f"{a.prefix}_in.png")
    print(f"{a.prefix}_in.png  (send this to the image editor; edit must keep the framing)")


def cmd_pick(a):
    from .corr import highlight, load_capture, pick
    from .model import ModelDir
    from .moc import Moc
    meta, _ = load_capture(a.prefix)
    md = ModelDir(meta["model"])
    inv = Moc(md.moc_path).inventory(md.part_names, md.param_names)
    rows = pick(a.prefix, _box(a.box), inv, top=a.top)
    for r in rows:
        print(f"{r['drawable']:5d} {r['id']:<14} {r['pixels']:7d}px part={r['part']} {r['part_name']} blend={r['blend']}")
    if a.show:
        highlight(a.prefix, [r["drawable"] for r in rows[:1]] if not a.show_all else [r["drawable"] for r in rows], a.show)
        print("highlight ->", a.show)


def cmd_conflicts(a):
    from .atlas import find_conflicts
    D = json.loads(Path(f"{a.prefix}_drawables.json").read_text())["drawables"]
    from .corr import load_capture
    meta, _ = load_capture(a.prefix)
    for x, y, n in find_conflicts(meta["model"], D, include_hidden=a.include_hidden)[: a.top]:
        print(f"{D[x]['id']:<14} <-> {D[y]['id']:<14} {n} texels")


def cmd_transfer(a):
    from .transfer import Capture, Transfer, conflict_report
    caps = []
    for c in a.cap:
        prefix, edit = c.split("=", 1)
        caps.append(Capture(prefix, edit, residual=not a.no_residual))
    tr = Transfer(resolve_model(a.src), caps)
    tr.fit_maps()
    if a.overrides:
        ov = {}
        for f in a.overrides:   # several files merge: dicts merge, lists concatenate, scalars: later wins
            for k, v in json.loads(Path(f).read_text()).items():
                if isinstance(v, dict) and isinstance(ov.get(k), dict):
                    ov[k] = {**ov[k], **v}
                elif isinstance(v, list) and isinstance(ov.get(k), list):
                    ov[k] = ov[k] + v
                else:
                    ov[k] = v
        tr.apply_overrides(ov)
    rep = conflict_report(tr)
    for r in rep[:15]:
        print(f"conflict {r['a_id']} ({r['a_src']}) <-> {r['b_id']} ({r['b_src']}): {r['texels']} texels, dE {r['disagreement']}")
    tr.write(a.dst)
    print(a.dst)


def cmd_variant(a):
    from .check import check_moc
    from .variant import build_variant
    build_variant(resolve_model(a.src), a.dst, a.recipe)
    from .model import ModelDir
    print(json.dumps(check_moc(ModelDir(a.dst).moc_path)))


def cmd_texels(a):
    from .check import check_moc
    from .model import ModelDir
    from .render import Renderer
    from .texels import apply_ops
    src = resolve_model(a.src)
    with Renderer() as r:
        r.load(src, 64, 64)
        drawables = r.drawables()["drawables"]
    apply_ops(src, a.dst, json.loads(Path(a.recipe).read_text()), drawables)
    print(json.dumps(check_moc(ModelDir(a.dst).moc_path)))


def cmd_moc(a):
    from .check import check_moc
    from .model import ModelDir
    from .moc import Moc
    if a.action == "check":
        print(json.dumps(check_moc(a.moc, a.ref), ensure_ascii=False))
    elif a.action == "param":
        m = resolve_model(a.moc); md = ModelDir(m); moc = Moc(md.moc_path)
        print(json.dumps(moc.param_info(a.id)))


def cmd_page(a):
    from PIL import Image
    from .pages import matte_flat_bg, test_pattern
    if a.action == "matte":
        bg = [float(v) for v in a.bg.split(",")] if a.bg else None
        Image.fromarray(matte_flat_bg(Image.open(a.inp), bg)).save(a.out)
    else:
        Image.fromarray(test_pattern(a.size)).save(a.out)
    print(a.out)


def cmd_vts(a):
    from .vts import install
    dst, mid = install(resolve_model(a.model), a.name, overwrite=a.overwrite)
    if a.icon:
        import shutil
        shutil.copy2(a.icon, dst / Path(a.icon).name)
        vj = next(dst.glob("*.vtube.json"))
        d = json.loads(vj.read_text()); d["FileReferences"]["Icon"] = Path(a.icon).name
        vj.write_text(json.dumps(d, ensure_ascii=False, indent=4))
    print(dst, mid)


def cmd_serve(a):
    import time
    from .server import Server
    s = Server(a.port)
    url = s.mount(resolve_model(a.model)) if a.model else None
    import urllib.parse
    page_url = f"{s.base}/viewer/index.html"
    if url:
        from .model import ModelDir
        page_url += "?model=" + urllib.parse.quote(url + urllib.parse.quote(ModelDir(resolve_model(a.model)).model3_path.name), safe="")
    print(page_url, flush=True)
    try:
        while True:
            time.sleep(3600)
    finally:
        s.close()


def cmd_motion(a):
    import math
    from .render import Renderer, composite
    if a.frames < 1 or a.fps < 1:
        raise ValueError("frames 和 fps 必须大于 0")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    schedule = []
    with Renderer() as renderer:
        renderer.load(resolve_model(a.model), *a.size, zoom=_zoom(a.zoom))
        for i in range(a.frames):
            p = {"ParamAngleX": 30 * math.sin(2 * math.pi * i / a.frames),
                 "ParamAngleY": 20 * math.sin(4 * math.pi * i / a.frames),
                 "ParamAngleZ": 12 * math.sin(2 * math.pi * i / a.frames),
                 "ParamEyeLOpen": 0 if i % 24 in (10, 11) else 1,
                 "ParamEyeROpen": 0 if i % 24 in (10, 11) else 1,
                 "ParamMouthOpenY": max(0, math.sin(i * 0.5))}
            schedule.append({"params": p, "dt": 1 / a.fps, "n": 1})
            composite(renderer.render(p, schedule)).save(out / f"{i:04d}.png")
    print(out)


def cmd_package(a):
    from .delivery import package_model
    print(package_model(resolve_model(a.model), a.out))


def build_parser():
    p = argparse.ArgumentParser(prog="l2d-mod", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common_render(sp, size=(900, 1600)):
        sp.add_argument("--size", nargs=2, type=int, default=list(size), metavar=("W", "H"))
        sp.add_argument("--zoom", help="z,cx,cy (cx, cy in 0..1 of the full-body frame)")
        sp.add_argument("--params", nargs="*", help="preset name, JSON file, inline JSON or k=v (later wins)")
        sp.add_argument("--port", type=int, default=None, help="HTTP port (default 8798, falls back to a free one)")

    s = sub.add_parser("render", help="render one pose to PNG")
    s.add_argument("model"); s.add_argument("-o", "--out", required=True)
    s.add_argument("--pose", default="front", help="front|blink|talk|smile|turn_l|turn_r|look_up|sway")
    s.add_argument("--expr", nargs="*", help="expression names (exp3 stem) or paths, applied over params")
    s.add_argument("--nearest", action="store_true"); s.add_argument("--no-physics", action="store_true")
    s.add_argument("--transparent", action="store_true"); s.add_argument("--dump", action="store_true")
    common_render(s); s.set_defaults(fn=cmd_render)

    s = sub.add_parser("sheet", help="comparison grid: models x poses")
    s.add_argument("models", nargs="+"); s.add_argument("-o", "--out", required=True)
    s.add_argument("--poses", default="front,blink,talk,smile,turn_l,turn_r,look_up,sway"); s.add_argument("--labels")
    s.add_argument("--base", help="JSON params applied under every pose for every model")
    s.add_argument("--size", nargs=2, type=int, default=[600, 1000]); s.add_argument("--zoom")
    s.set_defaults(fn=cmd_sheet)

    s = sub.add_parser("inventory", help="export parts tree, params, meshes, expressions")
    s.add_argument("model"); s.add_argument("-o", "--out", required=True); s.set_defaults(fn=cmd_inventory)

    s = sub.add_parser("capture", help="render + coded renders + ID maps + correspondence for one framing")
    s.add_argument("model"); s.add_argument("prefix"); common_render(s, (1152, 2048)); s.set_defaults(fn=cmd_capture)

    s = sub.add_parser("pick", help="which drawables are inside a pixel box of a capture")
    s.add_argument("prefix"); s.add_argument("--box", required=True, help="x0,y0,x1,y1")
    s.add_argument("--top", type=int, default=8); s.add_argument("--show", help="write a highlight PNG")
    s.add_argument("--show-all", action="store_true"); s.set_defaults(fn=cmd_pick)

    s = sub.add_parser("conflicts", help="drawable pairs sharing opaque texels (for a capture's pose)")
    s.add_argument("prefix"); s.add_argument("--top", type=int, default=40)
    s.add_argument("--include-hidden", action="store_true"); s.set_defaults(fn=cmd_conflicts)

    s = sub.add_parser("transfer", help="back-project edited captures onto the texture pages")
    s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--cap", nargs="+", required=True, help="PREFIX=EDIT.png (later = higher priority)")
    s.add_argument("--overrides", nargs="+", help="JSON: borrow/keep/alpha/no_residual/fill_hidden/affine_trim/"
                   "residual_holes; several files merge (dicts merge, lists concatenate)")
    s.add_argument("--no-residual", action="store_true", help="colour maps only (pure recolour)")
    s.set_defaults(fn=cmd_transfer)

    s = sub.add_parser("variant", help="apply a moc3 recipe (hide/affine/warp/blend/unmask/tint/params/texture/split)")
    s.add_argument("src"); s.add_argument("dst"); s.add_argument("recipe"); s.set_defaults(fn=cmd_variant)

    s = sub.add_parser("texels", help="deterministic texel edits: recolor by hue, saturate, smooth, match")
    s.add_argument("src"); s.add_argument("dst"); s.add_argument("recipe"); s.set_defaults(fn=cmd_texels)

    s = sub.add_parser("moc", help="moc3 utilities")
    s.add_argument("action", choices=["check", "param"]); s.add_argument("moc", help="moc3 path (check) or model (param)")
    s.add_argument("--ref"); s.add_argument("--id"); s.set_defaults(fn=cmd_moc)

    s = sub.add_parser("page", help="new texture pages")
    s.add_argument("action", choices=["matte", "test"]); s.add_argument("--in", dest="inp")
    s.add_argument("-o", "--out", required=True); s.add_argument("--bg", help="R,G,B of the flat background")
    s.add_argument("--size", type=int, default=512); s.set_defaults(fn=cmd_page)

    s = sub.add_parser("vts", help="install a model into VTube Studio as a new model")
    s.add_argument("model"); s.add_argument("--name", required=True); s.add_argument("--icon")
    s.add_argument("--overwrite", action="store_true"); s.set_defaults(fn=cmd_vts)

    s = sub.add_parser("serve", help="serve the viewer (and optionally a model) for manual inspection")
    s.add_argument("--model"); s.add_argument("--port", type=int, default=None); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("motion", help="render continuous head/blink/talk motion frames for visual review")
    s.add_argument("model"); s.add_argument("-o", "--out", required=True)
    s.add_argument("--frames", type=int, default=48); s.add_argument("--fps", type=int, default=12)
    s.add_argument("--size", nargs=2, type=int, default=[600, 1000]); s.add_argument("--zoom")
    s.set_defaults(fn=cmd_motion)
    s = sub.add_parser("package", help="package referenced model resources, excluding work logs")
    s.add_argument("model"); s.add_argument("-o", "--out", required=True); s.set_defaults(fn=cmd_package)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    main()
