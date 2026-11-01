'use strict';
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v, d = 2) => Number(v).toLocaleString(undefined, {maximumFractionDigits: d});
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
function clearResults(message = 'Waiting for the first test…') {
  selected = null;
  viewer.clear(message);
  $('test-picker').innerHTML = '';
  $('checks-title').textContent = 'Checks'; $('checks').innerHTML = ''; $('test-meta').textContent = '';
  for (const id of ['step-link', 'stl-link']) { $(id).removeAttribute('href'); $(id).classList.add('disabled'); }
  for (const id of ['chart-mass', 'chart-bend', 'chart-stress']) $(id).innerHTML = '<p class="muted">Appears after the first test.</p>';
  for (const id of ['rounds', 'params', 'assumptions', 'spec-reviews', 'print-review', 'measurements', 'draft-attempts', 'design-intent']) $(id).innerHTML = '';
  $('spec-meta').textContent = ''; $('part-best').textContent = 'No passing design yet';
  $('real-test-plan').textContent = 'After printing: hold it as shown, apply the load and measure how far it bends at the load. The agents then recalibrate the stiffness and can adjust the design again.';
}
function render() {
  $('project').hidden = false;
  const spec = state.specs.at(-1)?.spec, p = state.progress, best = state.best;
  $('part-name').textContent = spec ? spec.name : 'Drafting…';
  const last = p.log.at(-1)?.message || '';
  $('part-status').textContent = p.running ? `Working (${p.stage || ''}): ${last}` : (p.stage === 'ready' ? 'Done: ready to view and print' : p.stage === 'error' ? last : last || 'Idle');
  if (!p.running && (state.specs.at(-1)?.reviews || []).some(r => r.issues.some(i => i.severity === 'must_fix'))) $('part-status').textContent = 'Design review needs repair before printing';
  $('part-best').textContent = best ? `${num(best.results.mass_g, 1)} g · bends ${num(bend(best.results), 3)} mm · stress ${num(best.results.peak_stress_mpa, 1)} MPa` : 'No passing design yet';
  $('log').innerHTML = p.log.slice(-12).map(e => `<p><time>${esc(new Date(e.t).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}))}</time> ${esc(e.message)}</p>`).join('') || '<p class="muted">Starting…</p>';
  if (!state.tests.length) {
    // A new part is still drafting: show nothing from the previous part.
    clearResults(p.running ? 'The agents are drafting the part…' : 'No design was built; see the log.');
    renderSpec(spec); renderAttempts(); renderRounds(); gates(); return;
  }
  renderPicker(); renderSpec(spec); renderAttempts(); renderRounds(); renderCharts(); renderPrint(); renderMeasurements(); gates();
}
function renderAttempts() {
  const labels = {received: 'received; checking', rejected: 'rejected', accepted: 'accepted', review_rejected: 'repair requested', review_blocked: 'review needs attention', provider_error: 'model call failed', error: 'stopped'};
  $('draft-attempts').innerHTML = (state.attempts || []).slice().reverse().map(a => `<div class="tl-prop ${a.status === 'accepted' ? 'chosen' : 'invalid'}"><b>Attempt ${a.attempt}: ${esc(labels[a.status] || a.status)}</b>
    ${(a.errors || []).map(e => `<small class="bad">${esc(e)}</small>`).join('')}
    ${(a.reviews || []).map(r => `<small><b>${esc(r.agent)}:</b> ${esc(r.verdict)}${r.note ? ' · ' + esc(r.note) : ''}${r.issues.map(i => ' · ' + esc(i.problem) + ' (' + esc(i.verification || 'agent concern') + ')').join('')}</small>`).join('')}
    <small>${a.usage?.calls || 0} model calls · geometry and evidence saved</small></div>`).join('') || '<p class="muted">No saved drafting attempts. Earlier runs may predate attempt history.</p>';
  const intent = state.specs.at(-1)?.design_intent || state.attempts?.at(-1)?.design_intent || {};
  const labelsIntent = {function: 'Function', interfaces: 'Interfaces', keep_clear: 'Keep-clear space', load_path: 'Load path', assumptions: 'Provisional assumptions'};
  $('design-intent').innerHTML = Object.entries(intent).map(([k, v]) => `<p><b>${esc(labelsIntent[k] || k)}:</b> ${esc(Array.isArray(v) ? v.join('; ') : v)}</p>`).join('');
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
  $('step-link').classList.remove('disabled'); $('stl-link').classList.remove('disabled');
  try {
    if (!fields[id]) {
      const response = await fetch(`/api/designer/projects/${projectId}/tests/${id}/field`);
      if (!response.ok) throw new Error('The saved mesh is missing or unavailable; re-run the test.');
      fields[id] = await response.json();
    }
    const spec = state.specs.find(s => s.id === t.spec_id)?.spec;
    if (selected === id) viewer.setField(fields[id], r.regions || {supports: [], loads: []}, {stress: r.allowable_stress_mpa, bend: spec?.requirements.max_displacement_mm});
  } catch (e) { if (selected === id) { delete fields[id]; selected = null; viewer.clear(e.message); notice('Could not load the 3D result: ' + e.message, true); } }
}
function renderSpec(spec) {
  const rec = state.specs.at(-1);
  if (!spec) { $('spec-summary').textContent = 'The drafting agent is reading the request…'; return; }
  $('spec-summary').textContent = spec.summary;
  $('assumptions').innerHTML = (rec.assumptions || []).map(a => `<li>${esc(a)}</li>`).join('');
  const values = (state.best || state.tests.filter(t => t.chosen).at(-1))?.values || {};
  $('params').innerHTML = Object.entries(spec.parameters).map(([n, p]) => `<tr><td>${esc(n.replaceAll('_', ' '))}</td><td>${num(values[n] ?? p.value, 2)} mm</td><td>${num(p.min, 1)}–${num(p.max, 1)}</td><td>${esc(p.role)}</td></tr>`).join('');
  const sev = {must_fix: 'must fix', should_fix: 'should fix', note: 'note'};
  $('spec-reviews').innerHTML = (rec.reviews || []).map(r => `<div class="tl-prop ${r.verdict === 'approve' ? '' : 'invalid'}"><div><b>${esc(r.agent)}</b> <span class="vote ${r.verdict === 'approve' ? 'yes' : 'no'}">${r.verdict === 'approve' ? '✓ approves' : r.verdict === 'revise' ? '✗ asks for changes' : 'no reply'}</span></div>
    ${r.note ? `<small>${esc(r.note)}</small>` : ''}${r.issues.map(i => `<small class="${i.severity === 'must_fix' ? 'bad' : ''}"><b>${esc(sev[i.severity])}${i.feature ? ' · ' + esc(i.feature) : ''}:</b> ${esc(i.problem)}${i.fix ? ' → ' + esc(i.fix) : ''}</small>`).join('')}</div>`).join('') || '<p class="muted">No review recorded for this part.</p>';
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
    const status = {retrying: 'no improvement; using failures for the next correction', stalled: `stopped after ${r.stalled_rounds ?? 2} rounds without improvement`, converged: 'no improvement (earlier run)'};
    return `<div class="tl-item"><span class="tl-icon">${r.iteration}</span><div class="tl-body"><div class="tl-head"><b>Round ${r.iteration}: ${r.status === 'improved' ? 'adopted ' + esc(r.chosen_agent) + "'s change" : esc(status[r.status] || r.status)}</b><small class="muted">${r.mode === 'llm' ? 'live AI' : 'rule-based'}${r.usage?.calls ? ' · ' + r.usage.calls + ' AI calls' : ''}</small></div><div class="tl-props">${props || '<small class="muted">No proposals.</small>'}</div>${notes}</div></div>`;
  }).join('');
  const plan = [...state.rounds].reverse().find(r => r.notes?.measurement && !/^No usable/.test(r.notes.measurement))?.notes.measurement;
  if (plan) $('real-test-plan').textContent = 'Measurement agent\'s test plan: ' + plan;
}
$('rounds').addEventListener('click', e => { const id = e.target.dataset?.test; if (id) { pin(); $('test-picker').value = id; showTest(id); $('viewer').scrollIntoView({behavior: 'smooth', block: 'center'}); } });

