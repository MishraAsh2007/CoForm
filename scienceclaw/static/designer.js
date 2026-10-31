'use strict';
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v, d = 2) => Number(v).toLocaleString(undefined, {maximumFractionDigits: d});
const COLORS = {line: '#3987e5', dot: '#3987e5', fail: '#d95926', limit: '#d03b3b', grid: '#2c3437', axis: '#3d484b', muted: '#93a6aa'};
let config, state = null, projectId = null, busy = false, selected = null, viewer = null, pollTimer = null, preformReady = false, presets = [];
let follow = true;  // show the agents' newest design until the user picks one
const fields = {};

async function api(path, data) {
  const r = await fetch('/api' + path, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const out = await r.json();
  if (!r.ok) throw new Error(typeof out.detail === 'string' ? out.detail : JSON.stringify(out.detail));
  return out;
}
function notice(msg, error = false) { $('notice').hidden = false; $('notice').textContent = msg; $('notice').classList.toggle('error', error); }
async function work(fn, msg) {
  if (busy) return; busy = true; notice(msg || 'Working…');
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await fn(); } catch (e) { notice(e.message, true); }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); gates(); }
}
const live = () => $('agent-mode').value === 'live';
const bend = r => Math.max(...r.loads.map(l => Math.abs(l.deflection_mm)));
const testLabel = t => `${t.iteration === 0 ? 'First draft' : 'Round ' + t.iteration + ' · ' + t.agent}${t.chosen ? ' (adopted)' : ''} · ${t.results.passed ? 'passes' : 'fails'} · ${num(t.results.mass_g, 1)} g`;

// ---------------------------------------------------------------- loading
async function listProjects(select) {
  const items = await api('/designer/projects');
  $('project-picker').innerHTML = '<option value="">New part</option>' + items.slice().reverse().map(p => `<option value="${esc(p.id)}">${esc(p.prompt.slice(0, 60))}</option>`).join('');
  if (select) $('project-picker').value = select;
}
async function load(id) {
  clearTimeout(pollTimer);
  try {
    projectId = id; state = await api('/designer/projects/' + id);
    render();
  } finally {
    // Keep refreshing even if one request fails (for example while the server restarts).
    if (projectId === id) pollTimer = setTimeout(() => load(id).catch(() => {}), !state || state.progress.running ? 2000 : 15000);
  }
}

