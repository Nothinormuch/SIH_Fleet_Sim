'use strict';
const $ = (id) => document.getElementById(id);
const readOnlyMultihost = new URLSearchParams(window.location.search).get('source') === 'multihost';
const finiteMetric = value => typeof value === 'number' && Number.isFinite(value);
const metricText = (value, digits=2) => finiteMetric(value) ? value.toFixed(digits) : 'unavailable';
const sumMetrics = values => values.length && values.every(finiteMetric) ? values.reduce((a,b)=>a+b,0) : null;

function sourceIdentity(state) {
  const result = state?.result;
  // A finished result owns its identity. Never fill a missing historical pin
  // from a newer session configuration, the selected policy, or current HEAD.
  const source = result ? result.config?.source_sha256 ?? result.source_sha256
    : state?.config?.source_sha256;
  const label = result ? 'Recorded run' : 'Session configuration';
  const validHash = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
  if (validHash(source)) {
    const changed = result?.source_unchanged_at_end === false;
    return {
      text: `${label} · ${changed ? 'STARTUP ' : ''}source SHA-256 ${source.slice(0, 16)}… · ${changed ? 'source changed during run' : 'current checkout not compared'}`,
      title: `Source SHA-256: ${source}. This fingerprint belongs to the displayed ${result ? 'evidence' : 'session'}, not a check of the current checkout.`,
    };
  }
  // Local HIL reports carry per-file hashes, not the multi-host aggregate pin.
  // Keep that distinction visible instead of inventing an equivalent digest.
  if (source && typeof source === 'object' && !Array.isArray(source)) {
    const hashes = Object.values(source);
    if (hashes.length && hashes.every(validHash)) {
      return {
        text: `${label} · ${hashes.length} per-file SHA-256 hashes recorded · aggregate source fingerprint unavailable`,
        title: 'Download the evidence to inspect its recorded per-file hashes. The current checkout is not compared.',
      };
    }
  }
  return {text: `${label} · source SHA-256 unavailable · no valid recorded fingerprint provided`,
    title: 'Source identity cannot be established from the displayed telemetry or report.'};
}

if (readOnlyMultihost) {
  document.title = 'BIOS · Multi-host observer';
  $('lab-title').textContent = 'Two hosts. Independent robot brains.';
  $('lab-description').textContent = 'Read-only view of the terminal-owned network demonstration. Real controller processes exchange peer messages; robot bodies, sensors and warehouse physics are simulated.';
  $('lab-scope').innerHTML = 'NETWORKED SOFTWARE IN THE LOOP<br><small>Observed hosts · not Raspberry Pi emulation or certification</small>';
  $('source-note').hidden = false;
  $('map-empty').textContent = 'Waiting for the multi-host referee to publish live telemetry.';
  document.querySelector('.floor-note').textContent = 'The terminal-owned referee publishes positions and measured events. This page only observes; it cannot change the workload, choose winners or command robots.';
}
const colors = ['#35c6f4', '#46d39a', '#f5b843'];
let latest = null;
let busy = false;
let actionError = null;
let connected = true;
let packetCounts = {};
let liveTwin = null;
let viewMode = '3d';
let selectedRobot = 'AMR01';
let twinFailed = false;
const labels = {BD:'Task bid', AW:'Auction award', TD:'Task completion', TN:'Task announced',
  HB:'Heartbeat', IN:'Movement intent', CL:'Spatial claim'};

