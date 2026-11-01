"""3D viewer component: one or more canvases with a shared camera, group toggles and a lid-lift slider.
Everything runs in the browser (no Streamlit rerun when you rotate, lift the lid or toggle the board)."""
import json
from pathlib import Path

import streamlit.components.v1 as components

JS = (Path(__file__).parent / "static" / "viewer.js").read_text(encoding="utf-8")

GROUPS = [("base", "Base"), ("lid", "Lid"), ("board", "Raspberry Pi 5"), ("keepout", "Board keep-out")]


def html(panels, height=460, lift=0, hidden=("keepout",), controls=True, bg="#141a1c"):
    """panels: [{'title': str, 'subtitle': str, 'meshes': [...]}]."""
    present = {m["group"] for p in panels for m in p["meshes"]}
    data = json.dumps([{"title": p.get("title", ""), "subtitle": p.get("subtitle", ""), "meshes": p["meshes"]}
                       for p in panels], separators=(",", ":"))
    boxes = "".join(
        f'<label><input type="checkbox" data-g="{g}" {"" if g in hidden else "checked"}> {label}</label>'
        for g, label in GROUPS if g in present)
    lift_ctl = (f'<label class="lift">Lift lid <input type="range" id="lift" min="0" max="60" value="{lift}">'
                f'<span id="liftv">{lift} mm</span></label>') if "lid" in present else ""
    ctl = (f'<div class="ctl">{boxes}{lift_ctl}<button id="reset">Reset view</button>'
           f'<span class="hint">Drag to rotate · scroll to zoom</span></div>') if controls else ""
    cols = len(panels)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:transparent;color:#edf4f1;font:13px system-ui,-apple-system,"Segoe UI",sans-serif}}
.grid{{display:grid;grid-template-columns:repeat({cols},minmax(0,1fr));gap:12px}}
.panel{{background:#191f22;border:1px solid #30393c;border-radius:12px;overflow:hidden}}
.panel h4{{margin:0;padding:10px 14px 0;font-size:14px;font-weight:600}}
.panel small{{display:block;padding:0 14px 6px;color:#99abae;font-size:12px}}
canvas{{display:block;width:100%;height:{height}px;cursor:grab;touch-action:none;background:{bg}}}
.ctl{{display:flex;flex-wrap:wrap;gap:16px;align-items:center;margin:10px 2px 0;color:#99abae}}
.ctl label{{display:inline-flex;gap:6px;align-items:center;cursor:pointer;color:#edf4f1}}
.ctl input[type=checkbox]{{accent-color:#bbefd5;width:15px;height:15px}}
.ctl input[type=range]{{accent-color:#bbefd5;width:160px}}
.ctl button{{border:1px solid #4a595e;background:transparent;color:#edf4f1;border-radius:6px;padding:5px 12px;cursor:pointer}}
.ctl button:hover{{border-color:#bbefd5;color:#bbefd5}}.hint{{font-size:12px}}
</style></head><body>
<div class="grid" id="grid"></div>{ctl}
<script>{JS}</script>
<script>
const P = {data};
const cam = SCViewer.Camera();
const grid = document.getElementById('grid');
for (const p of P) {{
  const d = document.createElement('div'); d.className = 'panel';
  if (p.title) d.innerHTML = '<h4></h4><small></small>';
  const c = document.createElement('canvas'); d.append(c); grid.append(d);
  if (p.title) {{ d.querySelector('h4').textContent = p.title; d.querySelector('small').textContent = p.subtitle; }}
  SCViewer.View(c, p.meshes, cam, '{bg}');
}}
cam.lift = {int(lift)};
document.querySelectorAll('input[data-g]').forEach(b => {{ cam.hidden[b.dataset.g] = !b.checked;
  b.addEventListener('change', () => {{ cam.hidden[b.dataset.g] = !b.checked; cam.redraw(); }}); }});
const L = document.getElementById('lift');
if (L) L.addEventListener('input', () => {{ cam.lift = +L.value; document.getElementById('liftv').textContent = L.value + ' mm'; cam.redraw(); }});
const R = document.getElementById('reset'); if (R) R.addEventListener('click', cam.reset);
window.addEventListener('resize', cam.redraw);
requestAnimationFrame(cam.redraw);
</script></body></html>"""


def show(panels, height=460, lift=0, hidden=("keepout",), controls=True):
    extra = 64 if controls else 8
    title = 50 if any(p.get("title") for p in panels) else 0
    components.html(html(panels, height, lift, hidden, controls), height=height + extra + title, scrolling=False)