// ---------------------------------------------------------------- render
function render() {
  $('project').hidden = false;
  const spec = state.specs.at(-1)?.spec, p = state.progress, best = state.best;
  $('part-name').textContent = spec ? spec.name : 'Drafting…';
  const last = p.log.at(-1)?.message || '';
  $('part-status').textContent = p.running ? `Working (${p.stage || ''}): ${last}` : (p.stage === 'ready' ? 'Done: ready to view and print' : p.stage === 'error' ? last : last || 'Idle');
  $('part-best').textContent = best ? `${num(best.results.mass_g, 1)} g · bends ${num(bend(best.results), 3)} mm · stress ${num(best.results.peak_stress_mpa, 1)} MPa` : 'No passing design yet';
  $('log').innerHTML = p.log.slice(-12).map(e => `<p><time>${esc(new Date(e.t).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}))}</time> ${esc(e.message)}</p>`).join('') || '<p class="muted">Starting…</p>';
  renderPicker(); renderSpec(spec); renderRounds(); renderCharts(); renderPrint(); renderMeasurements(); gates();
}
function renderPicker() {
  const tests = state.tests, current = $('test-picker').value;
  $('test-picker').innerHTML = tests.slice().reverse().map(t => `<option value="${esc(t.id)}">${esc(testLabel(t))}${state.best?.id === t.id ? ' ★ best' : ''}</option>`).join('');
  const newest = state.progress.running ? tests.at(-1) : (state.best || tests.filter(t => t.chosen).at(-1) || tests.at(-1));
  const pick = !follow && tests.some(t => t.id === current) ? current : newest?.id;
  if (pick) { $('test-picker').value = pick; if (pick !== selected) showTest(pick); }
}
async function showTest(id) {
  selected = id;
  const t = state.tests.find(t => t.id === id); if (!t) return;
  const r = t.results;
  $('checks-title').textContent = `${t.iteration === 0 ? 'First draft' : 'Round ' + t.iteration + ', ' + t.agent + "'s proposal"}: ${r.passed ? 'passes every check' : 'fails'}`;
  $('checks').innerHTML = r.checks.map(c => `<tr class="${c.passed ? '' : 'failed'}"><td>${c.passed ? '✓' : '✗'} ${esc(c.label)}</td><td>${num(c.value, 3)} ${esc(c.unit)}</td></tr>`).join('')
    + `<tr><td>Mass</td><td>${num(r.mass_g, 2)} g</td></tr><tr><td>Size</td><td>${r.extents_mm.map(v => num(v, 1)).join(' × ')} mm</td></tr>`;
  $('test-meta').textContent = `${Object.entries(t.changes).length ? 'Changed: ' + Object.entries(t.changes).map(([k, v]) => `${k} → ${num(v, 2)}`).join(', ') + '. ' : ''}${t.reason || ''} · ${r.solver}, ${num(r.nodes, 0)} nodes, ${num(r.mesh_mm, 1)} mm elements, ${r.seconds} s.`;
  $('step-link').href = `/api/designer/projects/${projectId}/tests/${id}/step`;
  $('stl-link').href = `/api/designer/projects/${projectId}/tests/${id}/stl`;
  try {
    fields[id] = fields[id] || await (await fetch(`/api/designer/projects/${projectId}/tests/${id}/field`)).json();
    const spec = state.specs.find(s => s.id === t.spec_id)?.spec;
    if (selected === id) viewer.setField(fields[id], r.regions || {supports: [], loads: []}, {stress: r.allowable_stress_mpa, bend: spec?.requirements.max_displacement_mm});
  } catch (e) { notice('Could not load the 3D result: ' + e.message, true); }
}
function renderSpec(spec) {
  const rec = state.specs.at(-1);
  if (!spec) { $('spec-summary').textContent = 'The drafting agent is reading the request…'; return; }
  $('spec-summary').textContent = spec.summary;
  $('assumptions').innerHTML = (rec.assumptions || []).map(a => `<li>${esc(a)}</li>`).join('');
  const values = (state.best || state.tests.filter(t => t.chosen).at(-1))?.values || {};
  $('params').innerHTML = Object.entries(spec.parameters).map(([n, p]) => `<tr><td>${esc(n.replaceAll('_', ' '))}</td><td>${num(values[n] ?? p.value, 2)} mm</td><td>${num(p.min, 1)}–${num(p.max, 1)}</td><td>${esc(p.role)}</td></tr>`).join('');
  const req = spec.requirements;
  $('spec-meta').textContent = `Requirements: bend ≤ ${req.max_displacement_mm} mm, safety factor ${req.safety_factor}${req.max_mass_g ? ', mass ≤ ' + req.max_mass_g + ' g' : ''}. Loads: ${spec.loads.map(l => `${l.name} ${num(Math.hypot(...l.force_n), 3)} N`).join('; ')}. Drafted by ${rec.source === 'llm' ? 'the drafting agent (live AI)' : 'the built-in template (rule-based mode)'}.`;
}
function renderRounds() {
  $('rounds').innerHTML = state.rounds.slice().reverse().map(r => {
    const tests = state.tests.filter(t => t.iteration === r.iteration);
    const props = r.proposals.map(p => {
      const t = tests.find(t => t.agent === p.agent), votes = r.reviews.filter(v => v.proposal_id === p.id);
      return `<div class="tl-prop ${t?.chosen ? 'chosen' : ''} ${p.rejected ? 'invalid' : ''}"><div><b>${esc(p.agent)}</b> proposed ${esc(Object.entries(p.changes).map(([k, v]) => `${k} → ${num(v, 2)}`).join(', '))}${t?.chosen ? ' <span class="pill good">adopted</span>' : ''}</div><small>${esc(p.reason)}</small>
        ${t ? `<small>Tested: ${t.results.passed ? 'passes' : 'fails'} · ${num(t.results.mass_g, 1)} g · bend ${num(bend(t.results), 3)} mm · stress ${num(t.results.peak_stress_mpa, 1)} MPa <button class="text-button" data-test="${esc(t.id)}">view in 3D</button></small>` : ''}
        ${p.rejected ? `<small class="bad">${esc(p.rejected)}</small>` : ''}<div class="votes">${votes.map(v => `<span class="vote ${v.support ? 'yes' : 'no'}" title="${esc(v.reason)}">${v.support ? '✓' : '✗'} ${esc(v.agent)}</span>`).join('')}</div></div>`;
    }).join('');
    const notes = Object.entries(r.notes || {}).filter(([k]) => k !== 'measurement_concerns').map(([k, v]) => `<small><b>${esc(k)}:</b> ${esc(v)}</small>`).join('');
    return `<div class="tl-item"><span class="tl-icon">${r.iteration}</span><div class="tl-body"><div class="tl-head"><b>Round ${r.iteration}: ${r.status === 'improved' ? 'adopted ' + esc(r.chosen_agent) + "'s change" : 'nothing better, converged'}</b><small class="muted">${r.mode === 'llm' ? 'live AI' : 'rule-based'}${r.usage?.calls ? ' · ' + r.usage.calls + ' AI calls' : ''}</small></div><div class="tl-props">${props || '<small class="muted">No proposals.</small>'}</div>${notes}</div></div>`;
  }).join('');
  const plan = [...state.rounds].reverse().find(r => r.notes?.measurement && !/^No usable/.test(r.notes.measurement))?.notes.measurement;
  if (plan) $('real-test-plan').textContent = 'Measurement agent\'s test plan: ' + plan;
}
$('rounds').addEventListener('click', e => { const id = e.target.dataset?.test; if (id) { pin(); $('test-picker').value = id; showTest(id); $('viewer').scrollIntoView({behavior: 'smooth', block: 'center'}); } });

