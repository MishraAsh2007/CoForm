'use strict';
// Pure SVG generation, also exercised under Node without opening a browser.
(function (root) {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const fmt = n => Number(n).toLocaleString(undefined, {maximumSignificantDigits: 3});
  function chartSvg(tests, {width = 400, value, limit, unit = ''}) {
    const w = Math.max(260, Number.isFinite(width) ? width : 400), h = 210;
    const m = {l: 58, r: 20, t: 28, b: 42};
    const pts = tests.map((t, x) => ({t, x, y: value(t)})).filter(p => Number.isFinite(p.y) && p.y >= 0);
    if (!pts.length) return '<p class="muted">No finite results to plot yet.</p>';
    const hasLimit = Number.isFinite(limit) && limit > 0;
    const ymax = Math.max(1e-9, ...pts.map(p => p.y), hasLimit ? limit : 0) * 1.15;
    const xmax = Math.max(1, tests.length - 1);
    const X = x => m.l + (w - m.l - m.r) * x / xmax;
    const Y = y => h - m.b - (h - m.t - m.b) * y / ymax;
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(unit)} by tested design"><title>Each dot is a tested design; the line retains the adopted design.</title>`;
    for (let i = 0; i <= 4; i++) {
      const y = ymax * i / 4;
      svg += `<line x1="${m.l}" x2="${w - m.r}" y1="${Y(y)}" y2="${Y(y)}" stroke="#2c3437"/><text x="${m.l - 8}" y="${Y(y) + 4}" text-anchor="end" fill="#93a6aa" font-size="11">${fmt(y)}</text>`;
    }
    const stride = Math.max(1, Math.ceil(xmax / Math.max(2, Math.floor((w - m.l - m.r) / 48))));
    const ticks = new Set([0, xmax]);
    for (let i = stride; i < xmax; i += stride) ticks.add(i);
    for (const x of [...ticks].sort((a, b) => a - b)) svg += `<text x="${X(x)}" y="${h - 24}" text-anchor="middle" fill="#93a6aa" font-size="11">${x === 0 ? 'draft' : x}</text>`;
    svg += `<text x="${m.l}" y="14" fill="#93a6aa" font-size="11">${esc(unit)}</text><text x="${w / 2}" y="${h - 5}" text-anchor="middle" fill="#93a6aa" font-size="11">Test sequence</text>`;
    if (hasLimit) svg += `<line x1="${m.l}" x2="${w - m.r}" y1="${Y(limit)}" y2="${Y(limit)}" stroke="#d03b3b" stroke-dasharray="5 4"/><text x="${w - m.r}" y="${Y(limit) - 5}" text-anchor="end" fill="#f2b9a3" font-size="11">limit ${fmt(limit)} ${esc(unit)}</text>`;
    const line = [];
    let retained = null;
    for (const p of pts) {
      if (retained !== null) line.push(`${X(p.x)},${Y(retained)}`);
      if (p.t.chosen) retained = p.y;
      if (retained !== null) line.push(`${X(p.x)},${Y(retained)}`);
    }
    if (line.length > 1) svg += `<polyline data-series="adopted" fill="none" stroke="#3987e5" stroke-width="2" points="${line.join(' ')}"/>`;
    for (const p of pts) {
      const pass = p.t.results.passed, color = pass ? '#3987e5' : '#d95926';
      const label = `${p.x === 0 ? 'Draft' : 'Test ' + p.x + ', round ' + p.t.iteration}, ${p.t.agent || ''}: ${fmt(p.y)} ${unit}, ${pass ? 'passes' : 'fails'}${p.t.chosen ? ', adopted' : ''}`;
      svg += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.t.chosen ? 5 : 4}" fill="${p.t.chosen ? color : '#191f22'}" stroke="${color}" stroke-width="2" data-test="${esc(p.t.id)}" tabindex="0" role="button" aria-label="${esc(label)}"><title>${esc(label)}</title></circle>`;
    }
    return svg + '</svg>';
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {chartSvg};
  else root.DesignerCharts = {chartSvg};
})(typeof window !== 'undefined' ? window : globalThis);
