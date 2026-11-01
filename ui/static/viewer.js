'use strict';
// Mesh viewer for the enclosure UI. Adapted from the team's scienceclaw/static/viewer3d.js (Jash_ver):
// same approach (flat-shaded, painter-sorted triangles on a canvas, drag to rotate, wheel to zoom), extended to
// several meshes per scene, transparency, a lid lift and groups that can be shown or hidden.
// WebGL with a depth buffer when available (canvas painter fallback). Several canvases can share one camera, so "first vs final" rotate together.
(function () {
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

  function Camera() {
    const cam = {yaw: -0.75, pitch: 0.55, zoom: 1, lift: 0, hidden: {}, views: []};
    cam.redraw = () => cam.views.forEach(v => v.draw());
    cam.reset = () => { cam.yaw = -0.75; cam.pitch = 0.55; cam.zoom = 1; cam.redraw(); };
    return cam;
  }

  function CanvasView(canvas, meshes, cam, bg) {
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
      const k = cam.zoom * 0.8 * Math.min(w, h) / size, ox = w / 2, oy = h / 2 + 0.02 * h;
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

  // WebGL renderer with a depth buffer (correct overlaps); same camera and controls as the canvas one.
  const VS = `attribute vec3 p; attribute vec3 n;
uniform vec3 c; uniform float lift; uniform vec2 cs; uniform vec2 cps; uniform vec2 sc; uniform float dz; uniform float oy;
varying float sh;
void main(){ vec3 q = p - c; q.z += lift;
  float x1 = cs.x*q.x - cs.y*q.y; float y1 = cs.y*q.x + cs.x*q.y;
  float d = cps.x*y1 - cps.y*q.z; float sy = cps.y*y1 + cps.x*q.z;
  float nx1 = cs.x*n.x - cs.y*n.y; float ny1 = cs.y*n.x + cs.x*n.y;
  vec3 rn = normalize(vec3(nx1, cps.x*ny1 - cps.y*n.z, cps.y*ny1 + cps.x*n.z));
  sh = 0.42 + 0.58*abs(dot(rn, normalize(vec3(0.35, -0.6, 0.72))));
  gl_Position = vec4(x1*sc.x, sy*sc.y + oy, d*dz, 1.0); }`;
  const FS = `precision mediump float; uniform vec3 col; uniform float a; varying float sh;
void main(){ gl_FragColor = vec4(col*sh, a); }`;

  function View(canvas, meshes, cam, bg) {
    let gl = null;
    try { gl = canvas.getContext('webgl', {antialias: true, preserveDrawingBuffer: true, premultipliedAlpha: false}); } catch (e) { gl = null; }
    if (!gl) return CanvasView(canvas, meshes, cam, bg);
    const sh = (t, src) => { const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return CanvasView(canvas, meshes, cam, bg);
    gl.useProgram(prog);
    const U = {}; for (const u of ['c', 'lift', 'cs', 'cps', 'sc', 'dz', 'oy', 'col', 'a']) U[u] = gl.getUniformLocation(prog, u);
    const aP = gl.getAttribLocation(prog, 'p'), aN = gl.getAttribLocation(prog, 'n');
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const m of meshes) for (let i = 0; i < m.v.length; i += 3) for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], m.v[i + k]); hi[k] = Math.max(hi[k], m.v[i + k]); }
    const center = lo.map((v, k) => (v + hi[k]) / 2);
    const size = Math.max(...hi.map((v, k) => v - lo[k])) || 1;
    const bgc = hex(bg).map(x => x / 255);
    const bufs = meshes.map(m => {
      const F = m.f, V = m.v, P = new Float32Array(F.length * 3), N = new Float32Array(F.length * 3);
      for (let t = 0; t < F.length; t += 3) {
        const a = 3 * F[t], b = 3 * F[t + 1], c = 3 * F[t + 2];
        const ux = V[b] - V[a], uy = V[b + 1] - V[a + 1], uz = V[b + 2] - V[a + 2];
        const vx = V[c] - V[a], vy = V[c + 1] - V[a + 1], vz = V[c + 2] - V[a + 2];
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        for (let j = 0; j < 3; j++) { const s = 3 * F[t + j], o = 3 * (t + j);
          P[o] = V[s]; P[o + 1] = V[s + 1]; P[o + 2] = V[s + 2]; N[o] = nx; N[o + 1] = ny; N[o + 2] = nz; }
      }
      const bp = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bp); gl.bufferData(gl.ARRAY_BUFFER, P, gl.STATIC_DRAW);
      const bn = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bn); gl.bufferData(gl.ARRAY_BUFFER, N, gl.STATIC_DRAW);
      return {bp, bn, count: F.length, col: hex(m.color).map(x => x / 255)};
    });
    function draw() {
      const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
      canvas.width = w * ratio; canvas.height = h * ratio;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(bgc[0], bgc[1], bgc[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      const k = cam.zoom * 0.8 * Math.min(w, h) / size;
      gl.uniform3f(U.c, center[0], center[1], center[2]);
      gl.uniform2f(U.cs, Math.cos(cam.yaw), Math.sin(cam.yaw));
      gl.uniform2f(U.cps, Math.cos(cam.pitch), Math.sin(cam.pitch));
      gl.uniform2f(U.sc, k / (w / 2), k / (h / 2));
      gl.uniform1f(U.dz, 1 / (size * 1.6)); gl.uniform1f(U.oy, -0.04);
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL);
      for (const pass of [0, 1]) {
        if (pass) { gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); }
        else { gl.disable(gl.BLEND); gl.depthMask(true); }
        meshes.forEach((m, i) => {
          if (cam.hidden[m.group] || (m.opacity < 0.99) !== !!pass) return;
          const b = bufs[i];
          gl.uniform1f(U.lift, m.group === 'lid' ? cam.lift : 0);
          gl.uniform3f(U.col, b.col[0], b.col[1], b.col[2]); gl.uniform1f(U.a, m.opacity);
          gl.bindBuffer(gl.ARRAY_BUFFER, b.bp); gl.enableVertexAttribArray(aP); gl.vertexAttribPointer(aP, 3, gl.FLOAT, false, 0, 0);
          gl.bindBuffer(gl.ARRAY_BUFFER, b.bn); gl.enableVertexAttribArray(aN); gl.vertexAttribPointer(aN, 3, gl.FLOAT, false, 0, 0);
          gl.drawArrays(gl.TRIANGLES, 0, b.count);
        });
      }
      gl.depthMask(true);
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

  window.SCViewer = {Camera, View, CanvasView};
})();