// ---------------------------------------------------------------- charts
function lineChart(el, tests, value, {limit, unit}) {
  const w = el.clientWidth || 300, h = 170, m = {l: 44, r: 10, t: 12, b: 26};
  const pts = tests.map(t => ({x: t.iteration, y: value(t), t}));
  const ys = pts.map(p => p.y).concat(limit ? [limit] : []);
  const ymax = Math.max(...ys) * 1.1 || 1, xmax = Math.max(1, ...pts.map(p => p.x));
  const X = x => m.l + (w - m.l - m.r) * x / xmax, Y = y => h - m.b - (h - m.t - m.b) * y / ymax;
  const chosen = pts.filter(p => p.t.chosen).sort((a, b) => a.x - b.x);
  let svg = `<svg width="${w}" height="${h}" role="img">`;
  for (let k = 0; k <= 4; k++) { const y = ymax * k / 4; svg += `<line x1="${m.l}" x2="${w - m.r}" y1="${Y(y)}" y2="${Y(y)}" stroke="${COLORS.grid}"/><text x="${m.l - 6}" y="${Y(y) + 4}" text-anchor="end" fill="${COLORS.muted}" font-size="11">${num(y, y < 10 ? 2 : 0)}</text>`; }
  for (let x = 0; x <= xmax; x++) svg += `<text x="${X(x)}" y="${h - 8}" text-anchor="middle" fill="${COLORS.muted}" font-size="11">${x === 0 ? 'draft' : x}</text>`;
  if (limit) svg += `<line x1="${m.l}" x2="${w - m.r}" y1="${Y(limit)}" y2="${Y(limit)}" stroke="${COLORS.limit}" stroke-dasharray="5 4" stroke-width="1.5"/><text x="${w - m.r}" y="${Y(limit) - 5}" text-anchor="end" fill="#f2b9a3" font-size="11">limit ${num(limit, 3)} ${unit}</text>`;
  if (chosen.length > 1) svg += `<polyline fill="none" stroke="${COLORS.line}" stroke-width="2" points="${chosen.map(p => `${X(p.x)},${Y(p.y)}`).join(' ')}"/>`;
  for (const p of pts) svg += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.t.chosen ? 5 : 4}" fill="${p.t.chosen ? (p.t.results.passed ? COLORS.dot : COLORS.fail) : '#191f22'}" stroke="${p.t.results.passed ? COLORS.dot : COLORS.fail}" stroke-width="2" data-test="${esc(p.t.id)}"><title>${esc(testLabel(p.t))}: ${num(p.y, 3)} ${unit}</title></circle>`;
  el.innerHTML = svg + '</svg>';
}
function renderCharts() {
  const spec = state.specs.at(-1)?.spec; if (!spec || !state.tests.length) return;
  const t = state.tests, r0 = t[0].results;
  lineChart($('chart-mass'), t, x => x.results.mass_g, {limit: spec.requirements.max_mass_g, unit: 'g'});
  lineChart($('chart-bend'), t, x => bend(x.results), {limit: spec.requirements.max_displacement_mm, unit: 'mm'});
  lineChart($('chart-stress'), t, x => x.results.peak_stress_mpa, {limit: r0.allowable_stress_mpa, unit: 'MPa'});
}
document.querySelector('.charts-3').addEventListener('click', e => { const id = e.target.closest('[data-test]')?.dataset.test; if (id) { pin(); $('test-picker').value = id; showTest(id); } });

