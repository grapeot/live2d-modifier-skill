// usage: node check.js <moc3> [refMoc3]
// Prints one JSON line: consistency check, load result, counts, and (with ref) per-drawable differences at default params.
const fs = require('fs'), path = require('path');
const corePath = process.env.L2D_CORE_PATH || process.env.L2D_CORE;
if (!corePath) throw new Error('L2D_CORE_PATH is required');
const src = fs.readFileSync(corePath, 'utf8');
new Function('__dirname', '__filename', 'require', 'module', src + ';globalThis.Live2DCubismCore=Live2DCubismCore;')(path.dirname(corePath), 'x.js', require, {});
const C = globalThis.Live2DCubismCore;
function load(p) {
  const buf = fs.readFileSync(p); const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const cons = !!C.Moc.prototype.hasMocConsistency(ab);
  const moc = C.Moc.fromArrayBuffer(ab); if (!moc) return { cons, ok: false };
  const m = C.Model.fromMoc(moc); if (!m) return { cons, ok: false };
  m.update();
  const d = m.drawables;
  return { cons, ok: true, count: d.count, ids: d.ids, op: Array.from(d.opacities), ord: Array.from(d.renderOrders || d.drawOrders),
    vtx: d.vertexPositions.map(a => Array.from(a)), uv: d.vertexUvs.map(a => Array.from(a)), tex: Array.from(d.textureIndices),
    mul: d.multiplyColors ? Array.from(d.multiplyColors) : null, scr: d.screenColors ? Array.from(d.screenColors) : null,
    nparams: m.parameters.count, nparts: m.parts.count };
}
function main() {
  const a = load(process.argv[2]);
  const out = { file: process.argv[2], consistency: a.cons, loaded: a.ok, drawables: a.count, params: a.nparams, parts: a.nparts };
  if (process.argv[3] && a.ok) {
    const b = load(process.argv[3]); const diffs = [];
    for (let i = 0; i < a.count; i++) {
      let dv = 0, du = 0, dm = 0;
      for (let j = 0; j < a.vtx[i].length; j++) dv = Math.max(dv, Math.abs(a.vtx[i][j] - b.vtx[i][j]));
      for (let j = 0; j < a.uv[i].length; j++) du = Math.max(du, Math.abs(a.uv[i][j] - b.uv[i][j]));
      if (a.mul) for (let k = 0; k < 4; k++) dm = Math.max(dm, Math.abs(a.mul[i * 4 + k] - b.mul[i * 4 + k]), Math.abs(a.scr[i * 4 + k] - b.scr[i * 4 + k]));
      const dop = Math.abs(a.op[i] - b.op[i]);
      if (dv > 1e-6 || du > 1e-6 || dop > 1e-6 || dm > 1e-6 || a.ord[i] !== b.ord[i] || a.tex[i] !== b.tex[i])
        diffs.push({ id: a.ids[i], dvtx: +dv.toFixed(4), duv: +du.toFixed(4), op: [b.op[i], a.op[i]], dcolor: +dm.toFixed(3), tex: [b.tex[i], a.tex[i]] });
    }
    out.changed = diffs.length; out.sample = diffs.slice(0, 8);
  }
  console.log(JSON.stringify(out));
}
function tryRun(n) { try { C.Version.csmGetVersion(); } catch (e) { if (n > 0) return setTimeout(() => tryRun(n - 1), 100); throw e; } main(); }
setTimeout(() => tryRun(50), 50);
