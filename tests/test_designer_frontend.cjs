// Pure Node tests: no browser, desktop automation, external packages or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {chartSvg} = require('../scienceclaw/static/designer_charts.js');
const tests = [
  {id: 'draft', iteration: 0, chosen: true, results: {passed: true, mass_g: 70}},
  {id: 'a', iteration: 1, chosen: false, results: {passed: false, mass_g: 50}},
  {id: 'b', iteration: 1, chosen: false, results: {passed: false, mass_g: 50}},
];
let svg = chartSvg(tests, {width: 200, value: t => t.results.mass_g, unit: 'g'});
assert(svg.includes('width="100%"') && svg.includes('viewBox='));
assert(!/NaN|Infinity/.test(svg));
const xs = [...svg.matchAll(/<circle cx="([^"]+)"/g)].map(m => +m[1]);
assert.equal(new Set(xs).size, 3); // proposals sharing a round remain separately selectable
assert(svg.includes('data-series="adopted"')); // retained draft is visible across rejected proposals
const line = /data-series="adopted"[^>]+points="([^"]+)"/.exec(svg)[1].split(' ');
assert.equal(new Set(line.map(p => p.split(',')[1])).size, 1);
assert(chartSvg([], {value: t => t, unit: 'mm'}).includes('No finite results'));
assert(!/NaN|Infinity/.test(chartSvg([{...tests[0], results: {passed: false}}], {value: () => NaN})));
svg = chartSvg(Array.from({length: 100}, (_, i) => ({...tests[0], id: String(i), chosen: i === 0})),
               {width: 300, value: () => 1, limit: 2, unit: 'mm'});
assert((svg.match(/<text /g) || []).length < 20); // tick labels are bounded on narrow charts
assert.equal((svg.match(/<circle /g) || []).length, 100);

const ctx = {points: [], fills: 0, setTransform() {}, fillRect() {}, beginPath() {}, closePath() {}, stroke() {},
  setLineDash() {}, fillText() {}, fill() {this.fills++;}, moveTo(x, y) {this.points.push([x, y]);},
  lineTo(x, y) {this.points.push([x, y]);}};
const events = {};
const canvas = {clientWidth: 260, clientHeight: 140, getContext: () => ctx,
  addEventListener: (n, f) => {events[n] = f;}, setPointerCapture() {}};
const sandbox = {window: {devicePixelRatio: 1, addEventListener() {}}, console};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(require.resolve('../scienceclaw/static/viewer3d.js'), 'utf8'), sandbox);
const viewer = sandbox.window.Viewer3D(canvas, null);
const field = {positions: [[0, 0, 0], [100, 100, 100], [100, 0, 0]],
  displacement: [[0, 0, 0], [0, 0, 0], [0, 0, 0]], stress: [1, 2, 3], triangles: [[0, 1, 2], [2, 1, 0]]};
viewer.setField(field, {supports: [], loads: []}, {stress: 23, bend: 2});
assert.equal(ctx.fills, 2); // mixed winding no longer makes faces disappear
assert(ctx.points.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y) && x >= 0 && x <= 260 && y >= 0 && y <= 140));
events.pointerdown({clientX: 0, clientY: 0, pointerId: 1});
ctx.points = [];
events.pointermove({clientX: 180, clientY: 80});
assert(ctx.points.every(([x, y]) => x >= 0 && x <= 260 && y >= 0 && y <= 140));
assert.throws(() => viewer.setField({...field, triangles: [[0, 1, 999]]}), /incomplete or invalid/);
// A dense field must not exceed JavaScript's argument limit via Math.max(...array).
const dense = {...field, positions: Array(140000).fill([0, 0, 0]), displacement: Array(140000).fill([0, 0, 0]), stress: Array(140000).fill(0)};
viewer.setField(dense, {supports: [], loads: []}, {stress: 23});
console.log('Chart and 3D drawing checks passed (Node only).');