// ---------------------------------------------------------------- charts
function lineChart(el, tests, value, {limit, unit}) {
  el.innerHTML = window.DesignerCharts.chartSvg(tests, {width: el.clientWidth || 400, value, limit, unit});
}
function renderCharts() {
  const spec = state.specs.at(-1)?.spec; if (!spec || !state.tests.length) return;
  const t = state.tests, r0 = t[0].results;
  lineChart($('chart-mass'), t, x => x.results.mass_g, {limit: spec.requirements.max_mass_g, unit: 'g'});
  lineChart($('chart-bend'), t, x => bend(x.results), {limit: spec.requirements.max_displacement_mm, unit: 'mm'});
  lineChart($('chart-stress'), t, x => x.results.peak_stress_mpa, {limit: r0.allowable_stress_mpa, unit: 'MPa'});
}
document.querySelector('.charts-3').addEventListener('click', e => { const id = e.target.closest('[data-test]')?.dataset.test; if (id) { pin(); $('test-picker').value = id; showTest(id); } });
document.querySelector('.charts-3').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { const dot = e.target.closest('[data-test]'); if (dot) { e.preventDefault(); dot.dispatchEvent(new MouseEvent('click', {bubbles: true})); } } });

// ---------------------------------------------------------------- print & real test
// PreForm reports warnings as "check/model-id: key=value"; say them in plain words.
function plainWarning(w) {
  const m = /^(\w+)\/[^:]+:\s*(\w+)=([\d.eE+-]+)/.exec(w);
  if (!m) return w;
  const [, check, key, raw] = m, v = +raw;
  if (check === 'supportedness') return `${num(v * 100, 1)}% of the surface is not fully supported`;
  if (check === 'thin_features') return `thin walls: ${num(v, 1)} ${key.includes('volume') ? 'mm³' : ''}`;
  if (check === 'cups') return `${v} cup(s) that can trap resin`;
  if (check === 'minima') return `${v} unsupported low point(s)`;
  return `${check.replaceAll('_', ' ')}: ${key.replaceAll('_', ' ')} ${num(v, 3)}`;
}
function renderPrint() {
  const unresolved = (state.specs.at(-1)?.reviews || []).flatMap(r => r.issues.filter(i => i.severity === 'must_fix').map(i => ({...i, agent: r.agent})));
  const review = state.print_reviews.at(-1);
  $('print-review').innerHTML = review ? `<div class="decision-box ${review.status === 'approved' ? '' : 'warn'}"><b>${review.status === 'approved' ? 'Approved for the printer queue' : 'Not approved'}</b>
    <p>PreForm checks: ${esc(review.preform.grade)}${review.estimate?.total_print_time_s ? ` · ${num(review.estimate.total_print_time_s / 3600, 2)} h` : ''}${review.material_usage?.volume_ml ? ` · ${num(review.material_usage.volume_ml)} mL resin with supports` : ''}${(review.preform.warnings || []).length ? ' · ' + esc(review.preform.warnings.map(plainWarning).join('; ')) : ''}</p>
    ${review.gates.length ? `<p class="bad">Blocked: ${esc(review.gates.join('; '))}</p>` : ''}
    ${(review.unresolved_review_issues || []).length ? `<p>Open review issues: repair and review the design before printing.</p><ul>${review.unresolved_review_issues.map(i => `<li><b>${esc(i.agent)}</b>${i.feature ? ' · ' + esc(i.feature) : ''}: ${esc(i.problem)}${i.fix ? ' <span class="muted">→ ' + esc(i.fix) + '</span>' : ''}</li>`).join('')}</ul>` : ''}<ul>${review.votes.map(v => `<li><b>${esc(v.agent)}</b> ${v.support ? 'approves' : 'objects'}: ${esc(v.reason)}</li>`).join('')}</ul></div>` : '';
}
function renderMeasurements() {
  $('measurements').innerHTML = state.measurements.slice().reverse().map(m => `<div class="decision-box ${m.recheck.passed ? '' : 'warn'}"><b>Measured ${num(m.measured_mm, 3)} mm vs predicted ${num(m.predicted_mm, 3)} mm</b><p>Effective stiffness ${num(m.effective_modulus_pa / 1e9, 2)} GPa (datasheet 2.23 GPa). With it, the design ${m.recheck.passed ? 'still passes every check' : 'no longer passes: let the agents keep improving it'}.</p></div>`).join('');
}
function gates() {
  if (!state) return;
  const running = state.progress.running, best = state.best;
  const set = (id, reason) => { $(id).disabled = busy || !!reason; $(id).title = reason || ''; return reason; };
  set('run-more', running && 'The agents are working');
  const needsRepair = (state.specs.at(-1)?.reviews || []).some(r => r.issues.some(i => i.severity === 'must_fix'));
  $('run-more').textContent = needsRepair ? 'Repair design review issues' : state.specs.length ? 'Let the agents keep improving it' : 'Resume drafting from saved failures';
  const fusionReason = !selected ? 'Select a tested design first' : fusionJob ? 'Waiting for Fusion to finish the import' : !cadFusion ? 'Start the ScienceClaw add-in in Fusion' : !fusionCanView ? `Fusion is running add-in ${fusionVersion}. In Fusion: Utilities > Scripts and Add-Ins > ScienceClaw > Stop, then Run to load the update.` : '';
  set('fusion-view', fusionReason);
  $('fusion-hint').textContent = fusionReason;
  const why = set('prepare', running && 'Wait for the agents to finish' || needsRepair && 'Use Repair design review issues before preparing this part' || !best && 'No design passes every check yet' || !preformReady && 'Connect PreForm first' || !$('print-preset').value && 'Choose the printer and resin');
  $('print-hint').textContent = why || '';
  const review = state.print_reviews.at(-1);
  const reviewedTest = state.tests.find(t => t.id === review?.test_id);
  set('queue', running && 'Wait for the agents to finish' || needsRepair && 'Repair the design review first' || !review && 'Prepare & check first' || review.status !== 'approved' && 'The agents did not approve this print' || reviewedTest?.spec_id !== state.specs.at(-1)?.id && 'Prepare the latest design first' || !$('print-printer').value && 'Choose a printer');
}
let cadFusion = false, fusionCanView = false, fusionVersion = '', fusionJob = null;
async function refreshCad() {
  try { const s = await api('/fusion/status'); cadFusion = !!s.connected;
    fusionCanView = !!s.supports_view_step; fusionVersion = s.addin?.addin_version || 'unknown';
    $('cad-status').textContent = cadFusion ? (fusionCanView ? 'CAD: Fusion connected' : 'CAD: reload Fusion add-in') : 'CAD: Fusion not connected'; $('cad-status').classList.toggle('good', cadFusion && fusionCanView); gates();
  } catch (e) { cadFusion = false; fusionCanView = false; gates(); }
}

