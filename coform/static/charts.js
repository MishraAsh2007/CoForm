'use strict';
// Small SVG chart kit for the lab views. Palette validated with the dataviz validator
// against the app's dark surface #191f22 (adjacent and all-pairs CVD / contrast pass).
const VIZ = {
  surface: '#191f22', grid: '#2c3437', axis: '#3d484b', muted: '#93a6aa', ink: '#edf4f1', secondary: '#c3cfcc',
  blue: '#3987e5', orange: '#d95926', aqua: '#199e70', violet: '#9085e9', critical: '#d03b3b', good: '#0ca30c',
  ramp: ['#104281', '#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef'],
};
const SVGNS = 'http://www.w3.org/2000/svg';
function el(tag, attrs = {}, parent) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}
function text(parent, x, y, value, attrs = {}) {
  const t = el('text', {x, y, fill: VIZ.muted, 'font-size': 11, ...attrs}, parent);
  t.textContent = value;
  return t;
}
function niceTicks(min, max, count = 5) {
  const span = max - min || 1, step0 = span / count, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= count) || mag * 10;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}
const fmt = (v, d = 3) => Number(v).toLocaleString(undefined, {maximumFractionDigits: d});

// One tooltip for every chart: value leads (strong), label follows (secondary).
const tip = document.createElement('div');
tip.className = 'viz-tip'; tip.hidden = true; document.body.appendChild(tip);
function showTip(event, rows, title) {
  tip.replaceChildren();
  if (title) { const h = document.createElement('div'); h.className = 'viz-tip-title'; h.textContent = title; tip.appendChild(h); }
  for (const row of rows) {
    const line = document.createElement('div'); line.className = 'viz-tip-row';
    if (row.color) { const key = document.createElement('span'); key.className = 'viz-key'; key.style.background = row.color; line.appendChild(key); }
    const value = document.createElement('strong'); value.textContent = row.value; line.appendChild(value);
    const label = document.createElement('span'); label.textContent = ' ' + row.label; line.appendChild(label);
    tip.appendChild(line);
  }
  tip.hidden = false;
  const x = Math.min(window.innerWidth - tip.offsetWidth - 12, event.clientX + 14);
  tip.style.left = x + 'px'; tip.style.top = (event.clientY + 14) + 'px';
}
const hideTip = () => { tip.hidden = true; };

function frame(container, {height = 260, left = 52, right = 18, top = 14, bottom = 40} = {}) {
  container.replaceChildren();
  const width = Math.max(320, container.clientWidth || 600);
  const svg = el('svg', {width, height, viewBox: `0 0 ${width} ${height}`, role: 'img'});
  container.appendChild(svg);
  return {svg, width, height, plot: {x0: left, x1: width - right, y0: top, y1: height - bottom}};
}
function axes(f, xScale, yScale, xTicks, yTicks, xLabel, yLabel, xFormat = v => fmt(v), yFormat = v => fmt(v)) {
  const {svg, plot} = f;
  for (const t of yTicks) {
    el('line', {x1: plot.x0, x2: plot.x1, y1: yScale(t), y2: yScale(t), stroke: VIZ.grid, 'stroke-width': 1}, svg);
    text(svg, plot.x0 - 8, yScale(t) + 4, yFormat(t), {'text-anchor': 'end', 'font-variant-numeric': 'tabular-nums'});
  }
  el('line', {x1: plot.x0, x2: plot.x1, y1: plot.y1, y2: plot.y1, stroke: VIZ.axis, 'stroke-width': 1}, svg);
  for (const t of xTicks) text(svg, xScale(t), plot.y1 + 16, xFormat(t), {'text-anchor': 'middle', 'font-variant-numeric': 'tabular-nums'});
  text(svg, (plot.x0 + plot.x1) / 2, plot.y1 + 33, xLabel, {'text-anchor': 'middle'});
  text(svg, plot.x0, plot.y0 - 2, yLabel, {'text-anchor': 'start'});
}
function legend(container, items) {
  const box = document.createElement('div'); box.className = 'viz-legend';
  for (const item of items) {
    const entry = document.createElement('span');
    const key = document.createElement('span'); key.className = 'viz-key ' + (item.shape || 'line'); key.style.background = item.color;
    entry.appendChild(key); entry.appendChild(document.createTextNode(item.label)); box.appendChild(entry);
  }
  container.prepend(box);
}
function dot(svg, x, y, color, r = 4.5) {
  el('circle', {cx: x, cy: y, r: r + 2, fill: VIZ.surface}, svg);      // 2px surface ring
  return el('circle', {cx: x, cy: y, r, fill: color}, svg);
}
function hit(svg, x, y, onEnter) {
  const target = el('circle', {cx: x, cy: y, r: 12, fill: 'transparent', tabindex: 0}, svg);  // 24px target
  target.addEventListener('pointermove', onEnter); target.addEventListener('focus', e => onEnter(e.target.getBoundingClientRect ? {clientX: e.target.getBoundingClientRect().x, clientY: e.target.getBoundingClientRect().y} : e));
  target.addEventListener('pointerleave', hideTip); target.addEventListener('blur', hideTip);
}