for (let i = 0; i < 10; i++) {
  const rid = `AMR${String(i + 1).padStart(2, '0')}`;
  const card = document.createElement('article');
  card.className = 'node'; card.id = rid; card.hidden = i >= 3; card.style.setProperty('--color', colors[i % colors.length]);
  card.innerHTML = `<div class="board" aria-hidden="true"><div class="pins"></div><div class="chip">BIOS<small>EDGE NODE</small></div><div class="ports"></div><i class="led"></i><span class="board-label">VIRTUAL BOARD</span></div><div><div class="node-top"><h3>${rid}</h3><span class="badge">Offline</span></div><div class="node-data"><span class="datum">Process<b data-key="pid">—</b></span><span class="datum">Battery<b data-key="battery">—</b></span><span class="datum">Sensor frames<b data-key="sensor">—</b></span><span class="datum">Commands<b data-key="actuator">—</b></span><span class="datum">Peer packets<b data-key="peer">—</b></span><span class="datum">Sensor link<b data-key="link">—</b></span></div><div class="node-actions"><span class="command">Waiting for launch</span><button disabled>Disconnect sensor · 2s</button></div></div>`;
  card.querySelector('button').addEventListener('click', () => action('cut-sensor', {robot:rid}));
  const selectButton = document.createElement('button');
  selectButton.className = 'node-select'; selectButton.textContent = rid;
  selectButton.setAttribute('aria-label', `Select ${rid} in warehouse`);
  selectButton.addEventListener('click', () => selectRobot(rid));
  card.querySelector('h3').replaceChildren(selectButton);
  const taskLine = document.createElement('div'); taskLine.className = 'node-task';
  taskLine.innerHTML = 'Task <b data-key="task">—</b> · Cargo <b data-key="cargo">—</b>';
  card.querySelector('.node-data').after(taskLine);
  const motionLine = document.createElement('span'); motionLine.className = 'datum';
  motionLine.innerHTML = 'Actual speed<b data-key="speed">—</b>';
  card.querySelector('.node-data').append(motionLine);
  if (readOnlyMultihost) {
    const hostLine = document.createElement('span'); hostLine.className = 'datum';
    hostLine.innerHTML = 'Controller host / IP<b data-key="host">—</b>';
    card.querySelector('.node-data').append(hostLine);
    card.querySelector('.board-label').textContent = 'NETWORKED NODE';
    card.querySelector('.node-actions button').hidden = true;
  }
  $('controllers').append(card);
}

function selectRobot(rid) {
  selectedRobot = rid; $('selected-robot').value = rid; liveTwin?.select(rid);
  for (let i = 1; i <= 10; i++) {
    const card = $(`AMR${String(i).padStart(2, '0')}`), selected = card.id === rid;
    card.classList.toggle('selected', selected);
    card.querySelector('.node-select').setAttribute('aria-pressed', String(selected));
  }
}
function setView(mode) {
  viewMode = twinFailed ? '2d' : mode;
  $('map').hidden = viewMode !== '2d'; $('twin-live').hidden = viewMode !== '3d';
  $('view3d').setAttribute('aria-pressed', String(viewMode === '3d'));
  $('view2d').setAttribute('aria-pressed', String(viewMode === '2d'));
  $('camera-mode').disabled = viewMode !== '3d'; $('fit-camera').disabled = viewMode !== '3d';
  liveTwin?.setVisible(viewMode === '3d');
  if (viewMode === '2d') drawMap(latest?.snapshot);
}
function fallbackTo2D(message) {
  twinFailed = true; $('view3d').disabled = true;
  $('view-notice').textContent = message; setView('2d');
}
$('view3d').addEventListener('click', () => setView('3d'));
$('view2d').addEventListener('click', () => setView('2d'));
$('camera-mode').addEventListener('change', event => liveTwin?.setCamera(event.target.value));
$('selected-robot').addEventListener('change', event => selectRobot(event.target.value));
$('fit-camera').addEventListener('click', () => {
  liveTwin?.fit(); if (liveTwin) $('camera-mode').value = liveTwin.camera;
});
$('expand-view').addEventListener('click', () => {
  const expanded = document.querySelector('.workspace').classList.toggle('expanded');
  $('expand-view').textContent = expanded ? 'Compact view' : 'Expand view';
  $('expand-view').setAttribute('aria-pressed', String(expanded));
  if (viewMode === '2d') requestAnimationFrame(() => drawMap(latest?.snapshot));
});
import('./edge-lab-twin.js').then(({LiveEdgeTwin}) => {
  liveTwin = new LiveEdgeTwin($('twin-live'), selectRobot, fallbackTo2D);
  liveTwin.select(selectedRobot); liveTwin.setCamera($('camera-mode').value);
  liveTwin.receive(latest); setView(viewMode);
}).catch(error => fallbackTo2D(`3D graphics unavailable (${error.message}). Showing live 2D telemetry.`));
selectRobot(selectedRobot);