// ---------------------------------------------------------------- actions
$('brief-form').addEventListener('submit', e => { e.preventDefault(); work(async () => {
  const f = new FormData(e.target), body = {prompt: f.get('prompt'), live: live()};
  for (const k of ['load_n', 'max_displacement_mm', 'max_mass_g', 'safety_factor']) if (f.get(k)) body[k] = +f.get(k);
  const s = await api('/designer/projects', body);
  await listProjects(s.project.id); follow = true; $('follow').checked = true; state = null; clearResults('The agents are drafting the part…'); await load(s.project.id);
  notice(live() ? 'The agents are drafting the part with live AI. Watch "What the agents did".' : 'Rule-based mode: the built-in bracket template stands in for the drafting agent. Switch Agents to Live AI to design from your description.');
}, 'Sending the brief to the agents…'); });
$('project-picker').onchange = () => { const id = $('project-picker').value; follow = true; $('follow').checked = true; clearResults('Loading…'); projectId = id || null; if (id) load(id); else { $('project').hidden = true; state = null; } };
const pin = () => { follow = false; $('follow').checked = false; };
$('test-picker').onchange = () => { pin(); showTest($('test-picker').value); };
$('follow').onchange = () => { follow = $('follow').checked; if (state) renderPicker(); };
$('zoom-in').onclick = () => viewer.zoomIn();
$('zoom-out').onclick = () => viewer.zoomOut();
$('zoom-reset').onclick = () => viewer.reset();
let resizeTimer; window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state?.tests.length) renderCharts(); }, 150); });
$('color-by').onchange = () => viewer.setColorBy($('color-by').value === 'stress' ? 'stress' : 'bend');
$('exaggerate').onchange = () => viewer.setExaggerate($('exaggerate').checked);
$('run-more').onclick = () => work(async () => { await api(`/designer/projects/${projectId}/run`, {live: live()}); await load(projectId); notice('The agents are reviewing saved evidence and continuing the design.'); }, 'Starting…');
$('fusion-view').onclick = () => work(async () => {
  const job = await api(`/designer/projects/${projectId}/fusion`, {test_id: selected});
  fusionJob = job.id;
  notice('Import queued. Waiting for Fusion to confirm it opened this design…');
  trackFusionJob(job.id);
}, 'Sending to Fusion…');
async function trackFusionJob(id, started = Date.now()) {
  try {
    const job = await api(`/fusion/jobs/${id}`);
    if (job.status === 'done') { fusionJob = null; notice(`Opened in Fusion: ${job.result.document}`); gates(); return; }
    if (job.status === 'failed') throw new Error(job.result.error || 'Fusion import failed');
    if (Date.now() - started > 120000) throw new Error('Fusion has not confirmed the import. Close active dialogs in Fusion and check the ScienceClaw add-in. You can also download the STEP.');
    setTimeout(() => trackFusionJob(id, started), 1500);
  } catch (e) { fusionJob = null; notice(e.message, true); gates(); }
}
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
