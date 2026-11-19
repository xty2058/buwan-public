'use strict';
// Internal recall inspector (roadmap 1.13). Read-only; every API call is scoped to the signed-in user.
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const KIND={user_fact:'用户事实',boundary:'边界',promise:'约定',experience:'共同经历',relationship:'关系'};
const STATUS={confirmed:'有效',candidate:'候选',conflicted:'冲突',deleted:'已删除',invalidated:'已失效',rejected:'已拒绝',missing:'已不存在'};
async function api(path){const r=await fetch(path,{credentials:'same-origin'});const v=await r.json();if(!r.ok)throw Error(v.error?.message||'请求失败');return v}
const ms=v=>v==null?'—':(v>=1000?(v/1000).toFixed(2)+' s':v+' ms');
const when=t=>t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'—';
function ago(t){if(!t)return'';const d=Math.floor((Date.now()/1000-t)/86400);return d<1?'今天':'约 '+d+' 天前'}
function metrics(items){
  if(!items.length)return '<p class="empty">还没有带耗时记录的完成调用（迁移 010 之前的调用不计）。</p>';
  return '<div class="scroll"><table><thead><tr><th>模型</th><th>角色</th><th>完成调用</th><th>首字 p50 / p90</th><th>总耗时 p50 / p90</th><th>缓存命中率</th></tr></thead><tbody>'+
    items.map(m=>`<tr><td>${esc(m.model)}</td><td>${esc(m.character_id)}</td><td>${m.calls}（已测 ${m.measured}）</td><td>${ms(m.first_token_ms.p50)} / ${ms(m.first_token_ms.p90)}</td><td>${ms(m.duration_ms.p50)} / ${ms(m.duration_ms.p90)}</td><td>${m.cache_hit_rate==null?'未上报':(m.cache_hit_rate*100).toFixed(1)+'%'}</td></tr>`).join('')+'</tbody></table></div>';
}
function memory(m){
  const gone=m.content==null;
  return `<div class="memory${gone?' gone':''}"><div class="meta">${esc(KIND[m.kind]||m.kind)} · ${m.fact_scope==='story'?'故事内':'现实'} · 相似度 ${esc(m.score)} · 来源 ${esc(when(m.source_time))}（${esc(ago(m.source_time))}）· 现状 ${esc(STATUS[m.status_now]||m.status_now)}</div>`+
    (gone?'<div>内容已随删除或失效隐藏</div>':`<div>${esc(m.content)}</div>`+m.quotes.map(q=>`<div class="quote">原话：「${esc(q)}」</div>`).join(''))+'</div>';
}
function turn(t){
  const recall=t.recalled==null?'<p class="empty">本轮未经过召回步骤</p>':(t.recalled.length?t.recalled.map(memory).join(''):'<p class="empty">召回执行了，但没有命中有效记忆</p>');
  const c=t.call;
  return `<div class="turn"><div class="meta">${esc(when(t.created_at))} · ${t.kind==='regenerate'?'重新生成':'回复'} · ${esc(t.status)} · 检索 ${esc(t.retrieval_mode||'—')} · 首字 ${ms(c.first_token_ms)} · 总耗时 ${ms(c.duration_ms)} · 输入 ${esc(c.input_tokens??'—')}（缓存 ${esc(c.cached_input_tokens??'未上报')}）</div>`+
    `<div class="said"><b>用户</b>${esc(t.user_text)}</div><div class="said"><b>回复</b>${esc(t.reply_text||'（无）')}</div>${recall}</div>`;
}
async function load(cid){$('#turns').innerHTML='<p class="empty">读取中…</p>';
  try{const v=await api('/api/conversations/'+encodeURIComponent(cid)+'/recalls?limit=100');$('#turns').innerHTML=v.items.length?v.items.map(turn).join(''):'<p class="empty">这个会话还没有生成记录。</p>'}
  catch(e){$('#turns').innerHTML='<p class="empty">'+esc(e.message)+'</p>'}}
(async()=>{
  try{
    const s=await api('/api/auth/session');
    if(!s.user){$('#notice').textContent='请先在未晚主页登录，再回到此页。';return}
    $('#notice').textContent='';
    const [m,list]=await Promise.all([api('/api/inspection/metrics'),api('/api/conversations')]);
    $('#metrics').innerHTML=metrics(m.items);
    const sel=$('#conversation');
    sel.innerHTML=list.items.map(c=>`<option value="${esc(c.id)}">${esc(c.character?.name||c.character_id)} · ${esc(when(c.updated_at))}</option>`).join('');
    sel.onchange=()=>load(sel.value);
    if(list.items.length)load(list.items[0].id);else $('#turns').innerHTML='<p class="empty">还没有会话。</p>';
  }catch(e){$('#notice').textContent=e.message}
})();
