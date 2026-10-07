const $ = (s) => document.querySelector(s);
const state = { dates: [], index: 0, timer: null, riskRange: 90, rainRange: 30 };

async function getJSON(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(`${r.status} ${url}`);return r.json();}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function num(v,d=2){return v==null||Number.isNaN(Number(v))?'—':Number(v).toFixed(d);}
function severity(score){if(score==null)return'NO DATA';if(score>=.18)return'CRITICAL';if(score>=.10)return'HIGH';return'LOW';}
function safe(fn){try{fn()}catch(e){console.error(e)}}

function renderPipeline(stages){const el=$('#pipeline');el.innerHTML=(stages||[]).map(s=>`<div class="stage"><div class="stage-name">${esc(s.name)}</div><div class="stage-state ${String(s.state||'').toLowerCase()}">${esc(s.state)}</div></div>`).join('');}
function renderRisk(r){
  const score=r?.risk_score==null?null:Number(r.risk_score), sev=r?.severity||severity(score);
  $('#risk-score').textContent=score==null?'—':`${(score*100).toFixed(1)}%`;
  const se=$('#risk-severity');se.textContent=sev;se.style.color=sev==='CRITICAL'?'var(--red)':sev==='HIGH'?'var(--orange)':sev==='LOW'?'var(--green)':'var(--muted)';
  $('#risk-date').textContent=r?.date?`as of ${r.date}`:'no observation';
  $('#risk-source').textContent=r?.model_mode||'XGBoost';
  $('#risk-fill').style.width=`${Math.max(0,Math.min(100,(score||0)*100))}%`;
  const active=!!r?.trigger_active,count=Number(r?.consecutive_high_count||0);
  const ts=$('#trigger-state');ts.textContent=active?'ACTIVE':'CLEAR';ts.classList.toggle('active',active);
  $('#streak').textContent=`${count} / 3 days`;
  $('#trigger-reason').textContent=r?.trigger_reason||'No active persistence condition.';
  $('#asof').textContent=r?.date?`Data as of ${r.date}`:'Data as of —';
}
function renderStations(rows){
  $('#stations').innerHTML=(rows||[]).length?(rows.map(r=>`<div class="station-row"><div class="row-main"><span class="row-title">${esc(r.station_name||r.station_id)}</span><span class="row-value">${r.available?num(r.rainfall_value,2):'—'}</span></div><div class="row-sub">${r.date?`observed ${esc(r.date)}`:'no observation available'}</div></div>`).join('')):'<div class="empty">No station observations available.</div>';
  $('#station-date').textContent=(rows||[]).find(x=>x.date)?.date||'—';
}
function renderReservoirs(rows){
  $('#reservoirs').innerHTML=(rows||[]).length?(rows.map(r=>`<div class="res-row"><div class="row-main"><span class="row-title">${esc(r.name||r.reservoir_id)}</span><span class="row-sub">${esc(r.date||'')} ${esc(r.time||'')}</span></div><div class="res-grid"><div class="kv"><span>Water level</span><b>${num(r.waterlevel,3)}</b></div><div class="kv"><span>Storage</span><b>${num(r.storage,3)}</b></div><div class="kv"><span>Inflow</span><b>${num(r.inflow_total,3)}</b></div><div class="kv"><span>Outflow</span><b>${num(r.outflow_total,3)}</b></div></div></div>`).join('')):'<div class="empty">No reservoir observations available.</div>';
  $('#reservoir-date').textContent=(rows||[])[0]?.date||'—';
}
function lineChart(el,labels,values,opts={}){
  const W=760,H=235,P={l:46,r:14,t:15,b:28}; const a=(values||[]).map(v=>v==null?null:Number(v)); const valid=a.filter(v=>Number.isFinite(v));
  if(!valid.length){el.innerHTML='<div class="empty">No chart data available.</div>';return;}
  let min=opts.min??Math.min(...valid),max=opts.max??Math.max(...valid);if(min===max){min-=1;max+=1;}
  const x=i=>P.l+(i/Math.max(1,a.length-1))*(W-P.l-P.r), y=v=>H-P.b-((v-min)/(max-min))*(H-P.t-P.b);
  let path='',pen=false; a.forEach((v,i)=>{if(!Number.isFinite(v)){pen=false;return;}path+=`${pen?'L':'M'} ${x(i).toFixed(1)} ${y(v).toFixed(1)} `;pen=true;});
  const grid=[0,.25,.5,.75,1].map(t=>{const yy=P.t+t*(H-P.t-P.b),value=max-t*(max-min);return`<line x1="${P.l}" x2="${W-P.r}" y1="${yy}" y2="${yy}" stroke="#213140"/><text x="${P.l-8}" y="${yy+3}" text-anchor="end" fill="#70869a" font-size="9">${opts.percent?(value*100).toFixed(0)+'%':value.toFixed(1)}</text>`}).join('');
  const pts=a.map((v,i)=>Number.isFinite(v)?`<circle cx="${x(i)}" cy="${y(v)}" r="2.3" fill="${opts.point||'#55a7e8'}"/>`:'').join('');
  const inds=[0,Math.floor((labels.length-1)/2),labels.length-1].filter((v,i,a)=>a.indexOf(v)===i&&v>=0);const xl=inds.map(i=>`<text x="${x(i)}" y="${H-7}" text-anchor="middle" fill="#70869a" font-size="9">${esc(labels[i]||'')}</text>`).join('');
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.aria||'line chart')}">${grid}<path d="${path}" fill="none" stroke="${opts.stroke||'#55a7e8'}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>${pts}${xl}</svg>`;
}
async function loadRiskChart(){try{const d=await getJSON(`/api/risk-history?days=${state.riskRange}`);lineChart($('#risk-chart'),d.dates,d.scores,{min:0,max:1,percent:true,stroke:'#e6b65c',point:'#e6b65c',aria:'research risk history'});}catch(e){$('#risk-chart').innerHTML='<div class="empty">Risk history unavailable.</div>';console.error(e);}}
async function loadRainChart(){try{const d=await getJSON(`/api/rainfall?days=${state.rainRange}`);lineChart($('#rain-chart'),d.dates,d.values,{stroke:'#55a7e8',point:'#55d5d0',aria:'station aggregate rainfall'});}catch(e){$('#rain-chart').innerHTML='<div class="empty">Rainfall history unavailable.</div>';console.error(e);}}
async function loadReplayList(){const d=await getJSON('/api/replay');state.dates=d.dates||[];$('#slider').max=Math.max(0,state.dates.length-1);return d;}
async function loadReplay(date){try{const d=await getJSON(`/api/replay?date=${encodeURIComponent(date)}`);state.index=Math.max(0,state.dates.indexOf(d.date));$('#slider').value=state.index;$('#replay-date').textContent=d.date||'—';renderRisk(d);renderStations(d.stations||[]);renderReservoirs(d.reservoirs||[]);}catch(e){console.error(e);}}
async function loadPolicy(){try{const d=await getJSON('/api/policy-status');$('#policy-chip').textContent=d.corpus_state||'LOCAL';$('#policy-body').innerHTML=(d.documents||[]).length?d.documents.map(x=>`<div class="doc"><div><b>${esc(x.name)}</b><small>${esc(x.category||'document')}</small></div><span class="state-ready">AVAILABLE</span></div>`).join('')+`<div class="micro">${esc(d.notice||'Local policy corpus status.')}</div>`:'<div class="empty">Policy documents not found.</div>';}catch(e){$('#policy-body').innerHTML='<div class="empty">Policy service unavailable.</div>';}}
async function loadSystem(){try{const d=await getJSON('/api/system-status');$('#system-body').innerHTML=(d.stages||[]).map(s=>`<div class="health-row"><span>${esc(s.name)}</span><span class="state-${String(s.state||'').toLowerCase()}">${esc(s.state)}</span></div>`).join('');}catch(e){$('#system-body').innerHTML='<div class="empty">System status unavailable.</div>';}}
async function loadEvidence(){try{const e=await getJSON('/api/summary');$('#evidence-body').innerHTML=`<div class="evidence-row"><span>Active stations</span><b>${e.station_count??'—'}</b></div><div class="evidence-row"><span>Processed rainfall rows</span><b>${e.rainfall_rows??'—'}</b></div><div class="evidence-row"><span>Rainfall window</span><b>${esc(e.rainfall_first_date||'—')} → ${esc(e.rainfall_last_date||'—')}</b></div><div class="evidence-row"><span>Reservoir records</span><b>${e.reservoir_rows??'—'}</b></div>`;}catch(e){$('#evidence-body').innerHTML='<div class="empty">Summary unavailable.</div>';}}
function drawMap(){const names=['Anna University','Taramani','Velachery W178','NIOT Pallikaranai'],coords=[[24,27],[68,22],[50,70],[76,68]];$('#station-map').innerHTML=names.map((n,i)=>`<div class="map-node" style="left:${coords[i][0]}%;top:${coords[i][1]}%"></div><div class="map-label" style="left:${coords[i][0]}%;top:${coords[i][1]}%">${esc(n)}</div>`).join('');}
function step(delta){if(!state.dates.length)return;state.index=Math.max(0,Math.min(state.dates.length-1,state.index+delta));loadReplay(state.dates[state.index]);}
function stopPlay(){if(state.timer){clearInterval(state.timer);state.timer=null;}$('#play').textContent='▶ Play';}
function startPlay(){if(state.timer)return;$('#play').textContent='❚❚ Pause';state.timer=setInterval(()=>{if(state.index>=state.dates.length-1){stopPlay();return;}step(1);},350);}
async function jumpFirstTrigger(){try{const d=await getJSON('/api/risk-history?days=2000');const i=(d.trigger_active||[]).findIndex(Boolean);if(i<0){alert('No recorded trigger-active day is present in the current history.');return;}const date=d.dates[i];await loadReplay(date);}catch(e){console.error(e);}}

async function init(){
  drawMap();
  try{const s=await getJSON('/api/system-status');renderPipeline(s.stages||[]);}catch(e){console.error(e);}
  try{const r=await getJSON('/api/current-risk');renderRisk(r);}catch(e){renderRisk({risk_score:null,severity:'NO DATA'});}
  await loadReplayList();
  if(state.dates.length){state.index=state.dates.length-1;$('#slider').value=state.index;await loadReplay(state.dates[state.index]);}
  await Promise.all([loadRiskChart(),loadRainChart(),loadPolicy(),loadSystem(),loadEvidence()]);
  try{const t=await getJSON('/api/current-risk');const cfg=t?.trigger_threshold;$('#trigger-config').textContent='Persistence: 3 consecutive observed days · threshold configured in trigger_config.json';}catch{}
}

document.querySelectorAll('#risk-range button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('#risk-range button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.riskRange=Number(b.dataset.range);loadRiskChart();}));
document.querySelectorAll('#rain-range button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('#rain-range button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.rainRange=Number(b.dataset.range);loadRainChart();}));
$('#prev').onclick=()=>step(-1);$('#next').onclick=()=>step(1);$('#play').onclick=()=>state.timer?stopPlay():startPlay();$('#latest').onclick=()=>{if(state.dates.length){state.index=state.dates.length-1;$('#slider').value=state.index;loadReplay(state.dates[state.index]);}};$('#slider').oninput=e=>{state.index=Number(e.target.value);if(state.dates[state.index])loadReplay(state.dates[state.index]);};$('#demo-event').onclick=jumpFirstTrigger;
init();
