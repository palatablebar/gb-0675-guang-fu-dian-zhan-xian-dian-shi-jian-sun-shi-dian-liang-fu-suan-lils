const feedback=document.querySelector('#switch-feedback');
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function load(){
  const data=await (await fetch('/api/boundary')).json();
  document.querySelector('#phase-tag').textContent='边界阶段：'+data.phase+(data.fingerprint_changed?' · 规则已变化待重判':'');
  document.querySelector('#fingerprint-tag').textContent='规则指纹 '+data.boundary.fingerprint+(data.fingerprint_changed?'（已漂移）':'');
  document.querySelector('#phase-tag').style.color=data.fingerprint_changed?'var(--bad)':'var(--accent)';
  document.querySelector('#source-rows').innerHTML=data.sources.map(s=>{
    const blockers=s.blockers.length?s.blockers.map(b=>`<div><b style="color:var(--bad)">${esc(b.code)}</b> ${esc(b.detail)}</div>`).join(''):(s.exitable?'<span style="color:var(--ok)">无阻断，可退出</span>':'—');
    const color=s.exitable?'var(--ok)':(s.role==='legacy'?'var(--warn)':'var(--info)');
    return `<tr><td style="text-align:left">${esc(s.name)}<br><small style="color:var(--ink-muted)">${esc(s.source_id)}</small></td><td>${esc(s.lane)}</td><td>${esc(s.encoding)}</td><td style="text-align:left"><b style="color:${color}">${esc(s.status)}</b></td><td style="text-align:left">${blockers}</td></tr>`;
  }).join('');
  const e=data.evidence;
  document.querySelector('#evidence').innerHTML=[
    ['规则指纹一致',!data.fingerprint_changed],
    ['旧批待复核数量 = '+e.open_reviews,e.open_reviews===0],
    ['历史旧批总数 '+e.legacy_total+'，重放漂移 = '+e.replay_drift,e.replay_drift===0],
    ['旧采集口自测',e.legacy_self_check],
    ['新采集口自测',e.v2_self_check],
  ].map(([label,ok])=>`<div><span style="color:${ok?'var(--ok)':'var(--bad)'}">${ok?'✔ 满足':'✘ 阻断'}</span> · ${esc(label)}</div>`).join('')
    + e.drift_detail.map(d=>`<div style="color:var(--bad)">漂移批次 ${esc(d.run_id)}：${esc(d.reason)}</div>`).join('');
  document.querySelector('#history-rows').innerHTML=data.history.length?data.history.map(h=>
    `<tr><td>v${h.version}</td><td style="text-align:left">${esc(h.reason)}</td><td>${esc(h.created_at)}</td></tr>`).join('')
    :'<tr><td colspan="3" style="text-align:left">暂无变更，当前为初始双轨边界。</td></tr>';
  document.querySelector('#btn-cutover').disabled=data.phase==='cutover';
  document.querySelector('#btn-dual').disabled=data.phase==='dual';
}
async function switchPhase(phase){
  feedback.textContent='';
  const reason=document.querySelector('#switch-reason').value.trim();
  if(!reason){feedback.textContent='请填写切换原因以便留痕';return;}
  const res=await fetch('/api/boundary/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase,reason})});
  const body=await res.json();
  if(res.ok){feedback.style.color='var(--ok)';feedback.textContent='已切换至 '+body.phase+'，边界版本 v'+body.boundary.version;}
  else{feedback.style.color='var(--bad)';feedback.textContent='切换被阻断，已保持旧版边界：'+body.blockers.map(b=>b.code).join('、');}
  await load();
}
async function rollback(){
  feedback.textContent='';
  const reason=document.querySelector('#switch-reason').value.trim()||'手动回退';
  const res=await fetch('/api/boundary/rollback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});
  if(res.ok){const b=await res.json();feedback.style.color='var(--ok)';feedback.textContent='已回滚到边界版本 v'+b.boundary.version;}
  else{const b=await res.json();feedback.style.color='var(--bad)';feedback.textContent=b.detail;}
  await load();
}
document.querySelector('#btn-cutover').addEventListener('click',()=>switchPhase('cutover'));
document.querySelector('#btn-dual').addEventListener('click',()=>switchPhase('dual'));
document.querySelector('#btn-rollback').addEventListener('click',rollback);
load();
