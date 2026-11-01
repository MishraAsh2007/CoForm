'use strict';
// Mesh viewer for the enclosure UI. Adapted from the team's scienceclaw/static/viewer3d.js (Jash_ver):
// same approach (flat-shaded, painter-sorted triangles on a canvas, drag to rotate, wheel to zoom), extended to
// several meshes per scene, transparency, a lid lift and groups that can be shown or hidden.
// Several canvases can share one camera, so "first vs final" rotate together.
(function () {
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

  function Camera() {
    const cam = {yaw: -0.75, pitch: 0.55, zoom: 1, lift: 0, hidden: {}, views: []};
    cam.redraw = () => cam.views.forEach(v => v.draw());
    cam.reset = () => { cam.yaw = -0.75; cam.pitch = 0.55; cam.zoom = 1; cam.redraw(); };
    return cam;
  }

  function View(canvas, meshes, cam, bg) {
    const ctx = canvas.getContext('2d');
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const m of meshes) for (let i = 0; i < m.v.length; i += 3) for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], m.v[i + k]); hi[k] = Math.max(hi[k], m.v[i + k]);
    }
    const center = lo.map((v, k) => (v + hi[k]) / 2);
    const size = Math.max(...hi.map((v, k) => v - lo[k])) || 1;
    const cols = meshes.map(m => hex(m.color));

    function draw() {
      const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
      canvas.width = w * ratio; canvas.height = h * ratio; ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
      const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
      const k = cam.zoom * 0.95 * Math.min(w, h) / size, ox = w / 2, oy = h / 2 + 0.04 * h;
      const tris = [];
      const light = [0.35, -0.6, 0.72];
      meshes.forEach((m, mi) => {
        if (cam.hidden[m.group]) return;
        const dz = m.group === 'lid' ? cam.lift : 0;
        const V = m.v, n = V.length / 3, S = new Float32Array(n * 3);
        for (let i = 0; i < n; i++) {
          const x = V[3 * i] - center[0], y = V[3 * i + 1] - center[1], z = V[3 * i + 2] + dz - center[2];
          const x1 = cy * x - sy * y, y1 = sy * x + cy * y;
          S[3 * i] = x1; S[3 * i + 1] = cp * y1 - sp * z; S[3 * i + 2] = sp * y1 + cp * z;
        }
        const F = m.f, opaque = m.opacity >= 0.99;
        for (let t = 0; t < F.length; t += 3) {
          const a = 3 * F[t], b = 3 * F[t + 1], c = 3 * F[t + 2];
          const ux = S[b] - S[a], uy = S[b + 1] - S[a + 1], uz = S[b + 2] - S[a + 2];
          const vx = S[c] - S[a], vy = S[c + 1] - S[a + 1], vz = S[c + 2] - S[a + 2];
          const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
          const len = Math.hypot(nx, ny, nz) || 1;
          if (opaque && ny / len > 0.02) continue;  // back face
          const shade = 0.42 + 0.58 * Math.abs((nx * light[0] + ny * light[1] + nz * light[2]) / len);
          tris.push([(S[a + 1] + S[b + 1] + S[c + 1]) / 3, mi, shade,
            S[a], S[a + 2], S[b], S[b + 2], S[c], S[c + 2]]);
        }
      });
      tris.sort((p, q) => q[0] - p[0]);
      for (const t of tris) {
        const m = meshes[t[1]], rgb = cols[t[1]], s = t[2];
        ctx.globalAlpha = m.opacity;
        ctx.fillStyle = `rgb(${Math.round(rgb[0] * s)},${Math.round(rgb[1] * s)},${Math.round(rgb[2] * s)})`;
        ctx.beginPath(); ctx.moveTo(ox + k * t[3], oy - k * t[4]); ctx.lineTo(ox + k * t[5], oy - k * t[6]);
        ctx.lineTo(ox + k * t[7], oy - k * t[8]); ctx.closePath(); ctx.fill();
        if (m.opacity >= 0.99) { ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 0.5; ctx.stroke(); }
      }
      ctx.globalAlpha = 1;
    }

    let drag = null;
    canvas.addEventListener('pointerdown', e => { drag = [e.clientX, e.clientY, cam.yaw, cam.pitch]; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => { if (!drag) return;
      cam.yaw = drag[2] + (e.clientX - drag[0]) * 0.01;
      cam.pitch = Math.max(-1.5, Math.min(1.5, drag[3] + (e.clientY - drag[1]) * 0.01)); cam.redraw(); });
    canvas.addEventListener('pointerup', () => { drag = null; });
    canvas.addEventListener('wheel', e => { e.preventDefault();
      cam.zoom = Math.max(0.3, Math.min(8, cam.zoom * (e.deltaY < 0 ? 1.12 : 0.89))); cam.redraw(); }, {passive: false});
    const view = {draw};
    cam.views.push(view);
    return view;
  }

  window.SCViewer = {Camera, View};
})();