// A. Starting design: model curves vs every load test, scaled to the design load.
function modelChart(container, d) {
  const f = frame(container, {height: 280});
  const {svg, plot} = f;
  const xMax = d.freeLength * 1000, yValues = [...d.points.map(p => p.y + 2 * p.err), ...d.curves.flatMap(c => c.points.map(p => p[1])), d.limit];
  const yMax = Math.max(...yValues) * 1.1 || 1;
  const xs = v => plot.x0 + (v / xMax) * (plot.x1 - plot.x0), ys = v => plot.y1 - (v / yMax) * (plot.y1 - plot.y0);
  axes(f, xs, ys, niceTicks(0, xMax), niceTicks(0, yMax), 'Load position from clamp (mm)', 'Deflection at the design load (mm)');
  // Limit applies only at the design-load station: a short marker, not a line across.
  const lx = xs(d.proofPosition * 1000), ly = ys(d.limit);
  el('line', {x1: lx - 16, x2: lx + 16, y1: ly, y2: ly, stroke: VIZ.critical, 'stroke-width': 2, 'stroke-linecap': 'round'}, svg);
  text(svg, lx - 20, ly + 4, `limit ${fmt(d.limit, 2)} mm`, {'text-anchor': 'end', fill: VIZ.secondary});
  for (const c of d.curves) {
    el('path', {d: c.points.map((p, i) => `${i ? 'L' : 'M'}${xs(p[0])},${ys(p[1])}`).join(''), fill: 'none', stroke: c.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
    const last = c.points.at(-1);
    text(svg, xs(last[0]) - 4, ys(last[1]) - 8, c.name, {'text-anchor': 'end', fill: VIZ.secondary});
  }
  for (const p of d.points) {
    const x = xs(p.x), y = ys(p.y);
    el('line', {x1: x, x2: x, y1: ys(Math.max(0, p.y - 2 * p.err)), y2: ys(p.y + 2 * p.err), stroke: VIZ.orange, 'stroke-width': 2, 'stroke-linecap': 'round'}, svg);
    dot(svg, x, y, VIZ.orange);
    hit(svg, x, y, e => showTip(e, [{value: `${fmt(p.y)} mm`, label: 'measured (scaled)', color: VIZ.orange},
      ...d.curves.map(c => ({value: `${fmt(c.at(p.x))} mm`, label: c.name, color: c.color}))], `${p.label} · ${fmt(p.x, 0)} mm, ${fmt(p.force, 3)} N`));
  }
  legend(container, [{label: 'Measured (±2σ)', color: VIZ.orange, shape: 'dot'}, ...d.curves.map(c => ({label: c.name, color: c.color}))]);
}

// B. Every design proof-tested at the design load: predicted vs measured, against the limit.
function designsChart(container, rows, limit) {
  const f = frame(container, {height: 250, bottom: 48});
  const {svg, plot} = f;
  if (!rows.length) { text(svg, plot.x0, plot.y0 + 30, 'No design has been tested at the design load yet.', {fill: VIZ.secondary, 'font-size': 13}); return; }
  const yMax = Math.max(limit, ...rows.flatMap(r => [r.predicted, ...r.measured.map(m => m.y + 2 * m.err)])) * 1.15;
  const band = (plot.x1 - plot.x0) / rows.length, xs = i => plot.x0 + band * (i + .5), ys = v => plot.y1 - (v / yMax) * (plot.y1 - plot.y0);
  axes(f, xs, ys, [], niceTicks(0, yMax), '', 'Deflection at the design load (mm)');
  el('line', {x1: plot.x0, x2: plot.x1, y1: ys(limit), y2: ys(limit), stroke: VIZ.critical, 'stroke-width': 1}, svg);
  text(svg, plot.x1, ys(limit) - 6, `limit ${fmt(limit, 2)} mm`, {'text-anchor': 'end', fill: VIZ.secondary});
  rows.forEach((r, i) => {
    const x = xs(i);
    text(svg, x, plot.y1 + 16, r.name, {'text-anchor': 'middle', fill: VIZ.secondary});
    text(svg, x, plot.y1 + 30, r.origin, {'text-anchor': 'middle'});
    el('circle', {cx: x - 9, cy: ys(r.predicted), r: 5, fill: VIZ.surface, stroke: VIZ.blue, 'stroke-width': 2}, svg);
    hit(svg, x - 9, ys(r.predicted), e => showTip(e, [{value: `${fmt(r.predicted)} mm`, label: 'predicted', color: VIZ.blue}], r.name));
    r.measured.forEach((m, j) => {
      const mx = x + 9 + j * 6;
      el('line', {x1: mx, x2: mx, y1: ys(Math.max(0, m.y - 2 * m.err)), y2: ys(m.y + 2 * m.err), stroke: VIZ.orange, 'stroke-width': 2}, svg);
      dot(svg, mx, ys(m.y), VIZ.orange, 4);
      hit(svg, mx, ys(m.y), e => showTip(e, [{value: `${fmt(m.y)} ± ${fmt(2 * m.err)} mm`, label: m.status, color: VIZ.orange}], `${r.name} · ${m.label}`));
    });
  });
  legend(container, [{label: 'Predicted', color: VIZ.blue, shape: 'ring'}, {label: 'Measured (±2σ)', color: VIZ.orange, shape: 'dot'}]);
}

// C. What the model learned: one small multiple per parameter (never a dual axis).
function paramChart(container, points, {name, unit, color = VIZ.blue}) {
  const f = frame(container, {height: 170, bottom: 34});
  const {svg, plot} = f;
  if (!points.length) { text(svg, plot.x0, plot.y0 + 30, 'No identified fit yet: the agents need a second load station.', {fill: VIZ.secondary, 'font-size': 12}); return; }
  const hi = Math.max(...points.map(p => p.y + (p.sd || 0))) * 1.15 || 1, lo = Math.max(0, Math.min(...points.map(p => p.y - (p.sd || 0))) * .85);
  const xs = i => plot.x0 + (points.length === 1 ? (plot.x1 - plot.x0) / 2 : (i / (points.length - 1)) * (plot.x1 - plot.x0)), ys = v => plot.y1 - ((v - lo) / (hi - lo)) * (plot.y1 - plot.y0);
  axes(f, xs, ys, points.map((_, i) => i), niceTicks(lo, hi, 4), 'Model version (each refit after a load test)', `${name} (${unit})`, i => points[i]?.label ?? '');
  if (points.some(p => p.sd)) el('path', {d: [...points.map((p, i) => `${i ? 'L' : 'M'}${xs(i)},${ys(p.y + (p.sd || 0))}`), ...points.slice().reverse().map((p, k) => `L${xs(points.length - 1 - k)},${ys(Math.max(lo, p.y - (p.sd || 0)))}`), 'Z'].join(''), fill: color, 'fill-opacity': .1}, svg);
  if (points.length > 1) el('path', {d: points.map((p, i) => `${i ? 'L' : 'M'}${xs(i)},${ys(p.y)}`).join(''), fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round'}, svg);
  points.forEach((p, i) => { dot(svg, xs(i), ys(p.y), color, 4); hit(svg, xs(i), ys(p.y), e => showTip(e, [{value: `${fmt(p.y)}${p.sd ? ' ± ' + fmt(p.sd) : ''} ${unit}`, label: name, color}], p.title)); });
  const last = points.at(-1); text(svg, xs(points.length - 1) - 6, ys(last.y) - 10, `${fmt(last.y)} ${unit}`, {'text-anchor': 'end', fill: VIZ.ink});
}

// D. Design space: predicted share of the deflection limit for every allowed width x wall height.
function designSpace(container, space, markers, onPick) {
  const f = frame(container, {height: 330, left: 56, bottom: 44});
  const {svg, plot} = f;
  const widths = [...new Set(space.cells.map(c => +(c.base_width_m * 1000).toFixed(3)))].sort((a, b) => a - b);
  const heights = [...new Set(space.cells.map(c => +(c.wall_height_m * 1000).toFixed(3)))].sort((a, b) => a - b);
  const cw = (plot.x1 - plot.x0) / widths.length, ch = (plot.y1 - plot.y0) / heights.length;
  const xs = w => plot.x0 + (widths.indexOf(+w.toFixed(3)) + .5) * cw, ys = h => plot.y1 - (heights.indexOf(+h.toFixed(3)) + .5) * ch;
  const shade = u => VIZ.ramp[Math.min(VIZ.ramp.length - 1, Math.floor(Math.min(u, 1.5) / 1.5 * VIZ.ramp.length))];
  for (const c of space.cells) {
    const w = c.base_width_m * 1000, h = c.wall_height_m * 1000;
    const rect = el('rect', {x: xs(w) - cw / 2 + 1, y: ys(h) - ch / 2 + 1, width: Math.max(1, cw - 2), height: Math.max(1, ch - 2), rx: 2,
      fill: shade(c.utilization), 'fill-opacity': c.passes ? 1 : .28}, svg);
    rect.addEventListener('pointermove', e => showTip(e, [{value: `${fmt(c.utilization * 100, 0)}%`, label: 'of the deflection limit'}, {value: `${fmt(c.volume_m3 * 1e6, 2)} cm³`, label: 'material'},
      {value: c.passes ? '✓ meets every requirement' : '✗ fails: ' + c.failed.join(', ').replaceAll('_', ' '), label: ''}], `${fmt(w)} mm base × ${fmt(h)} mm wall`));
    rect.addEventListener('pointerleave', hideTip);
  }
  axes(f, w => xs(w), h => ys(h), widths.filter((_, i) => i % Math.ceil(widths.length / 8) === 0), [], 'Base width (mm)', 'Wall height (mm)');
  heights.filter((_, i) => i % Math.ceil(heights.length / 6) === 0).forEach(h => text(svg, plot.x0 - 8, ys(h) + 4, fmt(h), {'text-anchor': 'end'}));
  const origins = {start: VIZ.aqua, agents: VIZ.orange, you: VIZ.violet};
  for (const m of markers) {
    const x = xs(m.w), y = ys(m.h); if (Number.isNaN(x) || Number.isNaN(y)) continue;
    if (m.selected) el('circle', {cx: x, cy: y, r: 11, fill: 'none', stroke: VIZ.ink, 'stroke-width': 1.5}, svg);
    dot(svg, x, y, origins[m.origin], 5);
    if (m.tested) el('circle', {cx: x, cy: y, r: 2, fill: VIZ.surface}, svg);
    hit(svg, x, y, e => showTip(e, [{value: m.name, label: m.originLabel, color: origins[m.origin]}, {value: m.tested ? 'tested' : 'not tested yet', label: ''}], `${fmt(m.w)} × ${fmt(m.h)} mm`));
    if (onPick) svg.lastChild.addEventListener('click', () => onPick(m.id));
  }
  legend(container, [{label: 'Starting design', color: origins.start, shape: 'dot'}, {label: 'Agent designs', color: origins.agents, shape: 'dot'}, {label: 'Your Fusion edits', color: origins.you, shape: 'dot'}]);
  const scale = document.createElement('div'); scale.className = 'viz-scale';
  scale.append('Predicted share of the deflection limit: ');
  VIZ.ramp.forEach((c, i) => { const s = document.createElement('span'); s.style.background = c; s.title = `${fmt(i / VIZ.ramp.length * 150, 0)}–${fmt((i + 1) / VIZ.ramp.length * 150, 0)}%`; scale.appendChild(s); });
  scale.append(' 0% → 150%+ · faded cells fail a requirement · dot with a hole = tested');
  container.appendChild(scale);
}
window.Charts = {modelChart, designsChart, paramChart, designSpace, VIZ};