// ---------------------------------------------------------------- print & real test
function renderPrint() {
  const review = state.print_reviews.at(-1);
  $('print-review').innerHTML = review ? `<div class="decision-box ${review.status === 'approved' ? '' : 'warn'}"><b>${review.status === 'approved' ? 'Approved for the printer queue' : 'Not approved'}</b>
    <p>PreForm checks: ${esc(review.preform.grade)}${review.estimate?.total_print_time_s ? ` · ${num(review.estimate.total_print_time_s / 3600, 2)} h` : ''}${review.material_usage?.volume_ml ? ` · ${num(review.material_usage.volume_ml)} mL resin with supports` : ''}${(review.preform.warnings || []).length ? ' · warnings: ' + esc(review.preform.warnings.join('; ')) : ''}</p>
    ${review.gates.length ? `<p class="bad">Blocked: ${esc(review.gates.join('; '))}</p>` : ''}<ul>${review.votes.map(v => `<li><b>${esc(v.agent)}</b> ${v.support ? 'approves' : 'objects'}: ${esc(v.reason)}</li>`).join('')}</ul></div>` : '';
}
function renderMeasurements() {
  $('measurements').innerHTML = state.measurements.slice().reverse().map(m => `<div class="decision-box ${m.recheck.passed ? '' : 'warn'}"><b>Measured ${num(m.measured_mm, 3)} mm vs predicted ${num(m.predicted_mm, 3)} mm</b><p>Effective stiffness ${num(m.effective_modulus_pa / 1e9, 2)} GPa (datasheet 2.23 GPa). With it, the design ${m.recheck.passed ? 'still passes every check' : 'no longer passes: let the agents keep improving it'}.</p></div>`).join('');
}
function gates() {
  if (!state) return;
  const running = state.progress.running, best = state.best;
  const set = (id, reason) => { $(id).disabled = busy || !!reason; $(id).title = reason || ''; return reason; };
  set('run-more', running && 'The agents are working');
  set('fusion-view', !cadFusion && 'Fusion is not connected: in Fusion run Utilities › Add-Ins › ScienceClaw (reload it after updating)');
  $('fusion-hint').textContent = cadFusion ? '' : 'Fusion not connected: you can still download the STEP and open it in Fusion yourself.';
  const why = set('prepare', running && 'Wait for the agents to finish' || !best && 'No design passes every check yet' || !preformReady && 'Connect PreForm first' || !$('print-preset').value && 'Choose the printer and resin');
  $('print-hint').textContent = why || '';
  const review = state.print_reviews.at(-1);
  set('queue', !review && 'Prepare & check first' || review.status !== 'approved' && 'The agents did not approve this print' || !$('print-printer').value && 'Choose a printer');
}
let cadFusion = false;
async function refreshCad() {
  try { const s = await api('/cad/status'); cadFusion = !!s.fusion?.connected;
    $('cad-status').textContent = cadFusion ? 'CAD: Fusion connected' : 'CAD: Fusion not open'; $('cad-status').classList.toggle('good', cadFusion); gates(); } catch (e) {}
}