async function api(path, payload) {
  if (readOnlyMultihost && (payload !== undefined || path !== 'status')) throw new Error('Multi-host observer is read-only. Use the commissioned terminals for control.');
  const endpoint = readOnlyMultihost ? '/api/multihost/status' : `/api/edge-lab/${path}`;
  const response = await fetch(endpoint, payload === undefined ? {cache:'no-store', signal:AbortSignal.timeout(2000)} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), signal:AbortSignal.timeout(5000)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
async function action(path, payload={}) {
  if (busy || readOnlyMultihost) return;
  busy = true; actionError = null; $('error').hidden = true; render();
  try { latest = await api(path, payload); connected = true; }
  catch (error) { actionError = error.message; }
  finally { busy = false; render(); }
}
function runOptions(mode) {
  return {mode, policy:$('policy').value, profile:$('profile').value, robots:Number($('robot-count').value), seed:Number($('seed').value)};
}
$('policy').addEventListener('change', render);
$('start').addEventListener('click', () => action('start', runOptions('normal')));
$('fault-demo').addEventListener('click', () => action('start', runOptions('sensor_demo')));
$('stop').addEventListener('click', () => action('stop'));
$('download').addEventListener('click', () => {
  if (!latest?.result) return;
  const blob = new Blob([JSON.stringify({result:latest.result, manual_sensor_faults:latest.faults}, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = readOnlyMultihost ? 'bios-multihost-evidence.json' : 'bios-virtual-edge-evidence.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

function render() {
  const state = latest?.state || 'idle';
  const source = sourceIdentity(latest);
  $('evidence-source').textContent = source.text;
  $('evidence-source').title = source.title;
  const active = ['starting','running','stopping'].includes(state);
  const live = connected && state === 'running' && latest.snapshot_age_s < 1;
  $('start').disabled = readOnlyMultihost || busy || active; $('fault-demo').disabled = readOnlyMultihost || busy || active;
  $('stop').disabled = readOnlyMultihost || busy || !active || state === 'stopping';
  $('download').disabled = !latest?.result;
  const names = {idle:'Ready to launch',starting:'Launching independent edge processes…',running:'● Live · real UDP sockets',stopping:'Stopping edge processes…',cancelled:'Run stopped · partial evidence',finished:'Run finished · evidence recorded',failed:'Run failed'};
  if (readOnlyMultihost) { names.idle='Waiting for terminal-owned multi-host run'; names.starting='Waiting for both host agents and controller readiness…'; names.running='● Live · multi-host observer · read-only'; }
  $('status').textContent = !connected ? 'Disconnected from dashboard server' :
    state === 'running' && !live ? 'Telemetry delayed · last received positions' : names[state];
  const shownError = latest?.error || actionError;
  $('error').textContent = shownError || '';
  $('error').hidden = !shownError;
  const snapshot = latest?.snapshot;
  const result = latest?.result;
  const nodes = snapshot?.nodes || [];
  const count = latest?.robots || 3;
  for (const id of ['profile','robot-count','seed','policy']) $(id).disabled = readOnlyMultihost || active;
  if((active || readOnlyMultihost) && latest?.profile) {
    if (readOnlyMultihost && ![...$('profile').options].some(o=>o.value===latest.profile)) $('profile').add(new Option(latest.profile,latest.profile));
    if (readOnlyMultihost && ![...$('robot-count').options].some(o=>o.value===String(count))) $('robot-count').add(new Option(String(count),String(count)));
    $('profile').value=latest.profile; $('robot-count').value=String(count);
    $('seed').value=String(latest.seed);
    $('policy').value=latest.policy || 'BIOS_PIBT.6';
  }
  const shownPolicy = active || result ? latest?.policy : $('policy').value;
  $('policy-label').textContent = shownPolicy === 'BIOS_PIBT.7' ? '7.0' : '6.0';
  if ($('selected-robot').options.length !== count) {
    $('selected-robot').replaceChildren(...Array.from({length:count}, (_,i) => {
      const option=document.createElement('option'); option.textContent=`AMR${String(i+1).padStart(2,'0')}`; return option;
    }));
    if (Number(selectedRobot.slice(3)) > count) selectedRobot='AMR01';
    selectRobot(selectedRobot);
  }
  $('process-count').textContent = snapshot ? `${nodes.filter(n => n.running).length} / ${count}` : '—';
  $('completion').textContent = result ? `${result.tasks_completed} / ${result.tasks_announced}` : snapshot ? `${snapshot.observed_completed.length} / ${snapshot.tasks.length}` : '— / 3';
  $('completion-note').textContent = result ? readOnlyMultihost ? 'Verified within the declared evidence window' : 'Confirmed by the final node reports' : 'Live completion announcements · final verification pending';
  $('contacts').textContent = snapshot ? Object.values(result?.contacts || snapshot.contacts).reduce((a,b)=>a+b,0) : '—';
  $('packets').textContent = snapshot ? nodes.reduce((a,n)=>a+n.peer_packets_observed,0).toLocaleString() : '—';
  const time = result?.simulation_time_s ?? snapshot?.world.t ?? 0;
  const duration = snapshot?.duration_s || latest?.duration_s || 20;
  $('clock').textContent = `${time.toFixed(1)} / ${duration.toFixed(1)} s`;
  $('progress').style.width = `${Math.min(100,time/duration*100)}%`;
  $('scenario-events').textContent = (snapshot?.events || []).map(e => `${e.t.toFixed(1)} s · ${e.type.replaceAll('_',' ')} · ${e.robot || e.id}`).join(' | ') || 'No blockage or process-stop events.';
  if(snapshot?.human_behavior_events) $('scenario-events').textContent += ` Human behavior changes: ${snapshot.human_behavior_events}.`;
  $('map-empty').hidden = !!snapshot;
  $('twin-live').style.opacity = snapshot ? '1' : '0';
  liveTwin?.setVisible(viewMode === '3d' && !!snapshot);
  if (liveTwin && snapshot) {
    try { liveTwin.receive(latest); }
    catch (error) { fallbackTo2D(`3D display unavailable (${error.message}). Showing live 2D telemetry.`); }
  }
  for (let i=0;i<10;i++) {
    const rid=`AMR${String(i+1).padStart(2,'0')}`, card=$(rid), node=nodes.find(n=>n.id===rid);
    card.hidden = i >= count;
    const body=snapshot?.world.robots.find(n=>n.id===rid);
    const value=(key,v)=>{card.querySelector(`[data-key="${key}"]`).textContent=v;};
    value('pid',node?.pid || '—'); value('battery',body?`${(body.batt*100).toFixed(0)}%`:'—');
    value('sensor',node?.sensor_frames ?? '—'); value('actuator',node?.actuator_frames ?? '—');
    value('peer',node?.peer_packets_observed ?? '—');
    if (readOnlyMultihost) value('host',node ? `${node.host_hostname || node.host || 'Unknown host'} · ${node.host_ip || 'IP unavailable'}` : '—');
    value('speed', body ? `${Math.abs(body.v).toFixed(2)} m/s` : '—');
    value('task', node?.visual_status?.task || 'None');
    value('cargo', node?.visual_status?.carry ? 'On board' : 'Empty');
    value('link',node ? active ? node.sensor_cut?'Disconnected':'Connected' : 'Closed' : '—');
    card.querySelector('.badge').textContent = node ? !node.running?'Process stopped':!live ? active?'Waiting':'Exited' : node.command.safety_stop?'Safety stop':node.sensor_cut?'Sensor lost': 'Online' : 'Offline';
    const board=card.querySelector('.board'); board.classList.toggle('active',live);
    board.classList.toggle('cut',live && !!node?.sensor_cut);
    board.classList.toggle('pulse',live && !!node && node.peer_packets_observed !== packetCounts[rid]);
    packetCounts[rid]=node?.peer_packets_observed;
    card.querySelector('.node-actions button').disabled=readOnlyMultihost || busy || !live || !node?.running || !!node?.sensor_cut;
    card.querySelector('.command').textContent = node ? !active ? 'Controller exited' : node.command.safety_stop?'STOP · v = 0':`v ${node.command.v.toFixed(2)} m/s · ω ${node.command.omega.toFixed(2)}`:'Waiting for launch';
  }
  const trace=$('trace'); trace.replaceChildren();
  if (!snapshot?.packets.length) trace.textContent='No packets received yet.';
  else for (const p of [...snapshot.packets].reverse().slice(0,18)) {
    const row=document.createElement('div'); row.className='packet';
    for (const [tag,text] of [['span',`${p.t.toFixed(1)}s`],['b',p.src],['span',`${labels[p.type] || p.type}${p.task ? ` · ${p.task}`:''}`],['span',`#${p.seq}`]]) {
      const el=document.createElement(tag); el.textContent=text; row.append(el);
    }
    trace.append(row);
  }
  const panel=document.querySelector('.result-panel');
  panel.classList.toggle('success',!!result?.success);
  panel.classList.toggle('failed',!!result && !result.success);
  if (result) {
    $('result-title').textContent=result.cancelled?'Run stopped early':result.success?'Deployment run passed':'Run needs investigation';
    const fault=result.sensor_cut_evidence;
    const cutRobot = fault?.robot || latest?.sensor_cut?.robot || (readOnlyMultihost ? 'Selected AMR' : 'AMR01');
    $('result-detail').textContent = fault ? `${cutRobot} sensor-loss stop command: ${finiteMetric(fault.response_s)?`${(fault.response_s*1000).toFixed(1)} ms`:'not observed'}. Recovery: ${(fault.recovered_after_sensor_return ?? fault.recovered)?'confirmed':'not observed'}. ${result.tasks_completed}/${result.tasks_announced} tasks completed. ${result.control_deadlines_met===true?'No measured control deadline misses.':result.control_deadlines_met===false?'Control timing gate not passed.':'Control timing unavailable.'}` : `${result.tasks_completed}/${result.tasks_announced} tasks completed. ${result.separate_edge_nodes?`${result.robots} distinct process reports verified.`:'Process verification incomplete.'} ${result.peer_messages_observed?'Peer communication observed at all nodes.':'Peer verification incomplete.'}`;
    if(readOnlyMultihost) $('result-detail').textContent+=` Physical host separation: ${result.real_multihost===true?'observed':result.real_multihost===false?'not established':'unavailable'}. Networked software evidence; no physical AMR or Raspberry Pi performance claim.`;
    const reports=result.nodes || [];
    const expectedReports=result.robots || latest?.robots || count;
    const reportIds=reports.map(n=>n.robot_id);
    const rosterComplete=reports.length===expectedReports && new Set(reportIds).size===expectedReports && Array.from({length:expectedReports},(_,i)=>`AMR${String(i+1).padStart(2,'0')}`).every(id=>reportIds.includes(id));
    const times=reports.map(n=>`${n.robot_id}: ${metricText(n.runtime?.loop_p99_ms)} ms`);
    $('timing').textContent=`${rosterComplete?'':`INCOMPLETE NODE REPORTS (${reports.length}/${expectedReports}); fleet timing unavailable. `}Measured p99 loop time / 20 ms budget: ${times.join(' · ') || 'unavailable'}`;
    const fleetSum=values=>rosterComplete?sumMetrics(values):null;
    const misses=fleetSum(reports.map(n=>n.runtime?.deadline_misses));
    const maxima=reports.map(n=>n.runtime?.loop_max_ms);
    const maximum=rosterComplete && maxima.length && maxima.every(finiteMetric) ? Math.max(...maxima) : null;
    $('timing').textContent+=` · Maximum ${metricText(maximum)} ms · Deadline misses ${metricText(misses,0)}`;
    const cycles=reports.map(n => `${n.robot_id}: ${metricText(n.full_cycle?.loop_max_ms)} ms`);
    $('timing').textContent+=` · Full-cycle maxima (reported nodes): ${cycles.join(' · ') || 'unavailable'} · Fleet full-cycle overruns ${metricText(fleetSum(reports.map(r=>r.full_cycle?.deadline_misses)),0)} · Fleet late wakeups ${metricText(fleetSum(reports.map(r=>r.scheduling_late_ticks)),0)}`;
    if(misses) $('result-detail').textContent+=` Timing gate failed: ${misses} control loops exceeded the 20 ms budget on this host.`;
  } else {
    $('result-title').textContent=readOnlyMultihost?'Observe. Verify. Explain.':'Disconnect. Stop. Recover.';
    const plannedCut=latest?.sensor_cut;
    $('result-detail').textContent=readOnlyMultihost ? plannedCut ? `Terminal-owned test: ${plannedCut.robot} sensor input is cut at ${plannedCut.at_s} s for ${plannedCut.duration_s} s. This viewer cannot inject faults or assign tasks.` : 'Waiting for terminal-owned evidence. This viewer cannot start controllers, stop robots or inject faults.' : latest?.mode==='sensor_demo'?'Automatic test: AMR01 sensor input is cut at 3 s and restored at 5 s. Watch its status, zero-speed stop, and subsequent recovery.':'Run the sensor-loss demo, or disconnect any sensor manually for 2 seconds. Watch the actual actuator command change while the other controllers keep running.';
    $('timing').textContent='Loop timing appears after the run finishes.';
  }
  if (viewMode === '2d') drawMap(snapshot);
}

function drawMap(snapshot) {
  const canvas=$('map'), box=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
  if (canvas.width!==Math.round(box.width*dpr)||canvas.height!==Math.round(box.height*dpr)) {
    canvas.width=Math.round(box.width*dpr);canvas.height=Math.round(box.height*dpr);
  }
  const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,box.width,box.height);
  if (!snapshot) return;
  const map=snapshot.map, scale=Math.min((box.width-44)/map.width,(box.height-36)/map.height);
  const ox=(box.width-map.width*scale)/2, oy=(box.height-map.height*scale)/2;
  const xy=(x,y)=>[ox+x*scale,box.height-oy-y*scale];
  for(let y=0;y<map.height;y++)for(let x=0;x<map.width;x++){
    const [px,py]=xy(x,y+1); const cell=map.grid[y][x];
    c.fillStyle=cell===1?'#253449':'#172536';c.fillRect(px+1,py+1,scale-2,scale-2);
    if(cell===1){c.fillStyle='#30425a';c.fillRect(px+4,py+scale*.3,scale-8,2);c.fillRect(px+4,py+scale*.7,scale-8,2);}
    if(cell===3){c.strokeStyle='#69e7bc';c.lineWidth=1.5;c.beginPath();c.arc(px+scale/2,py+scale/2,scale*.24,0,Math.PI*2);c.stroke();}
  }
  for(const t of snapshot.tasks){for(const [key,color,label] of [['pick','#ffca79','P'],['drop','#82b9ff','D']]){
    const [x,y]=xy(t[key][0]+.5,t[key][1]+.5);c.fillStyle=color;c.globalAlpha=.18;c.fillRect(x-scale*.31,y-scale*.31,scale*.62,scale*.62);c.globalAlpha=1;
    c.font=`600 ${Math.max(10,scale*.21)}px sans-serif`;c.textAlign='center';c.textBaseline='middle';c.fillText(label,x,y);
  }}
  snapshot.world.robots.forEach((r,i)=>{
    const [x,y]=xy(r.x/snapshot.cell_m,r.y/snapshot.cell_m);const n=snapshot.nodes.find(n=>n.id===r.id);
    c.save();c.translate(x,y);c.rotate(-r.th);c.fillStyle=colors[i%colors.length];c.shadowColor=colors[i%colors.length];c.shadowBlur=8;
    c.beginPath();c.roundRect(-scale*.24,-scale*.21,scale*.48,scale*.42,scale*.09);c.fill();c.shadowBlur=0;c.fillStyle='#0c1a26';c.fillRect(-scale*.12,-scale*.15,scale*.23,scale*.3);
    c.fillStyle='white';c.beginPath();c.moveTo(scale*.3,0);c.lineTo(scale*.17,-scale*.08);c.lineTo(scale*.17,scale*.08);c.fill();c.restore();
    c.strokeStyle=n.sensor_cut?'#ffca79':colors[i%colors.length];c.lineWidth=1.5;c.beginPath();c.arc(x,y,scale*.34,0,Math.PI*2);c.stroke();
    c.font='600 10px sans-serif';c.textAlign='center';c.fillStyle=colors[i%colors.length];c.fillText(r.id,x,y-scale*.47);
  });
  for (const person of snapshot.world.humans || []) {
    const [x,y]=xy(person.x/snapshot.cell_m,person.y/snapshot.cell_m);
    c.fillStyle='#ffca79';c.beginPath();c.arc(x,y,scale*.22,0,Math.PI*2);c.fill();
    c.font='600 10px sans-serif';c.fillText(person.id,x,y-scale*.3);
  }
  for (const obstacle of snapshot.world.obstacles || []) {
    const [x,y]=xy(obstacle.x/snapshot.cell_m,obstacle.y/snapshot.cell_m);
    c.fillStyle='#ef827e';c.fillRect(x-scale*.25,y-scale*.25,scale*.5,scale*.5);
  }
}
window.addEventListener('resize',()=>{ if (viewMode === '2d') drawMap(latest?.snapshot); });
async function poll(){
  try {latest=await api('status');connected=true;render();}
  catch {connected=false;render();}
  setTimeout(poll,150);
}
render();poll();
