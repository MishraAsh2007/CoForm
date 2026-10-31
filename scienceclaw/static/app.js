'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const readable = value => String(value ?? '').replaceAll('_',' ');
const number = (value, digits = 2) => Number(value).toLocaleString(undefined,{maximumFractionDigits:digits});
const mm = v => number(v*1000,3)+' mm';
const GEOMETRY_ISSUES=['predicted_over_limit','measured_over_limit','stress_over_limit','over_material_budget'];
const STAGES=['design','print','test','decide','finish'];
let config, state, candidate, diagnosis=null, diagnosisFixable=false, busy=false, cadState=null, currentStage=null;
let manufacturing={assets:[],projects:[],submissions:[],receipts:[]}, preformReady=false, printPresets=[];

// Plain words for the record values the backend uses.
const KIND={initial:'Baseline',search:'Agents\' test',reserved:'Prediction check',acceptance:'Final copy'};
const FIT={identifiable:'calibrated from measurements',insufficient:'not enough data yet',inadequate:'measurements disagree with it',initial_assumption:'textbook values (not yet calibrated)'};
const RESULT=s=>({simulated_pass:'pass (simulated)',physical_pass:'pass',fail:'fail'})[s]||readable(s);
const ROLE={mechanics:{icon:'∑',title:'Mechanics',job:'Structural engineer',duty:'Proposes tests or designs · must approve every design'},
  fabrication:{icon:'⬡',title:'Fabrication',job:'Print engineer',duty:'Proposes designs only · must approve every design'},
  measurement:{icon:'◎',title:'Measurement',job:'Test engineer',duty:'Proposes tests only · must approve every test'},
  single:{icon:'●',title:'Single agent',job:'All three roles alone',duty:'Baseline: decides without peers'}};