// ---------------------------------------------------------------- actions
$('brief-form').addEventListener('submit', e => { e.preventDefault(); work(async () => {
  const f = new FormData(e.target), body = {prompt: f.get('prompt'), live: live()};
  for (const k of ['load_n', 'max_displacement_mm', 'max_mass_g', 'safety_factor']) if (f.get(k)) body[k] = +f.get(k);
  const s = await api('/designer/projects', body);
  await listProjects(s.project.id); selected = null; follow = true; $('follow').checked = true; await load(s.project.id);
  notice(live() ? 'The agents are drafting the part with live AI. Watch "What the agents did".' : 'Rule-based mode: the built-in bracket template stands in for the drafting agent. Switch Agents to Live AI to design from your description.');
}, 'Sending the brief to the agents…'); });
$('project-picker').onchange = () => { const id = $('project-picker').value; selected = null; follow = true; $('follow').checked = true; if (id) load(id); else { $('project').hidden = true; state = null; } };
const pin = () => { follow = false; $('follow').checked = false; };
$('test-picker').onchange = () => { pin(); showTest($('test-picker').value); };
$('follow').onchange = () => { follow = $('follow').checked; if (state) renderPicker(); };
$('color-by').onchange = () => viewer.setColorBy($('color-by').value === 'stress' ? 'stress' : 'bend');
$('exaggerate').onchange = () => viewer.setExaggerate($('exaggerate').checked);
$('run-more').onclick = () => work(async () => { await api(`/designer/projects/${projectId}/run`, {live: live()}); await load(projectId); notice('The agents are improving the design.'); }, 'Starting…');
$('fusion-view').onclick = () => work(async () => { await api(`/designer/projects/${projectId}/fusion`, {test_id: selected}); notice('Sent to Fusion: it opens as a new document in a few seconds.'); }, 'Sending to Fusion…');
$('preform-connect').onclick = () => work(async () => {
  const s = await api('/formlabs/status'); preformReady = s.connected;
  $('preform-status').textContent = s.connected ? `Connected to PreForm ${s.version} · ${s.devices.length} printer(s).` : s.detail;
  $('preform-pill').textContent = s.connected ? 'PreForm: connected' : 'PreForm: not connected'; $('preform-pill').classList.toggle('good', s.connected);
  $('print-printer').innerHTML = '<option value="">Choose the printer</option>' + s.devices.filter(d => d.is_connected).map(d => `<option value="${esc(d.id)}">${esc(d.product_name)} · ${esc(d.id)}</option>`).join('');
  if (s.connected) {
    const catalog = await api('/formlabs/catalog'); presets = [];
    for (const p of catalog.printer_types || []) if (/^Form(?:\s|$)/i.test(p.label)) for (const m of p.materials || []) for (const st of m.material_settings || [])
      if (typeof st.scene_settings.layer_thickness_mm === 'number') presets.push({label: `${p.label} / ${m.label} / ${st.label}`, profile: st.scene_settings});
    presets.sort((a, b) => (/Alpha Fast/i.test(b.label) - /Alpha Fast/i.test(a.label)) || a.label.localeCompare(b.label));
    $('print-preset').innerHTML = '<option value="">Select the printer and resin</option>' + presets.map((p, i) => `<option value="${i}">${esc(p.label)}</option>`).join('');
    const alpha = presets.findIndex(p => /Alpha Fast/i.test(p.label)); if (alpha >= 0) $('print-preset').value = alpha;
  }
  notice(s.connected ? 'PreForm connected.' : s.detail, !s.connected);
}, 'Connecting to PreForm…');
$('prepare').onclick = () => work(async () => {
  const preset = presets[+$('print-preset').value]; if (!preset) throw new Error('Choose the printer and resin');
  const review = await api(`/designer/projects/${projectId}/print`, {test_id: state.best.id, live: live(), profile: {...preset.profile, minimum_wall_mm: +$('minimum-wall').value}});
  await load(projectId); notice(review.status === 'approved' ? 'Approved. Add your notes and send it to the printer queue.' : 'Not approved; see the reasons below.', review.status !== 'approved');
}, 'PreForm is preparing the part and the agents are reviewing it. This can take a few minutes…');
$('queue-form').addEventListener('submit', e => { e.preventDefault(); work(async () => {
  const r = await api(`/designer/projects/${projectId}/queue`, {review_id: state.print_reviews.at(-1).id, printer: $('print-printer').value, review_notes: $('review-notes').value});
  notice(`Job ${r.job_id} is in the printer queue. Start it at the printer, then test the printed part below.`);
}, 'Sending to the printer queue…'); });
$('measure-form').addEventListener('submit', e => { e.preventDefault(); work(async () => {
  const f = new FormData(e.target);
  await api(`/designer/projects/${projectId}/measurement`, {test_id: state.best.id, measured_mm: +f.get('measured_mm'), uncertainty_mm: +f.get('uncertainty_mm'), notes: f.get('notes') || ''});
  await load(projectId); notice('Calibrated from your measurement. If it no longer passes, let the agents keep improving it.');
}, 'Recalibrating with finite elements…'); });
['print-preset', 'print-printer'].forEach(id => $(id).addEventListener('change', gates));
$('agent-mode').onchange = () => { if (live() && !config.live_ready) notice('Live AI is not configured in .env; rule-based mode still works.', true); };

(async () => { try {
  viewer = window.Viewer3D($('viewer'), $('viewer-legend'));
  config = await api('/config'); refreshCad(); setInterval(refreshCad, 8000);
  if (config.live_ready) $('agent-mode').value = 'live';
  await listProjects();
  const latest = (await api('/designer/projects')).at(-1);
  if (latest) { $('project-picker').value = latest.id; await load(latest.id); }
} catch (e) { notice(e.message, true); } })();
