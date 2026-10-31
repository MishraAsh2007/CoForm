'use strict';
// Minimal 3D viewer for finite-element results: flat-shaded, painter-sorted triangles on a canvas.
// Drag to rotate, wheel to zoom. Colour = stress or bend on one sequential ramp (dark = low, light = high).
(function () {
  const RAMP = ['#104281', '#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef', '#b4d2f5', '#dbe9fb'];
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const RGB = RAMP.map(hex);
  const OVER = '#d95926', OVER_RGB = hex(OVER);  // above the limit

  function Viewer(canvas, legend) {
    const ctx = canvas.getContext('2d');
    let field = null, overlay = {supports: [], loads: []}, yaw = -0.7, pitch = 0.45, zoom = 1, colorBy = 'stress', exaggerate = true;
    let center = [0, 0, 0], size = 1, scale = 1, maxValue = 1, values = [], limits = {};

    // limits: {stress: allowable MPa, bend: allowed mm}. Colours then mean the same thing for every
    // design (share of the limit), and anything over the limit turns orange.
    function setField(f, o, l) {
      field = f; overlay = o || overlay; limits = l || limits;
      const P = f.positions, lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
      for (const p of P) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
      center = lo.map((v, k) => (v + hi[k]) / 2); size = Math.max(...hi.map((v, k) => v - lo[k])) || 1;
      const umax = Math.max(1e-12, ...f.displacement.map(u => Math.hypot(u[0], u[1], u[2])));
      scale = exaggerate ? 0.12 * size / umax : 1;
      recolor(); draw();
    }
    function recolor() {
      if (!field) return;
      values = colorBy === 'stress' ? field.stress : field.displacement.map(u => Math.hypot(u[0], u[1], u[2]));
      const limit = limits[colorBy];
      const sorted = [...values].sort((a, b) => a - b);
      maxValue = limit || sorted[Math.floor(sorted.length * 0.995)] || 1e-9;  // without a limit, ignore single-node spikes
      if (legend) {
        legend.innerHTML = '';
        const label = colorBy === 'stress' ? 'Stress (MPa)' : 'Bend (mm)';
        legend.append(Object.assign(document.createElement('span'), {textContent: `${label}: 0`}));
        for (const c of RAMP) { const s = document.createElement('i'); s.style.background = c; legend.append(s); }
        legend.append(Object.assign(document.createElement('span'), {textContent: limit ? `${maxValue.toPrecision(3)} (${colorBy === 'stress' ? 'allowable' : 'limit'})` : `${maxValue.toPrecision(3)}+`}));
        if (limit) { const s = document.createElement('i'); s.style.background = OVER; legend.append(s, Object.assign(document.createElement('span'), {textContent: 'over'})); }
        legend.append(Object.assign(document.createElement('span'), {className: 'muted', textContent: exaggerate ? `· shape exaggerated ×${scale.toPrecision(2)}` : '· true scale'}));
      }
    }
    function rotate(p) {
      const x = p[0] - center[0], y = p[1] - center[1], z = p[2] - center[2];
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const x1 = cy * x - sy * y, y1 = sy * x + cy * y;      // about z (up)
      return [x1, cp * y1 - sp * z, sp * y1 + cp * z];        // tilt about screen x; [screenX, depth, screenY]
    }
    function draw() {
      const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
      canvas.width = w * ratio; canvas.height = h * ratio; ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.fillStyle = '#141a1c'; ctx.fillRect(0, 0, w, h);
      if (!field) return;
      const k = zoom * 0.8 * Math.min(w, h) / size, ox = w / 2, oy = h / 2;
      const S = field.positions.map((p, i) => { const u = field.displacement[i];
        return rotate([p[0] + scale * u[0], p[1] + scale * u[1], p[2] + scale * u[2]]); });
      const order = field.triangles.map((t, i) => [i, (S[t[0]][1] + S[t[1]][1] + S[t[2]][1]) / 3]).sort((a, b) => b[1] - a[1]);
      const light = [0.35, -0.6, 0.72];
      for (const [i] of order) {
        const [a, b, c] = field.triangles[i], A = S[a], B = S[b], C = S[c];
        const u = [B[0] - A[0], B[1] - A[1], B[2] - A[2]], v = [C[0] - A[0], C[1] - A[1], C[2] - A[2]];
        const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
        const len = Math.hypot(...n) || 1;
        if (n[1] / len > 0.02) continue;  // back face (normals point toward the viewer along -depth)
        const shade = 0.45 + 0.55 * Math.abs((n[0] * light[0] + n[1] * light[1] + n[2] * light[2]) / len);
        const value = (values[a] + values[b] + values[c]) / 3 / maxValue;
        const rgb = value > 1 && limits[colorBy] ? OVER_RGB : RGB[Math.max(0, Math.min(RGB.length - 1, Math.round(value * (RGB.length - 1))))];
        ctx.fillStyle = `rgb(${rgb.map(x => Math.round(x * shade)).join(',')})`;
        ctx.beginPath(); ctx.moveTo(ox + k * A[0], oy - k * A[2]); ctx.lineTo(ox + k * B[0], oy - k * B[2]); ctx.lineTo(ox + k * C[0], oy - k * C[2]); ctx.closePath();
        ctx.fill(); ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 0.6; ctx.stroke();
      }
      // Supports: grey boxes. Loads: orange arrows pointing along the force.
      ctx.lineWidth = 1.5;
      for (const s of overlay.supports) {
        ctx.strokeStyle = '#99abae'; ctx.setLineDash([4, 3]); boxPath(s.min, s.max, k, ox, oy); ctx.setLineDash([]);
        label(s.name, s.max, k, ox, oy, '#99abae');
      }
      for (const l of overlay.loads) {
        const mid = l.min.map((v, i) => (v + l.max[i]) / 2), f = l.force_n, fl = Math.hypot(...f) || 1;
        const tail = mid.map((v, i) => v - f[i] / fl * size * 0.25);
        const P = rotate(mid), T = rotate(tail);
        ctx.strokeStyle = ctx.fillStyle = '#d95926'; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(ox + k * T[0], oy - k * T[2]); ctx.lineTo(ox + k * P[0], oy - k * P[2]); ctx.stroke();
        const ang = Math.atan2(-(P[2] - T[2]), P[0] - T[0]);
        ctx.beginPath(); ctx.moveTo(ox + k * P[0], oy - k * P[2]);
        ctx.lineTo(ox + k * P[0] - 10 * Math.cos(ang - 0.4), oy - k * P[2] - 10 * Math.sin(ang - 0.4));
        ctx.lineTo(ox + k * P[0] - 10 * Math.cos(ang + 0.4), oy - k * P[2] - 10 * Math.sin(ang + 0.4)); ctx.closePath(); ctx.fill();
        label(`${l.name} (${fl.toPrecision(3)} N)`, tail, k, ox, oy, '#f2b9a3');
      }
    }
    function boxPath(lo, hi, k, ox, oy) {
      const c = [0, 1, 2, 3, 4, 5, 6, 7].map(i => rotate([i & 1 ? hi[0] : lo[0], i & 2 ? hi[1] : lo[1], i & 4 ? hi[2] : lo[2]]));
      ctx.beginPath();
      for (const [a, b] of [[0,1],[1,3],[3,2],[2,0],[4,5],[5,7],[7,6],[6,4],[0,4],[1,5],[2,6],[3,7]]) {
        ctx.moveTo(ox + k * c[a][0], oy - k * c[a][2]); ctx.lineTo(ox + k * c[b][0], oy - k * c[b][2]); }
      ctx.stroke();
    }
    function label(text, at, k, ox, oy, color) {
      const p = rotate(at); ctx.fillStyle = color; ctx.font = '12px system-ui'; ctx.fillText(text, ox + k * p[0] + 6, oy - k * p[2] - 6);
    }
    let drag = null;
    canvas.addEventListener('pointerdown', e => { drag = [e.clientX, e.clientY, yaw, pitch]; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => { if (!drag) return;
      yaw = drag[2] + (e.clientX - drag[0]) * 0.01; pitch = Math.max(-1.5, Math.min(1.5, drag[3] + (e.clientY - drag[1]) * 0.01)); draw(); });
    canvas.addEventListener('pointerup', () => { drag = null; });
    canvas.addEventListener('wheel', e => { e.preventDefault(); zoom = Math.max(0.3, Math.min(6, zoom * (e.deltaY < 0 ? 1.12 : 0.89))); draw(); }, {passive: false});
    window.addEventListener('resize', draw);
    return {
      setField,
      setColorBy(mode) { colorBy = mode; recolor(); draw(); },
      setExaggerate(on) { exaggerate = on; if (field) setField(field, overlay, limits); },
      reset() { yaw = -0.7; pitch = 0.45; zoom = 1; draw(); },
    };
  }
  window.Viewer3D = Viewer;
})();