async function api(path, data) {
  const response = await fetch('/api'+path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function notice(message, error=false) { $('notice').hidden=false; $('notice').textContent=message; $('notice').classList.toggle('error',error); }
async function work(action, message) {
  if (busy) return;
  busy=true; document.querySelectorAll('button').forEach(b=>b.disabled=true);
  notice(message || 'Working…');
  try { await action(); } catch(error) { notice(error.message,true); }
  finally { busy=false; document.querySelectorAll('button').forEach(b=>b.disabled=false); updateButtons(); }
}
const live = () => $('agent-mode').value==='live';
const sim = () => state?.run.mode==='simulated';
const designName = c => !c ? 'unknown design' : c.reference ? 'Starting design' : 'Design '+state.candidates.indexOf(c);
const dims = c => `${number(c.geometry.base_width_m*1000)} × ${number(c.geometry.wall_height_m*1000)} mm`;
const originOf = c => c.reference ? 'start' : c.proposed_by==='human:fusion' ? 'you' : 'agents';
const originLabel = c => c.reference ? 'starting design' : c.proposed_by==='human:fusion' ? 'your Fusion edit' : c.proposed_by.startsWith('repair') ? 'agent repair' : 'proposed by the agents';
const actionText = p => p.action==='design' ? `new design ${number(p.geometry?.base_width_m*1000)} × ${number(p.geometry?.wall_height_m*1000)} mm` : `test at ${number(p.load_position_m*1000)} mm from the clamp`;

// ------------------------------------------------------------------ stage logic
function stageNow() {
  if (state.nominations.length) return 'finish';
  const pending=state.pending_experiment, reference=state.candidates[0];
  if (sim()) return pending ? 'test' : state.budget.cycles_remaining<=0 ? 'finish' : 'decide';
  const refCad=(state.cad_revisions||[]).some(r=>r.candidate_id===reference.id && r.check.status==='pass');
  if (!refCad) return 'design';
  if (pending) {
    const registered=state.fabrications.some(f=>f.specimen_id===pending.specimen_id);
    if (registered) return 'test';
    const approved=(state.print_reviews||[]).find(r=>r.candidate_id===pending.candidate_id && r.status==='approved');
    const queued=approved && manufacturing.receipts.some(r=>r.project_id===approved.project_id && r.status==='queued');
    const printedBefore=state.fabrications.some(f=>f.candidate_id===pending.candidate_id);
    return queued || printedBefore ? 'test' : 'print';
  }
  return state.budget.cycles_remaining<=0 ? 'finish' : 'decide';
}
function renderStages() {
  const now=stageNow(), order=STAGES.indexOf(now);
  document.querySelectorAll('.stage').forEach(li=>{
    const i=STAGES.indexOf(li.dataset.stage), s=li.dataset.stage;
    const status=s===now?'now':s==='finish'?'later':(now==='finish'||i<order)?'done':'later';
    li.classList.toggle('now',status==='now'); li.classList.toggle('done',status==='done');
    li.querySelector('.state').textContent={now:'YOUR TURN',done:'done',later:'next'}[status];
    if (sim() && s==='print') li.querySelector('.state').textContent='skipped';
  });
  if (now!==currentStage) { openStage(now, true); currentStage=now; }
}
function openStage(name, only=false) {
  document.querySelectorAll('.stage').forEach(li=>{
    const open=li.dataset.stage===name ? true : only ? false : li.classList.contains('open');
    li.classList.toggle('open',open); li.querySelector('.stage-head').setAttribute('aria-expanded',open);
  });
}
document.querySelectorAll('.stage-head').forEach(h=>h.addEventListener('click',()=>{
  const li=h.closest('.stage'); li.classList.toggle('open'); h.setAttribute('aria-expanded',li.classList.contains('open'));
  if (li.dataset.stage==='design' && li.classList.contains('open')) renderCharts();
}));

// ------------------------------------------------------------------ study
async function listRuns(selected) {
  const runs = await api('/runs');
  $('run-picker').innerHTML = runs.length ? runs.slice().reverse().map(r=>`<option value="${esc(r.id)}">${esc(r.name)} · ${r.mode==='simulated'?'simulation':'real prints'}</option>`).join('') : '<option value="">No studies yet</option>';
  if (selected) $('run-picker').value=selected;
  return runs;
}
async function loadRun(id) { state=await api('/runs/'+id); currentStage=null; render(); }
function render() {
  $('workspace').hidden=false; $('empty').hidden=true;
  const run=state.run, p=run.protocol;
  const scenario=run.simulation_scenario;
  $('source-badge').textContent=sim()?(scenario?.physics==='fea'?'SIMULATION · virtual test rig':'SIMULATION · beam formula'):'REAL PRINTS';
  $('source-badge').className='pill '+(sim()?'sim':'good');
  $('export').href=`/api/runs/${run.id}/export`;
  const current=$('candidate-picker').value;
  $('candidate-picker').innerHTML=state.candidates.map(c=>`<option value="${esc(c.id)}">${esc(designName(c))} · ${esc(dims(c))}${state.nominations.some(n=>n.candidate_id===c.id)?' · final':''}</option>`).join('');
  const focus=state.pending_experiment?.candidate_id;
  const selected=state.candidates.some(c=>c.id===current)?current:(state.nominations[0]?.candidate_id || focus || state.candidates.at(-1).id);
  $('candidate-picker').value=selected;
  renderGoal(); renderStages(); renderTest(); renderDecide(); renderAgents(); renderTimeline(); selectCandidate(selected);
  renderPrintProjects(); renderHistory(); updateButtons(); renderCharts();
}
function renderGoal() {
  const run=state.run, p=run.protocol, ref=state.candidates[0];
  $('goal-text').textContent=`Bend no more than ${mm(p.max_deflection_m)} under ${number(p.proof_force_n,3)} N applied ${number(p.proof_position_m*1000)} mm from the clamp, using as little resin as possible`+(p.max_volume_m3?` (at most ${number(p.max_volume_m3*1e6,2)} cm³)`:'')+'.';
  $('budget-text').textContent=`${state.budget.cycles_used} of ${p.search_cycles} tests · ${state.budget.designs_used} of ${p.max_designs} new designs`;
  const passed=new Set(state.observations.filter(o=>['physical_pass','simulated_pass'].includes(o.assessment.status)).map(o=>o.candidate_id));
  const best=state.candidates.filter(c=>passed.has(c.id)).sort((a,b)=>a.volume_m3-b.volume_m3)[0];
  $('best-text').textContent=best?`${designName(best)} (${dims(best)}) passed using ${number(best.volume_m3/ref.volume_m3*100,0)}% of the starting resin`:'No design has passed a measured test yet';
}
function renderHistory() {
  $('integrity').textContent=state.log_verified?'Append-only record, hash chain verified.':'Record verification failed.';
  $('history').innerHTML=state.events.slice().reverse().slice(0,200).map(e=>`<div class="event"><time>${esc(new Date(e.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}))}</time><details><summary><b>${esc(readable(e.type))}</b></summary><pre>${esc(JSON.stringify(e.payload,null,2))}</pre></details></div>`).join('');
}

// ------------------------------------------------------------------ charts
function predictMm(c, params, force, position) {
  const I=c.section.second_moment_m4, E=params.youngs_modulus_pa, C=params.rotational_compliance_rad_per_nm;
  return (force*position**3/(3*E*I) + force*C*position**2)*1000;
}
async function renderCharts() {
  if (!state || !window.Charts) return;
  const p=state.run.protocol, ref=state.candidates[0], F=p.proof_force_n;
  const has=state.observations.length>0;
  $('progress-empty').hidden=has; $('progress-charts').hidden=!has;
  if (has) {
    const free=ref.geometry.length_m-ref.geometry.clamp_length_m;
    const curve=(params,name,color)=>{const pts=[];for(let a=0;a<=free+1e-9;a+=free/60) pts.push([a*1000,predictMm(ref,params,F,a)]);return {name,color,points:pts,at:x=>predictMm(ref,params,F,x/1000)};};
    const curves=[curve(state.models[0].parameters,'Textbook assumption',Charts.VIZ.muted)];
    if (state.last_adopted_model) curves.push(curve(state.last_adopted_model.parameters,'Calibrated model',Charts.VIZ.blue));
    const points=state.observations.filter(o=>o.candidate_id===ref.id).map(o=>({x:o.load_position_m*1000,y:o.displacement_m*1000*F/o.force_n,err:o.uncertainty_m*1000*F/o.force_n,force:o.force_n,
      label:`${KIND[o.purpose]||readable(o.purpose)} · ${RESULT(o.assessment.status)}`}));
    Charts.modelChart($('chart-model'),{freeLength:free,proofPosition:p.proof_position_m,limit:p.max_deflection_m*1000,curves,points});
    const params=state.model.parameters, rows=[];
    for (const c of state.candidates) {
      const proof=state.observations.filter(o=>o.candidate_id===c.id && Math.abs(o.load_position_m-p.proof_position_m)<1e-9 && Math.abs(o.force_n-F)<1e-9);
      if (!proof.length) continue;
      rows.push({name:designName(c),origin:originLabel(c),predicted:predictMm(c,params,F,p.proof_position_m),
        measured:proof.map(o=>({y:o.displacement_m*1000,err:o.uncertainty_m*1000,status:RESULT(o.assessment.status),label:KIND[o.purpose]||readable(o.purpose)}))});
    }
    Charts.designsChart($('chart-designs'),rows,p.max_deflection_m*1000);
    const fits=state.models.filter(m=>m.adopted && m.fit.status==='identifiable');
    Charts.paramChart($('chart-e'),fits.map((m,i)=>({y:m.parameters.youngs_modulus_pa/1e9,sd:(m.fit.uncertainty?.youngs_modulus_pa_std||0)/1e9,label:'fit '+(i+1),title:`Fit ${i+1} · from ${m.fit.source_observation_ids.length} load tests`})),{name:'Resin stiffness',unit:'GPa',color:Charts.VIZ.blue});
    Charts.paramChart($('chart-c'),fits.map((m,i)=>({y:m.parameters.rotational_compliance_rad_per_nm,sd:m.fit.uncertainty?.rotational_compliance_rad_per_nm_std||0,label:'fit '+(i+1),title:`Fit ${i+1}`})),{name:'Clamp looseness',unit:'rad/N·m',color:Charts.VIZ.aqua});
  }
  if (document.querySelector('.stage[data-stage=design]').classList.contains('open') && $('space-details').open) {
    try {
      const space=await api(`/runs/${state.run.id}/design-space`);
      const tested=new Set(state.observations.map(o=>o.candidate_id));
      const markers=state.candidates.map(c=>({id:c.id,w:c.geometry.base_width_m*1000,h:c.geometry.wall_height_m*1000,origin:originOf(c),originLabel:originLabel(c),name:designName(c),tested:tested.has(c.id),selected:c.id===candidate?.id}));
      Charts.designSpace($('chart-space'),space,markers,id=>{ $('candidate-picker').value=id; selectCandidate(id); });
    } catch(error) { $('chart-space').textContent=error.message; }
  }
}
$('space-details').addEventListener('toggle',renderCharts);

// ------------------------------------------------------------------ agents panel
function renderAgents() {
  const run=state.run;
  if (run.method==='adaptive') { $('agents').innerHTML='<p class="muted">This study uses the numerical optimizer baseline, so no AI agents take part.</p>'; $('last-decision').innerHTML=''; return; }
  const roles=run.method==='single_agent'?['single']:['mechanics','fabrication','measurement'];
  const decision=[...state.decisions].reverse().find(d=>(d.proposals||[]).length || d.transcript?.length);
  $('agents').innerHTML=roles.map(role=>{
    const r=ROLE[role], c=config.charters?.[role];
    const member=run.team?.[role] || (role==='single'?run.team?.mechanics:null);
    const provider=member?.provider || (role==='single'?(config.single_agent_provider||config.roles.mechanics):config.roles[role]);
    const model=(member?.model || config.providers[provider]?.model || '').split('/').at(-1);
    let said='<p class="said"><em>Nothing yet.</em></p>';
    if (decision) {
      const mine=(decision.proposals||[]).filter(p=>p.agent===role).at(-1);
      const abstain=(decision.transcript||[]).filter(t=>t.type==='evidence_request' && t.agent===role).at(-1);
      const vote=decision.selected && (decision.reviews||[]).find(v=>v.agent===role && v.proposal_id===decision.selected.id);
      said=mine?`<p class="said">Proposed a ${esc(actionText(mine))}${mine.valid?'':' (rejected by the checks)'}: <em>${esc((mine.reason||'').slice(0,160))}</em></p>`
        :abstain?`<p class="said">Sat out: <em>${esc(abstain.reason.slice(0,160))}</em></p>`:'<p class="said"><em>No proposal this round.</em></p>';
      if (vote) said+=`<p class="said">On the chosen plan: <span class="vote ${vote.support?'yes':'no'}">${vote.support?'✓ supported':'✗ objected'}</span>${vote.findings?.length?` <em>${esc(vote.findings.slice(0,2).join(' · '))}</em>`:''}</p>`;
    }
    const notebook=decision?.agent_states?.[role]?.notebook;
    return `<div class="agent" data-role="${esc(role)}"><div class="agent-top"><span class="avatar">${r.icon}</span><div><b>${esc(r.title)}</b> <small>${esc(r.job)} · ${esc(r.duty)}</small></div></div>${said}
      <details><summary>What it sees${notebook?' · private notes':''}</summary>${c?`<p><b>Sees:</b> ${esc(c.evidence)}</p><p><b>Owns:</b> ${esc(c.owns)}</p>`:''}<p class="muted">${esc(provider||'')} · ${esc(model)}${live()?'':' · rule-based mode now'}</p>${notebook?`<p><b>Private notes:</b> ${esc(notebook)}</p>`:''}</details></div>`;
  }).join('');
  const d=state.decisions.at(-1);
  $('last-decision').innerHTML=d?`<div class="decision-box ${d.selected?'':'warn'}"><b>${d.selected?'Agreed: '+esc(actionText(d.selected)):'No agreement: '+esc(readable(d.status))}</b>${d.selected?`<p>Supported by ${esc((d.selected.supporters||[]).join(', '))}${d.rounds>1?' after '+d.rounds+' rounds':''}.</p>`:''}${d.usage?.calls?`<p class="muted">${d.usage.calls} AI calls · ${number(d.usage.total_tokens,0)} tokens</p>`:''}<button class="text-button" id="open-notebook">Full discussion →</button></div>`:'';
  $('open-notebook')?.addEventListener('click',()=>{ $('history-card').open=true; $('history-card').scrollIntoView({behavior:'smooth'}); });
}

// ------------------------------------------------------------------ lab notebook
function timelineItems() {
  const byId={}; for (const kind of ['decisions','experiments','observations','models','candidates','repairs','print_reviews','cad_revisions','fabrications']) for (const r of state[kind]||[]) byId[r.id]={kind,r};
  const items=[];
  for (const e of state.events) {
    const hit=byId[e.payload?.record_id]; if(!hit) continue;
    const {kind,r}=hit, t=e.timestamp;
    if (kind==='decisions') {
      const votes=(r.reviews||[]); const who=r.policy==='adaptive_numerical'?'Numerical optimizer':r.mode==='llm'?'Agents (live AI)':'Agents (rule-based)';
      const props=(r.proposals||[]).map(pr=>({agent:pr.agent,text:actionText(pr),reason:pr.reason,valid:pr.valid,errors:pr.validation_errors,
        votes:votes.filter(v=>v.proposal_id===pr.id).map(v=>({agent:v.agent,support:v.support,owner:v.owner,reason:[v.reason,...(v.findings||[])].join(' · ')})),chosen:r.selected?.id===pr.id,
        blocked:(r.transcript||[]).filter(x=>x.type==='owner_objection'&&x.proposal_id===pr.id).map(x=>x.reason)}));
      items.push({t,icon:'◆',title:`${who}: ${r.selected?'agreed on a '+actionText(r.selected):readable(r.status)}`,body:r.selected?.reason||'',props,rounds:r.rounds,usage:r.usage});
    } else if (kind==='experiments') {
      const c=state.candidates.find(c=>c.id===r.candidate_id);
      items.push({t,icon:'▶',title:`${KIND[r.purpose]||readable(r.purpose)} test planned on ${designName(c).toLowerCase()}`,body:`${number(r.force_n,3)} N at ${number(r.load_position_m*1000)} mm · model predicts ${mm(r.prediction.displacement_m)}`});
    } else if (kind==='observations') {
      const exp=state.experiments.find(x=>x.id===r.experiment_id), predicted=exp?.prediction.displacement_m, ratio=predicted?r.displacement_m/predicted:null;
      items.push({t,icon:'●',title:`Measured ${mm(r.displacement_m)} ± ${mm(2*r.uncertainty_m)}`,body:`${predicted?`The model predicted ${mm(predicted)}${ratio&&Math.abs(ratio-1)>.15?` (it bent ${number(ratio,2)}× the prediction)`:''}. `:''}${RESULT(r.assessment.status)}`,status:r.assessment.status});
    } else if (kind==='models' && r.adopted) {
      items.push({t,icon:'∑',title:'Model recalibrated from the measurements',body:`Resin stiffness ${number(r.parameters.youngs_modulus_pa/1e9,3)} GPa · clamp looseness ${number(r.parameters.rotational_compliance_rad_per_nm,3)} rad/N·m · from ${r.fit.source_observation_ids.length} tests`});
    } else if (kind==='candidates' && !r.reference) {
      items.push({t,icon:'⬡',title:`New design: ${dims(r)}`,body:`${originLabel(r)} · ${number(r.volume_m3*1e6,2)} cm³ of resin`});
    } else if (kind==='repairs') {
      const props=(r.proposals||[]).map(pr=>({agent:pr.agent,text:`${number(pr.base_width_m*1000)} × ${number(pr.wall_height_m*1000)} mm → ${pr.predicted_m!==undefined?mm(pr.predicted_m):'?'}`,reason:pr.reason,valid:pr.valid,errors:pr.validation_errors,
        votes:(r.reviews||[]).filter(v=>v.proposal_id===pr.id).map(v=>({agent:v.agent,support:v.support,reason:v.reason})),chosen:r.selected?.id===pr.id}));
      items.push({t,icon:'✚',title:`Agents' fix: ${readable(r.status)}`,body:r.selected?.reason||(r.transcript||[]).filter(x=>x.type==='evidence_request').map(x=>x.reason).join(' ')||'',props});
    } else if (kind==='print_reviews') {
      items.push({t,icon:'⎙',title:`Print review: ${readable(r.status)}`,body:(r.votes||[]).map(v=>`${v.agent} ${v.support?'approves':'objects'}`).join(' · ')+(r.gate_errors?.length?' · blocked: '+r.gate_errors.join('; '):'')});
    } else if (kind==='cad_revisions') {
      items.push({t,icon:'◧',title:`CAD built in ${({fusion:'Fusion',freecad:'FreeCAD',local_fixture:'ScienceClaw mesh',manual_export:'uploaded export'})[r.source]||r.source}`,body:r.check.status==='pass'?'Matches the analysed part.':'Differs from the analysed part.'});
    } else if (kind==='fabrications' && r.source==='physical') {
      items.push({t,icon:'▣',title:'Printed part measured',body:r.notes});
    }
  }
  return items.reverse();
}
function timelineHtml(items) {
  if (!items.length) return '<p class="muted">Nothing yet.</p>';
  return items.map(i=>`<div class="tl-item ${i.status&&/pass/.test(i.status)?'pass':i.status==='fail'?'fail':''}"><span class="tl-icon">${esc(i.icon)}</span><div class="tl-body"><div class="tl-head"><b>${esc(i.title)}</b><time>${esc(new Date(i.t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}))}</time></div>${i.body?`<p>${esc(i.body)}</p>`:''}${i.props?.length?`<div class="tl-props">${i.props.map(pr=>`<div class="tl-prop ${pr.chosen?'chosen':''} ${pr.valid?'':'invalid'}"><div><b>${esc(pr.agent)}</b> proposed a ${esc(pr.text)}${pr.chosen?' <span class="pill good">chosen</span>':''}${pr.valid?'':' <span class="pill">rejected by the checks</span>'}</div><small>${esc(pr.reason||'')}</small>${pr.errors?.length?`<small class="bad">${esc(pr.errors.join('; '))}</small>`:''}${pr.blocked?.length?`<small class="bad">Blocked: ${esc(pr.blocked.join('; '))}</small>`:''}<div class="votes">${pr.votes.map(v=>`<span class="vote ${v.support?'yes':'no'}" title="${esc(v.reason)}">${v.support?'✓':'✗'} ${esc(v.agent)}${v.owner?' (owner)':''}</span>`).join('')}</div>${pr.votes.some(v=>v.reason.includes(' · '))?`<details class="findings"><summary>What each agent checked</summary>${pr.votes.map(v=>`<p><b>${esc(v.agent)}</b> ${esc(v.reason)}</p>`).join('')}</details>`:''}</div>`).join('')}</div>`:''}${i.usage?.calls?`<small class="muted">${i.usage.calls} AI calls · ${number(i.usage.total_tokens,0)} tokens${i.rounds>1?' · '+i.rounds+' discussion rounds':''}</small>`:''}</div></div>`).join('');
}
function renderTimeline() { $('timeline').innerHTML=timelineHtml(timelineItems()); }

// ------------------------------------------------------------------ buttons
function gate(id, reasons) {
  const reason=reasons.find(Boolean) || '';
  $(id).disabled=busy || !!reason;
  $(id).title=reason;
  return reason;
}
function updateButtons() {
  if (!state) return;
  const frozen=state.nominations.length>0, pending=state.pending_experiment;
  const pendingText=sim()?'A test is still running':'Finish the planned test first (step 3)';
  const round=gate('agent-round',[frozen&&'The final design is frozen', pending&&pendingText, state.budget.cycles_remaining<=0&&'The test budget is used up: go to Finish',
    live()&&!config.live_ready&&'Live AI is not configured in .env (switch Agents to rule-based)']);
  gate('demo',[!sim()&&'Only for simulations', frozen&&'The final design is frozen', pending&&pendingText]);
  gate('reserved',[frozen&&'The final design is frozen', pending&&pendingText, !state.model.adopted&&'Needs a calibrated model first', state.experiments.some(e=>e.purpose==='reserved')&&'Already checked']);
  gate('nominate',[frozen&&'Already frozen', pending&&pendingText]);
  gate('accept',[!frozen&&'Freeze a final design first', pending&&pendingText, state.acceptance.cycle_count>=state.run.protocol.final_copies&&'All copies tested']);
  $('demo').hidden=!sim();
  $('test-hint').textContent=round?'Not available: '+round:'';
  gate('agents-fix',[frozen&&'The final design is frozen', !diagnosisFixable&&'No problems found for this design']);
  const project=manufacturing.projects.find(p=>p.id===$('print-project').value);
  const preformReason=!preformReady&&'Connect PreForm first';
  const prepare=gate('pipeline-button',[preformReason, !$('print-preset').value&&'Choose the printer and resin', !$('minimum-wall').value&&'Enter the minimum wall for this resin']);
  gate('prepare-button',[preformReason, !$('print-asset').value&&'Import an STL first', !$('print-preset').value&&'Choose the printer and resin', !$('minimum-wall').value&&'Enter the minimum wall']);
  const queue=gate('queue-button',[preformReason, !project&&'Prepare & review a design first', project?.validation.grade==='FAIL'&&'PreForm checks failed for this project',
    !$('print-printer').value&&'Choose a printer (PreForm must see it connected)', project&&manufacturing.submissions.some(s=>s.project_id===project.id)&&'Already sent to the queue']);
  $('print-hint').textContent=prepare?'To prepare: '+prepare:queue&&project?'To queue: '+queue:'';
  $('preform-pill').textContent=preformReady?'PreForm: connected':'PreForm: not connected';
  $('preform-pill').classList.toggle('good',preformReady);
}

// ------------------------------------------------------------------ 1 · design
function selectCandidate(id) {
  candidate=state.candidates.find(c=>c.id===id); const g=candidate.geometry, ref=state.candidates[0];
  const parent=state.candidates.find(c=>c.id===candidate.parent_id);
  $('candidate-origin').textContent=candidate.reference?'The study\'s starting design.':`${originLabel(candidate)}${parent?', changed from '+designName(parent).toLowerCase():''}.`;
  $('geometry-info').innerHTML=`<div><span>Base width × wall height</span><strong>${dims(candidate)}</strong></div><div><span>Length</span><strong>${number(g.length_m*1000)} mm</strong></div><div><span>Wall thickness</span><strong>${number(g.thickness_m*1000,2)} mm</strong></div><div><span>Resin</span><strong>${number(candidate.volume_m3*1e6)} cm³ (${number(candidate.volume_m3/ref.volume_m3*100,0)}%)</strong></div>`;
  $('print-design').textContent=`Printing: ${designName(candidate)} (${dims(candidate)}). Choose another design under step 1.`;
  $('stl').href='/api/artifacts/'+candidate.artifacts.stl.filename;
  renderCad((state.cad_revisions||[]).filter(r=>r.candidate_id===id).at(-1));
  const repair=(state.repairs||[]).filter(r=>r.candidate_id===id || r.new_candidate_id===id).at(-1);
  if(repair) renderRepair({repair,candidate:state.candidates.find(c=>c.id===repair.new_candidate_id)}); else $('repair-report').innerHTML='';
  const review=(state.print_reviews||[]).filter(r=>r.candidate_id===id).at(-1);
  if(review){ const project=state.print_projects.find(p=>p.id===review.project_id); renderPipeline({status:review.status==='approved'?'ready_to_queue':'review_'+review.status,print_review:review,print_project:project}); if(project) $('print-project').value=project.id; }
  else $('pipeline-report').innerHTML='';
  loadDiagnosis(id);
}
function renderCad(cad) {
  const tool=cadState?.fusion?.connected?'Fusion':cadState?.freecad?.available?'FreeCAD':'CAD';
  $('cad-build').textContent=`Build in ${tool}`;
  if(!cad){$('cad-report').innerHTML='<p class="muted">Not built in CAD yet.</p>';return;}
  const run=encodeURIComponent(state.run.id), id=encodeURIComponent(cad.id);
  const name={fusion:'Autodesk Fusion',freecad:'FreeCAD',local_fixture:'ScienceClaw mesh',manual_export:'uploaded export',onshape:'Onshape'}[cad.source]||cad.source;
  const links=cad.source==='freecad'?`<button class="secondary small" data-open-cad="${esc(cad.id)}">Open in FreeCAD</button> <a href="/api/runs/${run}/cad/${id}/step">STEP</a>`
    :cad.source==='fusion'?`${cad.fusion.document?esc(cad.fusion.document)+(cad.fusion.version?' v'+esc(cad.fusion.version):''):''}${cad.fusion.step?` · <a href="/api/runs/${run}/cad/${id}/fusion-step">STEP</a>`:''}`:'';
  const failed=cad.check.checks.filter(c=>!c.passed).map(c=>readable(c.name));
  $('cad-report').innerHTML=`<div class="decision-box ${cad.check.status==='pass'?'':'warn'}"><b>Built in ${esc(name)}: ${cad.check.status==='pass'?'matches the analysed part ✓':'differs from the analysed part'}</b>${failed.length?`<p>Failed: ${esc(failed.join(', '))}</p>`:''}<p>${links}</p></div>`;
}
async function refreshCadStatus() {
  try { cadState=await api('/cad/status'); } catch(error) { return; }
  const f=cadState.fusion, fc=cadState.freecad;
  $('cad-status').textContent=f.connected?'CAD: Fusion connected':fc.available?'CAD: FreeCAD (Fusion not open)':'CAD: none available';
  $('cad-status').classList.toggle('good',!!cadState.active);
  if (candidate && !busy) $('cad-build').textContent=`Build in ${f.connected?'Fusion':fc.available?'FreeCAD':'CAD'}`;
}
async function buildCad(id) {
  const path={auto:'cad',fusion:'fusion',freecad:'freecad'}[$('cad-source').value];
  if(!path) throw new Error('Under Evidence & advanced, set "Build CAD with" to Automatic, Fusion or FreeCAD');
  const cad=await api(`/runs/${state.run.id}/candidates/${id}/${path}`,{});
  await loadRun(state.run.id); $('candidate-picker').value=id; selectCandidate(id);
  return cad;
}
async function loadDiagnosis(id){
  try{diagnosis=await api(`/runs/${state.run.id}/candidates/${id}/diagnosis`);renderDiagnosis();}
  catch(error){$('issues').textContent=error.message;}
}
function renderDiagnosis(){
  const d=diagnosis,p=d.physics,over=p.utilization>1,share=p.bending_fraction ?? 1;
  const show=(unit,v)=>unit==='length'?mm(v):unit==='volume'?number(v*1e6,2)+' cm³':unit==='stress'?number(v/1e6,3)+' MPa':number(v*100,1)+'%';
  $('requirements').innerHTML=d.requirements.map(r=>`<tr class="${r.passed?'':'failed'}"><td>${r.passed?'✓':'✗'} ${esc(r.label)}</td><td>${show(r.unit,r.value)} ${r.range?`in ${show(r.unit,r.range[0])}–${show(r.unit,r.range[1])}`:`≤ ${show(r.unit,r.limit)}`}</td></tr>`).join('')+(d.geometry_fixes?.note?`<tr><td colspan="2">${esc(d.geometry_fixes.note)}</td></tr>`:'');
  $('issues').innerHTML=d.issues.length?d.issues.map(i=>`<div class="issue ${esc(i.severity)}"><b>${esc(i.title)}</b> <small>· ${esc(i.owner)} agent</small><p>${esc(i.detail)}</p>${i.fixes.length?`<ul>${i.fixes.map(f=>`<li>${esc(f.description)}</li>`).join('')}</ul>`:''}</div>`).join(''):'<p class="muted">No problems found with the current evidence (a prediction until it is measured).</p>';
  const ok=d.requirements.every(r=>r.passed);
  $('sum-design').textContent=`${designName(candidate)} · ${dims(candidate)} · ${ok?'meets the requirements (predicted)':'misses a requirement (predicted)'}`;
  $('physics').innerHTML=`<p class="equation">${esc(p.equation)}</p>
  <div class="split" title="Bending vs clamp rotation"><div class="bend" data-width="${share*100}"></div><div class="mount" data-width="${(1-share)*100}"></div></div>
  <p class="muted">Bending of the part ${number(share*100,0)}% · rotation at the clamp ${number((1-share)*100,0)}% · uses ${number(p.utilization*100,0)}% of the allowed bend (with measurement margin)</p>
  <div class="meter${over?' over':''}"><span data-width="${Math.min(100,p.utilization*100)}"></span></div>
  <table class="facts-table"><tbody>
  <tr><td>Model</td><td>${esc(d.model.basis==='fitted'?'calibrated from measurements':'textbook values (not yet calibrated)')}</td></tr>
  <tr><td>Resin stiffness E</td><td>${number(p.youngs_modulus_pa/1e9,3)} GPa</td></tr>
  <tr><td>Clamp looseness Cθ</td><td>${number(p.rotational_compliance_rad_per_nm,3)} rad/N·m</td></tr>
  <tr><td>Section stiffness I</td><td>${number(p.second_moment_m4*1e12,0)} mm⁴</td></tr>
  <tr><td>Predicted bend${p.model_sd_m?' (±1σ)':''}</td><td>${mm(p.predicted_m)}${p.model_sd_m?' ± '+mm(p.model_sd_m):''}</td></tr>
  <tr><td>Stress at the clamp</td><td>${number(p.root_stress_pa/1e6,3)} MPa</td></tr>
  ${Object.entries(p.sensitivity).filter(([,v])=>v).map(([k,v])=>`<tr><td>+${number(v.step_m*1000)} mm ${esc(readable(k.replace('_m','')))}</td><td>${number(v.deflection_change_m*1000,4)} mm bend · ${number(v.volume_change_m3*1e9,0)} mm³ resin</td></tr>`).join('')}
  </tbody></table>`;
  $('physics').querySelectorAll('[data-width]').forEach(el=>{el.style.width=el.dataset.width+'%';});
  const fixable=d.issues.some(i=>GEOMETRY_ISSUES.includes(i.code) || i.code==='cad_mismatch') || d.issues.flatMap(i=>i.fixes).some(f=>f.kind==='experiment');
  diagnosisFixable=fixable || d.issues.length>0;
  $('agents-fix').textContent=fixable?'Let the agents fix it':d.issues.length?'What needs fixing?':'Nothing to fix';
  updateButtons();
}
function renderRepair(result){
  const r=result.repair;
  const rows=r.proposals.map(p=>`<tr><td>${esc(p.agent)}</td><td>${number(p.base_width_m*1000)} × ${number(p.wall_height_m*1000)} mm</td><td>${p.predicted_m!==undefined?mm(p.predicted_m):'—'}</td><td>${p.volume_m3?number(p.volume_m3*1e6,2)+' cm³':'—'}</td><td>${esc((p.supporters||[]).join(', ') || (p.validation_errors||[]).join('; '))}</td></tr>`).join('');
  const objections=r.reviews.filter(v=>!v.support).map(v=>`<li><b>${esc(v.agent)}</b> objects: ${esc(v.reason)}</li>`).join('');
  const requests=r.transcript.filter(t=>t.type==='evidence_request').map(t=>`<li>${t.agent?'<b>'+esc(t.agent)+'</b>: ':''}${esc(t.reason)}</li>`).join('');
  $('repair-report').innerHTML=`<div class="decision-box"><b>Agents' fix: ${esc(readable(r.status))}</b>${r.selected&&result.candidate?`<p>Chose the ${esc(r.selected.agent)} agent's fix: ${esc(r.selected.reason)}</p>`:''}${rows?`<div class="table-scroll"><table><thead><tr><th>Proposed by</th><th>Base × wall</th><th>Predicted bend</th><th>Resin</th><th>Supporters</th></tr></thead><tbody>${rows}</tbody></table></div>`:''}${objections||requests?`<ul>${objections}${requests}</ul>`:''}</div>`;
}

// ------------------------------------------------------------------ 2 · print
function renderPipeline(result) {
  if(!result){$('pipeline-report').innerHTML='';return;}
  const review=result.print_review, project=result.print_project;
  const votes=review?(review.votes||[]).map(v=>`<li><b>${esc(v.agent)}</b> ${v.support?'approves':'objects'}: ${esc(v.reason)}${v.concerns?.length?' <small>('+esc(v.concerns.join('; '))+')</small>':''}</li>`).join(''):'';
  const headline={ready_to_queue:'Approved by the agents: ready for the printer queue',stopped:'Stopped'+(result.stage?' at '+result.stage:'')}[result.status]||readable(result.status);
  $('pipeline-report').innerHTML=`<div class="decision-box ${result.status==='ready_to_queue'?'':'warn'}"><b>${esc(headline)}</b>${result.detail?`<p>${esc(result.detail)}</p>`:''}${project?`<p>PreForm checks: ${esc(project.validation.grade)}${project.estimate?` · ${number(project.estimate.total_print_time_s/3600,2)} h`:''}${project.material_usage?.volume_ml?` · ${number(project.material_usage.volume_ml)} mL resin with supports`:''} · <a href="/api/formlabs/projects/${encodeURIComponent(project.id)}/file">.form project</a></p>`:''}${review?`<p>Agents' review: ${esc(readable(review.status))}${review.gate_errors?.length?' · blocked by: '+esc(review.gate_errors.join('; ')):''}</p><ul>${votes}</ul>`:''}</div>`;
}
async function loadManufacturing(assetId, projectId){
  manufacturing=await api('/formlabs/projects');
  const asset=assetId || $('print-asset').value;
  $('print-asset').innerHTML='<option value="">Import a model first</option>'+manufacturing.assets.filter(a=>!a.candidate_id).map(a=>`<option value="${esc(a.id)}">${esc(a.label)}</option>`).join('');
  if(asset) $('print-asset').value=asset;
  renderPrintProjects(projectId);
}
function renderPrintProjects(projectId){
  const current=projectId || $('print-project').value;
  const label=p=>{const c=state?.candidates.find(c=>c.id===p.candidate_id); const review=(state?.print_reviews||[]).filter(r=>r.project_id===p.id).at(-1);
    return `${c?designName(c):p.candidate_id?'Other study':'Outside part'} · PreForm ${p.validation.grade}${p.candidate_id?' · review '+(review?readable(review.status):'missing'):''}`;};
  $('print-project').innerHTML='<option value="">Nothing prepared yet</option>'+manufacturing.projects.slice().reverse().map(p=>`<option value="${esc(p.id)}">${esc(label(p))}</option>`).join('');
  if(current) $('print-project').value=current;
  const p=manufacturing.projects.find(p=>p.id===$('print-project').value), receipts=p?manufacturing.receipts.filter(r=>r.project_id===p.id):[];
  $('print-report').innerHTML=receipts.length?`<p class="muted">${receipts.map(r=>`Queue: ${esc(readable(r.status))}${r.job_id?' · job '+esc(r.job_id):''}`).join('<br>')}</p>`:'';
  if (state) {
    $('print-sim').hidden=!sim(); $('print-physical').hidden=sim();
    const pending=state.pending_experiment;
    $('sum-print').textContent=sim()?'Not needed in a simulation':pending?`${designName(state.candidates.find(c=>c.id===pending.candidate_id))} needs printing`:receipts.length?'Sent to the printer queue':'Nothing to print right now';
  }
  updateButtons();
}

// ------------------------------------------------------------------ 3 · test
function renderTest() {
  const run=state.run, pending=state.pending_experiment, last=state.observations.at(-1);
  const scenario=run.simulation_scenario;
  $('test-sim').hidden=!sim(); $('test-physical').hidden=sim()||!pending;
  if (sim()) $('test-sim').innerHTML=(scenario?.physics==='fea'
    ?'<b>Measured by the virtual test rig.</b> Each reading is a 3D finite-element solve of the actual CAD geometry. The resin stiffness is drawn from its datasheet range and hidden from the agents, and the clamp is modelled as elastic jaws.'
    :'<b>Measured by the beam formula</b> with built-in material and clamp values (the quickest, least realistic simulation).')+
    (last?`<br>Latest reading: ${esc(KIND[last.purpose]||last.purpose)} on ${esc(designName(state.candidates.find(c=>c.id===last.candidate_id)).toLowerCase())}, ${mm(last.displacement_m)} at ${number(last.load_position_m*1000)} mm.`:'');
  if (pending && !sim()) {
    const c=state.candidates.find(c=>c.id===pending.candidate_id), f=$('fabrication-form').elements, m=$('measurement-form').elements;
    const registered=state.fabrications.some(x=>x.specimen_id===pending.specimen_id);
    $('test-instruction').textContent=`Clamp the first ${number(c.geometry.clamp_length_m*1000)} mm of the ${designName(c).toLowerCase()}, hang ${number(pending.force_n,3)} N ${number(pending.load_position_m*1000)} mm from the clamp, wait ${number(run.protocol.dwell_s)} s and read how far it bends. The model predicts ${mm(pending.prediction.displacement_m)}.`;
    $('specimen-id').textContent=`Measure the printed part and correct these if they differ from the design. Part label: ${pending.specimen_id}`;
    f.width.value=c.geometry.base_width_m*1000; f.height.value=c.geometry.wall_height_m*1000; f.thickness.value=c.geometry.thickness_m*1000;
    $('fabrication-form').hidden=registered; $('measurement-form').hidden=!registered;
    m.force.value=pending.force_n; m.position.value=pending.load_position_m*1000; m.dwell.value=run.protocol.dwell_s; m.recovery.value=run.protocol.recovery_s;
  }
  $('sum-test').textContent=pending&&!sim()?`Load ${number(pending.force_n,3)} N at ${number(pending.load_position_m*1000)} mm and measure the bend`:last?`${state.observations.length} measurements · latest ${mm(last.displacement_m)}`:'No measurements yet';
  $('observations').innerHTML=state.observations.slice().reverse().map((o,i)=>`<tr><td>${state.observations.length-i}</td><td>${esc(KIND[o.purpose]||o.purpose)}</td><td>${esc(designName(state.candidates.find(c=>c.id===o.candidate_id)))}</td><td>${number(o.load_position_m*1000)} mm</td><td>${number(o.force_n,3)} N</td><td>${number(o.displacement_m*1000,3)} ± ${number(o.uncertainty_m*1000,3)} mm</td><td title="${esc(o.assessment.reasons.join(', '))}">${esc(RESULT(o.assessment.status))}</td></tr>`).join('');
  $('fit-warning').textContent=(state.model.fit.warnings || []).join(' · ');
  $('token-note').textContent=`${number(state.budget.tokens_used)} of ${number(run.protocol.token_budget)} AI tokens used.`;
}

// ------------------------------------------------------------------ 4 · decide, finish
function renderDecide() {
  const pilots=state.observations.filter(o=>o.purpose==='initial').length, fit=state.model.fit.status;
  const d=state.decisions.at(-1);
  $('decide-lead').textContent=pilots<4?`First, ${4-pilots} more baseline test${4-pilots>1?'s':''} on the starting design calibrate the model. No AI is needed for these.`
    :`The model is ${FIT[fit]||readable(fit)}. ${state.run.method==='adaptive'?'The optimizer':'The agents'} now choose between another test and a new, lighter design.`;
  $('agent-round').textContent=pilots<4?`Run baseline test ${pilots+1} of 4`:state.run.method==='adaptive'?'Ask the optimizer for the next step':'Ask the agents for the next step';
  $('sum-decide').textContent=d?.selected?`Last: agreed on a ${actionText(d.selected)}`:d?`Last: ${readable(d.status)}`:'Not started';
  $('decision-summary').innerHTML=d?`<div class="decision-box ${d.selected?'':'warn'}"><b>${d.selected?'Agreed on a '+esc(actionText(d.selected)):'No agreement ('+esc(readable(d.status))+')'}</b><p>${esc(d.selected?.reason || 'See the agents panel and the full lab notebook for objections or errors.')}</p></div>`:'';
  const a=state.acceptance, p=state.run.protocol, frozen=state.nominations[0];
  $('acceptance').textContent=`${a.passing_specimens} of ${p.final_copies} passed so far.`;
  $('sum-finish').textContent=frozen?`Final: ${designName(state.candidates.find(c=>c.id===frozen.candidate_id))} · ${a.passing_specimens}/${p.final_copies} copies passed`:'After the search';
}

// ------------------------------------------------------------------ handlers
$('new-study').onclick=()=>{$('setup').hidden=!$('setup').hidden; window.scrollTo({top:0,behavior:'smooth'});};
$('close-setup').onclick=()=>{$('setup').hidden=true;};
function scenarioFields() {
  const form=$('create-form'), simulated=form.elements.mode.value==='simulated';
  $('scenario-field').hidden=!simulated;
  const s=(config.scenarios||[]).find(s=>s.name===form.elements.scenario.value);
  const limit=s?.scenario.protocol_overrides?.max_deflection_m;
  if(simulated && limit) form.elements.limit.value=+(limit*1000).toFixed(3);  // the limit that makes this scenario a real trade-off
}
$('create-form').elements.mode.addEventListener('change',scenarioFields);
$('scenario-picker').addEventListener('change',scenarioFields);
$('run-picker').onchange=()=>work(async()=>{await loadRun($('run-picker').value);$('notice').hidden=true;});
$('candidate-picker').onchange=()=>{ selectCandidate($('candidate-picker').value); renderCharts(); };
$('agent-mode').onchange=()=>{ if(state) renderAgents(); updateButtons(); if(live() && !config.live_ready) notice('Live AI is not configured in .env; rule-based mode still works.',true); };
$('create-form').addEventListener('submit',event=>{event.preventDefault();work(async()=>{
  const f=new FormData(event.target);
  const preset=f.get('team'), override=(f.get('team_model')||'').trim();
  const team=preset==='env'?null:Object.fromEntries(['mechanics','fabrication','measurement','single'].map(r=>[r,{provider:preset,model:override||config.providers[preset].model}]));
  const scenario=f.get('mode')==='simulated'?(config.scenarios||[]).find(s=>s.name===f.get('scenario')):null;
  const g={...config.defaults.geometry,section_family:f.get('family'),length_m:+f.get('length')/1000,base_width_m:+f.get('width')/1000,wall_height_m:+f.get('height')/1000,thickness_m:+f.get('thickness')/1000,material_batch:f.get('material')};
  state=await api('/runs',{name:f.get('name'),mode:f.get('mode'),method:f.get('method'),seed:+f.get('seed'),geometry:g,team,scenario:scenario?.scenario||null,protocol:{...config.defaults.protocol,...(scenario?.scenario.protocol_overrides||{}),proof_force_n:+f.get('force'),proof_position_m:+f.get('position')/1000,max_deflection_m:+f.get('limit')/1000,
    base_width_min_m:+f.get('width_min')/1000,base_width_max_m:+f.get('width_max')/1000,wall_height_min_m:+f.get('height_min')/1000,wall_height_max_m:+f.get('height_max')/1000,
    max_volume_m3:f.get('volume')?+f.get('volume')/1e6:null,max_root_stress_pa:f.get('stress')?+f.get('stress')*1e6:null,token_budget:+f.get('tokens')}});
  $('setup').hidden=true; await listRuns(state.run.id); currentStage=null; render(); notice('Study created. The highlighted step is yours.');
},'Creating the study…');});

$('cad-build').onclick=()=>work(async()=>{
  const cad=await buildCad(candidate.id);
  notice(cad.check.status==='pass'?`Built in ${cad.source==='fusion'?'Fusion':'FreeCAD'}; it matches the analysed part.`:'The CAD differs from the analysed part; see step 1.',cad.check.status!=='pass');
},'Building the CAD…');
$('cad-report').addEventListener('click',e=>{const id=e.target.dataset?.openCad;if(id)work(async()=>{const r=await api(`/runs/${state.run.id}/cad/${id}/open`,{});notice(`Opened ${r.opened} in FreeCAD.`);},'Opening FreeCAD…');});
$('agents-fix').onclick=()=>work(async()=>{
  const result=await api(`/runs/${state.run.id}/candidates/${diagnosis.candidate_id}/fix`,{live:live()});
  await loadRun(state.run.id); $('candidate-picker').value=result.candidate_id; selectCandidate(result.candidate_id); openStage('design');
  if(result.repair) renderRepair({repair:result.repair,candidate:result.repair.new_candidate_id?{id:result.repair.new_candidate_id}:null});
  $('repair-report').insertAdjacentHTML('afterbegin',`<div class="decision-box ${result.needs_you.length?'warn':''}"><b>What the agents did</b><ul>${result.actions.map(a=>`<li>${esc(a.summary)}</li>`).join('')}</ul>${result.needs_you.length?`<b>Needs you</b><ul>${result.needs_you.map(n=>`<li><b>${esc(n.title)}:</b> ${esc(n.action)}</li>`).join('')}</ul>`:''}</div>`);
  const after=result.diagnosis_after.status;
  notice(after==='ok'?'Fixed: the selected design now meets every requirement (as predicted).':result.needs_you.length?'The agents fixed what they could; some items need you (see step 1).':'The agents acted; see step 1 for the result.',after!=='ok' && result.needs_you.length>0);
},'The agents are fixing what they can…');

function runAction(path,body={},label='Working…') { return work(async()=>{const result=await api(`/runs/${state.run.id}/${path}`,body);await loadRun(state.run.id);notice(result.status?readable(result.status):'Saved.');},label); }
$('agent-round').onclick=()=>runAction('step',{live:live()},live()?'The agents are proposing and reviewing; this can take a minute…':'Working…');
$('demo').onclick=()=>runAction('demo',{},'Running the whole simulated study…');
$('reserved').onclick=()=>runAction('reserved-check');
$('nominate').onclick=()=>runAction('nominate',{candidate_id:candidate.id});
$('accept').onclick=()=>runAction('acceptance');
$('fabrication-form').onsubmit=event=>{event.preventDefault();const f=new FormData(event.target),pending=state.pending_experiment,c=state.candidates.find(c=>c.id===pending.candidate_id);runAction('fabrications',{candidate_id:c.id,specimen_id:pending.specimen_id,actual_geometry:{...c.geometry,base_width_m:+f.get('width')/1000,wall_height_m:+f.get('height')/1000,thickness_m:+f.get('thickness')/1000},notes:f.get('notes'),duration_s:0});};
$('measurement-form').onsubmit=event=>{event.preventDefault();const f=new FormData(event.target);runAction(`experiments/${state.pending_experiment.id}/observations`,{actual_force_n:+f.get('force'),actual_load_position_m:+f.get('position')/1000,displacement_m:+f.get('displacement')/1000,uncertainty_m:+f.get('uncertainty')/1000,residual_m:+f.get('residual')/1000,fixture_motion_m:+f.get('fixture')/1000,dwell_s:+f.get('dwell'),recovery_s:+f.get('recovery'),quality_flags:f.get('flags').split(',').map(x=>x.trim()).filter(Boolean),human_notes:f.get('notes')});};

$('preform-connect').onclick=()=>work(async()=>{
  const status=await api('/formlabs/status'); preformReady=status.connected;
  $('preform-status').textContent=status.connected?`Connected to PreForm ${status.version} · ${status.devices.length} printer(s) found.`:status.detail;
  $('print-printer').innerHTML='<option value="">Choose the printer</option>'+status.devices.filter(d=>d.is_connected).map(d=>`<option value="${esc(d.id)}">${esc(d.product_name)} · ${esc(d.id)} · ${esc(d.tank_material_code || 'resin not reported')}</option>`).join('');
  if(status.connected){
    const catalog=await api('/formlabs/catalog');printPresets=[];
    const studio=catalog.printer_types.find(p=>p.label==='Form Studio 1');
    if(studio) $('material-status').textContent=studio.materials.some(m=>/Alpha.*Fast/i.test(m.label))?'Alpha Fast Resin is available for Form Studio 1. Choose it below.':'No Alpha Fast preset for Form Studio 1 in this catalog; check PreForm is up to date.';
    for(const p of catalog.printer_types || []) if(/^Form(?:\s|$)/i.test(p.label)) for(const m of p.materials || []) for(const setting of m.material_settings || [])
      if(typeof setting.scene_settings.layer_thickness_mm === 'number') printPresets.push({label:`${p.label} / ${m.label} / ${setting.label}`,profile:setting.scene_settings});
    printPresets.sort((a,b)=>a.label.localeCompare(b.label));
    $('print-preset').innerHTML='<option value="">Select the printer and resin</option>'+printPresets.map((p,i)=>`<option value="${i}">${esc(p.label)}</option>`).join('');
  }
  notice(status.connected?'PreForm connected. Choose the printer and resin, and enter the minimum wall.':status.detail,!status.connected);
},'Connecting to PreForm…');
function profile(){ const preset=printPresets[+$('print-preset').value]; if(!preset) throw new Error('Connect PreForm and choose the printer and resin'); return {...preset.profile,minimum_wall_mm:+$('minimum-wall').value}; }
$('pipeline-button').onclick=()=>work(async()=>{
  const result=await api(`/runs/${state.run.id}/print-pipeline`,{candidate_id:candidate.id,cad_source:$('cad-source').value,live:live(),profile:profile()});
  await loadRun(state.run.id); $('candidate-picker').value=candidate.id; selectCandidate(candidate.id);
  await loadManufacturing(null,result.print_project?.id); renderPipeline(result); openStage('print');
  notice(result.status==='ready_to_queue'?'Approved. Check the .form project, add your notes and send it to the printer queue.':readable(result.status)+(result.detail?': '+result.detail:''),result.status!=='ready_to_queue');
},'Building the CAD, preparing it in PreForm and asking the agents to review. This can take several minutes…');
$('preform-queue').onsubmit=e=>{e.preventDefault();work(async()=>{
  try{const result=await api(`/formlabs/projects/${$('print-project').value}/queue`,{printer:$('print-printer').value,review_notes:$('print-review').value});notice(`Job ${result.job_id} is in the printer queue. Start it at the printer, then measure it in step 3.`);}
  finally{await loadManufacturing(); currentStage=null; renderStages(); }
},'Rechecking the saved project and sending it to the printer queue…');};
['print-project','print-printer','print-asset','print-preset'].forEach(id=>$(id).addEventListener('change',()=>{ if(id==='print-project') renderPrintProjects(); updateButtons(); }));
$('minimum-wall').oninput=updateButtons;
$('cad-upload').onsubmit=e=>{e.preventDefault();work(async()=>{
  const file=$('cad-file').files[0];if(!file)return;
  const response=await fetch(`/api/runs/${state.run.id}/candidates/${candidate.id}/cad-upload?filename=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
  const result=await response.json();if(!response.ok)throw new Error(result.detail);
  await loadRun(state.run.id);$('candidate-picker').value=result.candidate_id;selectCandidate(result.candidate_id);$('cad-source').value='uploaded';
  notice(result.check.status==='pass'?'Export matches the analysed part.':'The export differs from the analysed part; see step 1.',result.check.status!=='pass');
},'Checking the export…');};
$('mesh-upload').onsubmit=e=>{e.preventDefault();work(async()=>{
  const file=$('mesh-file').files[0];if(!file)return;
  const response=await fetch('/api/formlabs/assets?filename='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
  const result=await response.json();if(!response.ok)throw new Error(result.detail);
  await loadManufacturing(result.id);notice('STL imported. Prepare it with the printer settings from step 2.');
},'Importing…');};
$('preform-prepare').onsubmit=e=>{e.preventDefault();work(async()=>{
  const result=await api('/formlabs/prepare',{asset_id:$('print-asset').value,profile:profile()});
  await loadManufacturing(null,result.id);notice('Outside part prepared. Select it in step 2 to queue it.');
},'PreForm is preparing the part…');};
$('onshape-check').onclick=()=>work(async()=>{const s=await api('/onshape/status');notice(s.ready?'Onshape is ready.':(s.detail||'Onshape needs setup; see docs/onshape.md.'),!s.ready);});
$('onshape-bootstrap').onclick=()=>work(async()=>{const s=await api('/onshape/bootstrap',{run_id:state?.run.id||null});notice(`Created ${s.created.length} Onshape variable(s).`);});

let resizeTimer; window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderCharts,150);});
// Designs sent from Fusion appear without a reload; tool status stays current.
setInterval(async()=>{
  refreshCadStatus();
  if(busy || !state || document.hidden) return;
  try{
    const fresh=await api('/runs/'+state.run.id);
    if(fresh.candidates.length>state.candidates.length || fresh.observations.length!==state.observations.length || fresh.decisions.length!==state.decisions.length){
      const added=fresh.candidates.length>state.candidates.length?fresh.candidates.at(-1):null; state=fresh;
      if(added) $('candidate-picker').value=added.id;
      render();
      if(added?.proposed_by==='human:fusion') { openStage('design'); notice('Your Fusion edit arrived as a new design. See step 1.'); }
    }
  }catch(error){}
},8000);
(async()=>{try{
  config=await api('/config'); await refreshCadStatus();
  const labels={nvidia:'NVIDIA',openai:'OpenAI (ChatGPT)',anthropic:'Anthropic (Claude)',gemini:'Google (Gemini)'};
  $('team-preset').insertAdjacentHTML('beforeend',Object.entries(config.providers).filter(([,p])=>p.ready).map(([name,p])=>`<option value="${esc(name)}">${esc(labels[name]||name)} · ${esc(p.model.split('/').at(-1))} (all three)</option>`).join(''));
  if(config.providers.nvidia?.ready) $('team-preset').value='nvidia';
  $('scenario-picker').innerHTML=(config.scenarios||[]).map(s=>`<option value="${esc(s.name)}">${esc(s.label)}</option>`).join('');
  scenarioFields();
  const runs=await listRuns();
  await loadManufacturing();
  if(runs.length) await loadRun(runs.at(-1).id); else { $('empty').hidden=false; $('setup').hidden=false; }
}catch(error){notice(error.message,true);}})();
