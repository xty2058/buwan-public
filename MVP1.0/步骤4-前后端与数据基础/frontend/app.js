'use strict';
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {user:null,csrf:null,characters:[],conversations:[],conversation:null,messages:[],memory:[],relationship:null,activity:null,activityList:null,activityId:null,archive:null,archiveError:null,
 latestUser:null,hasNewer:false,replyRequired:false,localRecord:{},restoreView:null,cacheWarning:false,capabilities:{},proactive:null,branches:null,replyOptions:null,voice:'idle',generation:null,stopping:false,streamError:null,page:'discover',role:'linyu',cid:null,loading:true,error:null,authMode:'login',busy:false,requestId:null,nextBefore:null};
let epoch=0, pendingController=null, toastTimer, retryMessage=null;
let streamController=null,streamSerial=0,reconcileTimer=null;
let composingCid=null,delayedChatRender=false;
let requestSequence=0;
function newRequestKey(){
 // Idempotency keys are request labels, not credentials. LAN HTTP may lack randomUUID.
 const source=typeof crypto!=='undefined'?crypto:null;
 if(typeof source?.randomUUID==='function')return source.randomUUID().replaceAll('-','');
 if(typeof source?.getRandomValues==='function'){
  const bytes=new Uint8Array(16);source.getRandomValues(bytes);
  return Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');
 }
 return 'req_'+Date.now().toString(36)+'_'+(++requestSequence).toString(36)+'_'+Math.random().toString(36).slice(2,14);
}
const drafts=new Map();
const pendingMessages=new Map();
let returnTo=null;
const usesInDocument=new Set();
const documentIsReload=typeof performance!=='undefined'&&performance.getEntriesByType?.('navigation')?.[0]?.type==='reload';
try { returnTo=JSON.parse(sessionStorage.getItem('weiwan-return')||'null'); } catch { /* Optional browser storage. */ }
const storage={get(key){try{return sessionStorage.getItem(key)}catch{return null}},set(key,val){try{sessionStorage.setItem(key,val)}catch{}},delete(key){try{sessionStorage.removeItem(key)}catch{}}};
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3000)}
class RequestError extends Error{constructor(status,data){super(data.error?.message||'请求失败，请稍后重试');this.status=status;this.code=data.error?.code;this.requestId=data.request_id;}}
async function api(path,{method='GET',body,signal,report=true}={}){
 let response,data;
 if(method==='POST'&&body&&/^\/api\/conversations\/[a-f0-9]{32}\/(messages|generations|relationships)(?:\/|$)/.test(path)){const cid=path.split('/')[3],use=storage.get('weiwan-use:'+cid);if(use&&!body.use_id)body={...body,use_id:use};}
 try {response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',signal,
  headers:{...(method!=='GET'?{'Content-Type':'application/json','X-CSRF-Token':state.csrf||''}:{})},
  ...(body!==undefined?{body:JSON.stringify(body)}:{})});
  data=await response.json();
 } catch(error){if(error.name==='AbortError')throw error;const err=new Error('无法连接开发服务，请检查服务是否运行。');err.network=true;throw err;}
 state.requestId=response.headers.get('X-Request-ID');
 if(!response.ok){const error=new RequestError(response.status,data);if(response.status>=500&&report)reportError('api_failure');throw error}return data;
}
function reportError(code){if(!state.user||!state.csrf)return;fetch('/api/client-errors',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify({code,component:['discover','detail','messages','chat','memory','relationship','me','auth'].includes(state.page)?state.page:'boot'})}).catch(()=>{});}
window.addEventListener('error',()=>reportError('window_error'));
window.addEventListener('unhandledrejection',()=>reportError('unhandled_rejection'));
// Character art and icons come from the 不晚 visual spec (design/buwan-ds), served under /buwan/.
function portraitPath(role){return ({linyu:'/buwan/assets/characters/linyu.jpg',luye:'/buwan/assets/characters/luye.jpg',shenyan:'/buwan/assets/characters/shenyan.jpg'})[role]||null;}
const icon=(name,cls='')=>`<svg class="icon ${cls}" aria-hidden="true"><use href="/buwan/assets/icons.svg#${name}"/></svg>`;
function avatar(role){const r=state.characters.find(x=>x.id===role)||{},src=portraitPath(role);return `<span class="avatar ${role==='luye'?'luye':role==='shenyan'?'shenyan':''}">${src?`<img src="${src}" alt="${esc(r.name||'角色')}头像" loading="lazy">`:esc((r.name||'晚').slice(0,1))}</span>`}
function nav(current=state.page){return `<nav class="bottom-nav" aria-label="主导航">${[['discover','compass','发现'],['messages','chat','消息'],['me','user','我的']].map(([p,i,n])=>`<button data-action="navigate" data-value="${p}" class="${current===p?'selected':''}" ${current===p?'aria-current="page"':''}>${icon(i)}${n}</button>`).join('')}</nav>`;}
function header(title,back='discover',extra=''){return `<header><button class="back" aria-label="返回" data-action="navigate" data-value="${back}">${icon('chevron-left')}</button><h3>${esc(title)}</h3>${extra||'<span></span>'}</header>`;}
function empty(title,text,button=''){return `<div class="empty"><div class="symbol" aria-hidden="true"></div><h3>${title}</h3><p>${text}</p>${button}</div>`;}
const button=(label,action,value='',cls='primary')=>`<button type="button" class="${cls}" ${action==='stop-generation'?'aria-label="停止生成" title="停止生成"':''} data-action="${action}" data-value="${esc(value)}">${label}</button>`;
function render(){
 if(composingCid!==null&&state.page==='chat'&&composingCid===state.cid){delayedChatRender=true;return;}
 const previousInput=$('#draft'),preserveInput=state.page==='chat'&&previousInput?.dataset?.conversation===state.cid&&document.activeElement===previousInput,selection=preserveInput?[previousInput.selectionStart,previousInput.selectionEnd]:null;
 const role=state.characters.find(x=>x.id===state.role),c=state.conversation;
 let html='';
 if(state.loading)html=`${header('正在加载',state.cid?'messages':'discover')}<div class="scroll pad"><div class="skeleton"></div><div class="skeleton"></div></div>`;
 else if(state.error)html=`${header('暂时未能加载',state.cid?'messages':'discover')}<div class="pad"><div class="error">${esc(state.error.message)}${state.error.requestId?`<br>错误编号：${esc(state.error.requestId)}`:''}</div>${button('重试加载','reload')}</div>`;
 else if(state.page==='discover')html=discoverHTML();
 else if(state.page==='detail'&&role){const previous=state.conversations.find(x=>x.character_id===role.id);const portrait=portraitPath(role.id);html=`<div class="detail-stage"><header class="topbar portrait-top"><button class="back" aria-label="返回遇见" data-action="navigate" data-value="discover">${icon('chevron-left')}</button><span class="pill">AI 角色</span></header><div class="scroll"><div class="portrait-header">${portrait?`<img src="${portrait}" alt="${esc(role.name)}人物立绘" decoding="async">`:''}<div class="portrait-caption"><h1>${esc(role.name)}</h1><p>${role.age} 岁 · ${esc(role.job)}</p></div></div><div class="pad"><div class="tags">${role.tags.map(t=>`<span class="pill light">${esc(t)}</span>`).join('')}</div><p class="body-copy">${esc(role.bio)}</p><div class="section-title"><h2>你们如何认识</h2>${!previous?button('调整','edit-scene','','text-button'):''}</div><div class="scene-edit"><p>${esc(sceneDrafts[role.id]||role.initial_scene)}</p></div><p class="muted stage-label">首次开始将采用这个背景。你可以调整，角色不会据此替你决定后续动作。</p>${previous&&previous.version!==role.version?'<div class="version">已有对话保留原设定，不会自动更换版本。</div>':''}<details class="fold"><summary>开场预览<span class="chev">⌄</span></summary><p class="body-copy prewrap">${esc(role.opening)}</p></details><div class="notice">AI 角色 · 人物插画 / 表达 v${esc(role.version)}<br>已有对话保留原角色版本。</div></div></div><div class="fixed-action">${button(previous?'继续聊天 →':'从这个场景开始聊天 →','start',role.id,'primary wide')}</div></div>`;}
 else if(state.page==='auth')html=authHTML();
 else if(state.page==='messages')html=`<header><h2>消息</h2><span class="head-label">记录都在这里</span></header><div class="scroll pad">${!state.user?empty('登录后找回你的对话','同一账号在同一开发服务上恢复记录。',button('去登录','login')):state.conversations.length?state.conversations.map(x=>`<button class="conversation" data-action="chat" data-value="${x.id}">${avatar(x.character_id)}<div class="content"><div class="role-line"><h3>${esc(x.character.name)}</h3><small>v${x.version}</small></div><p>${esc(x.preview)}</p>${conversationStateLabel(x.generation_status)}</div><span>›</span></button>`).join(''):empty('故事尚未开始','去认识一个想聊天的人吧。',button('去发现','navigate','discover','secondary'))}</div>${nav()}`;
 else if(state.page==='chat'&&c)html=chatHTML(c);
 else if(state.page==='activity'&&c)html=activityHTML(c);
 else if(state.page==='memory'&&c)html=memoryHTML(c);
 else if(state.page==='relationship'&&c)html=relationshipHTML(c);
 else if(state.page==='me')html=`<header><h2>我的</h2><span class="head-label">留一点空间给自己</span></header><div class="scroll pad"><div class="section"><div class="eyebrow">${state.user?'YOUR ACCOUNT':'WELCOME'}</div><h1>${esc(state.user?.nickname||'尚未登录')}</h1><small>${esc(state.user?.username||'可先浏览三个角色')}</small></div>${state.user?`<button class="settings-row" data-action="nickname">修改昵称 <span>›</span></button>`:button('登录 / 注册','login','','primary wide')}<a class="settings-row" href="/evaluation">林屿 · 模型回复评测 <span>↗</span></a><button class="settings-row" data-action="privacy">账号与记录说明 <span>›</span></button>${state.user?'<button class="settings-row danger" data-action="logout">退出登录 <span>↗</span></button>':''}<p class="stage-label">本地开发版 · 真实账号、真实数据库<br>当前未提供密码找回或外部身份登录。</p></div>${nav()}`;
 $('#app').innerHTML=html;renderInspector();
 if(state.page==='discover'&&!state.loading&&!state.error)wireHomeFeed();
 if(state.page==='chat'&&!state.loading&&!state.error&&state.autoSend===state.cid){state.autoSend=null;setTimeout(()=>{if(state.page==='chat'&&$('#draft')?.value.trim())saveMessage();},0);}
 if(state.page==='chat'&&!state.loading&&!state.error){wireChatView();if(preserveInput&&$('#draft')){$('#draft').focus({preventScroll:true});$('#draft').setSelectionRange(...selection);}}
}
const sceneDrafts={};
// ---- Home feed (不晚规范 T5 沉浸全屏 + Spotlight): one character per screen, swipe up/down, chat in place ----
// A first line typed on the home page before signing in is kept here until the conversation exists.
function homeDraft(){try{return JSON.parse(storage.get('weiwan-home-draft')||'null')}catch{return null}}
function immersiveCid(){return storage.get('weiwan-immersive')||null;}
function setImmersive(cid){if(cid)storage.set('weiwan-immersive',cid);else storage.delete('weiwan-immersive');}
function spotlightHTML(r){
 const existing=state.conversations.find(x=>x.character_id===r.id),portrait=portraitPath(r.id),off=r.available===false,draft=homeDraft();
 const panel=existing
  ?`<button class="opening-preview" data-action="home-continue" data-value="${r.id}" aria-label="继续和${esc(r.name)}的对话"><span class="label">上次聊到</span><p>${esc(existing.preview||'')}</p></button>`
  :`<div class="opening-preview"><span class="label">初次相遇 · ${esc(r.place||'')}</span><p>${esc(r.opening)}</p></div>`;
 return `<article class="spotlight" data-role="${r.id}" aria-labelledby="home-name-${r.id}">${portrait?`<img class="backdrop" src="${portrait}" alt="${esc(r.name)}人物立绘" decoding="async" ${r.id===state.role?'':'loading="lazy"'}>`:''}<div class="shade"></div><div class="spotlight-content">${panel}<div class="spotlight-intro"><div class="home-head"><h2 class="home-name" id="home-name-${r.id}">${esc(r.name)}</h2><span class="home-job">${r.age} · ${esc(r.job)}</span><button class="icon-btn glass-button" data-action="detail" data-value="${r.id}" aria-label="查看${esc(r.name)}的人物档案" title="人物档案">${icon('chevron-right')}</button></div><div class="tags"><span class="pill">AI 角色</span>${(r.tags||[]).map(t=>`<span class="pill">${esc(t)}</span>`).join('')}</div></div>${off?'<p class="home-job">这个角色还没有开放，敬请期待。</p>':`<form class="starter" data-role="${r.id}"><label class="sr-only" for="say-${r.id}">对${esc(r.name)}说</label><input class="starter-input" id="say-${r.id}" name="say" maxlength="1000" autocomplete="off" enterkeyhint="send" placeholder="${existing?'接着说…':'对'+esc(r.name)+'说点什么…'}" value="${draft?.role===r.id?esc(draft.text):''}"><button class="send" aria-label="发送，${existing?'继续':'开始'}和${esc(r.name)}聊">${icon('arrow-up')}</button></form>`}</div></article>`;
}
function discoverHTML(){
 if(!state.characters.length)return `<div class="home-stage"><header class="topbar home-top"><span class="wordmark">不晚</span></header><div class="scroll pad"><p class="home-description">角色还没有加载，稍后回来。</p></div>${nav()}</div>`;
 if(!state.characters.some(r=>r.id===state.role))state.role=state.characters[0].id;
 return `<div class="home-stage"><header class="topbar home-top"><span class="wordmark">不晚</span></header><main class="feed" id="home-feed" tabindex="0" aria-label="角色，上下滑动切换">${state.characters.map(spotlightHTML).join('')}</main>${nav()}</div>`;
}
// Keep the feed on the current character after a render, and follow the user's swipes without re-rendering.
function wireHomeFeed(){
 const feed=$('#home-feed');if(!feed)return;
 const cards=[...feed.querySelectorAll('.spotlight')],index=Math.max(0,cards.findIndex(c=>c.dataset.role===state.role));
 feed.scrollTop=index*feed.clientHeight;
 let frame=0;
 feed.addEventListener('scroll',()=>{if(frame)return;frame=requestAnimationFrame(()=>{frame=0;const card=cards[Math.min(cards.length-1,Math.max(0,Math.round(feed.scrollTop/Math.max(feed.clientHeight,1))))];if(card)state.role=card.dataset.role;});},{passive:true});
}
async function homeStart(form){
 const version=epoch,role=form.dataset.role,input=form.elements.say,text=input.value.trim(),send=form.querySelector('button');
 if(!text){input.focus();return;}
 if([...text].length>1000){toast('最多输入1000字，请缩短后发送。');return;}
 state.role=role;
 if(!state.user){storage.set('weiwan-home-draft',JSON.stringify({role,text}));setReturn({page:'discover',role});await navigate('auth');return;}
 send.disabled=true;
 try{const conv=await api('/api/conversations',{method:'POST',body:{character_id:role}});if(version!==epoch)return;
  storage.delete('weiwan-home-draft');setImmersive(conv.id);drafts.set(conv.id,text);state.autoSend=conv.id;await navigate('chat',conv.id);}
 catch(error){if(version!==epoch)return;send.disabled=false;if(error.status===401){clearPrivate();state.user=null;storage.set('weiwan-home-draft',JSON.stringify({role,text}));setReturn({page:'discover',role});await navigate('auth');return;}toast(error.message);}
}
function authHTML(){return `${header('登录不晚',returnTo?.page==='detail'?'detail':'discover')}<div class="scroll pad"><div class="login-mark">不晚</div>${state.capabilities.guest_trial===false?'<p class="body-text">当前为邀请制：请用邀请码注册，或登录已有账号。</p>':'<button data-action="guest" class="primary accent">免注册试用 · 直接与林屿聊天</button><p class="body-text">同一浏览器可继续测试记忆。退出试用、清除 Cookie 或凭证过期后无法找回此测试身份。</p>'}<h1 class="auth-line">让相遇有下文。</h1><p class="body-text">使用账号找回你的记录。<br>昵称不会自动告诉任何角色。</p><div class="auth-switch"><button data-action="auth-mode" data-value="login" class="${state.authMode==='login'?'selected':''}">登录</button><button data-action="auth-mode" data-value="register" class="${state.authMode==='register'?'selected':''}">注册账号</button></div><form id="auth-form"><label class="field">用户名<input name="username" autocomplete="username" pattern="[A-Za-z0-9_]{3,32}" minlength="3" maxlength="32" required placeholder="3—32 位字母、数字、下划线"></label><label class="field">密码<input name="password" type="password" minlength="${state.authMode==='register'?12:1}" maxlength="128" autocomplete="${state.authMode==='register'?'new-password':'current-password'}" required placeholder="${state.authMode==='register'?'12—128 个字符':'请输入密码'}"></label>${state.authMode==='register'?'<label class="field">昵称<input name="nickname" autocomplete="nickname" maxlength="16" required placeholder="1—16 个字"></label>'+(state.capabilities.invite_required?'<label class="field">邀请码<input name="invite_code" autocomplete="off" autocapitalize="characters" maxlength="40" required placeholder="向邀请你的人索取"></label>':''):''}<div id="auth-error"></div><button class="primary wide" ${state.busy?'disabled':''}>${state.busy?'处理中…':state.authMode==='login'?'登录并继续 →':'创建账号并继续 →'}</button></form><p class="stage-label">真实本地账号，不是演示身份。<br>请记住用户名与密码；本轮尚未实现密码找回。<br>${globalThis.location?.protocol==='https:'?'':'本地 HTTP 环境仅供开发测试。'}</p></div>`;}
function renderInspector(){const facts=[['后端状态','同源 API'],['账号归属',state.user?.username||'未登录'],['数据库','SQLite · 持久化'],['角色版本',state.conversation?'v'+state.conversation.version:'按角色当前版本'],['会话数量',state.user?state.conversations.length:'—'],['语音服务',state.capabilities.tts_configured?(state.capabilities.tts_provider||'已配置'):'待启用付费额度'],['角色模型',state.capabilities.model_status==='simulated'?'模拟服务 · 非真实模型':state.capabilities.model_label||'未配置真实模型']];$('#inspector').innerHTML=`<div class="eyebrow">DEVELOPMENT STATUS</div><h3>当前运行状态</h3>${facts.map(([k,v])=>`<div class="status-line"><span>${k}</span><b>${esc(v)}</b></div>`).join('')}<div class="control-group"><h3>MVP 1.1 路径</h3><p>分段共读、进度恢复、活动记忆确认、关系档案，以及点击后合成角色语音。</p></div><div class="control-group"><h3>验证边界</h3><p>服务已配置不代表模型质量或音色试听通过。真机播放、跨天续聊与持续体验需要各自的真实记录；模拟测试仅验证工程行为。</p></div><div class="inspector-foot"><small>最近请求编号</small><code>${esc(state.requestId||'—')}</code><p>用编号定位后端 JSON 错误日志；日志不记录密码、凭证或聊天正文。</p></div>`;}
function modal(title,body,actions=''){ $('#modal').innerHTML=`<section class="sheet" role="dialog" aria-modal="true" aria-label="${title}"><button class="close" data-action="close" aria-label="关闭">${icon('close')}</button><h2>${title}</h2>${body}${actions?`<div class="buttons">${actions}</div>`:''}</section>`;$('#modal input, #modal textarea, #modal button')?.focus();}
function closeModal(){$('#modal').innerHTML='';}
function setReturn(value){returnTo=value;if(value)storage.set('weiwan-return',JSON.stringify(value));else storage.delete('weiwan-return');}
function clearPrivate({preserveGuestScene=false}={}){stopVoice();state.branches=null;state.replyOptions=null;state.voice='idle';if(typeof voiceInput!=='undefined')voiceInput.stop(true);state.activity=null;state.activityList=null;state.activityId=null;state.archive=null;state.archiveError=null;activityDrafts.clear();activityPending.clear();try{Object.keys(sessionStorage).filter(k=>k.startsWith('weiwan-activity:')||k.startsWith('weiwan-archive:')).forEach(k=>storage.delete(k));}catch{}composingCid=null;delayedChatRender=false;if(typeof recoveryCache!=='undefined')recoveryCache.clear();state.latestUser=null;state.localRecord={};state.restoreView=null;stopObserving();state.generation=null;state.streamError=null;closeModal();if(!preserveGuestScene||state.user){for(const role of Object.keys(sceneDrafts))delete sceneDrafts[role];}drafts.clear();pendingMessages.clear();retryMessage=null;state.conversations=[];state.conversation=null;state.messages=[];state.useId=null;usesInDocument.clear();state.relationPending=null;state.relationSources=[];state.relationMemories=[];try{Object.keys(sessionStorage).filter(k=>k.startsWith('weiwan-rel-pending:')).forEach(k=>storage.delete(k));}catch{}state.memory=[];state.memoryInfo=null;state.editingMemory=null;state.relationship=null;state.csrf=null;try{Object.keys(sessionStorage).filter(k=>k.startsWith('weiwan-use:')).forEach(k=>storage.delete(k));}catch{}}
function protect(){if(state.user)return true;setReturn({page:state.page,cid:state.cid,role:state.role});state.page='auth';state.loading=false;render();return false;}
function encodeRoute(page,cid=state.cid,role=state.role){return '#/'+page+(['chat','memory','relationship','activity'].includes(page)?'/'+cid+(page==='activity'&&state.activityId?'/'+state.activityId:''):page==='detail'?'/'+role:'');}
async function navigate(page,id){
 saveReadingPosition();
 if(['chat','memory','relationship','activity'].includes(state.page)&&!['chat','memory','relationship','activity'].includes(page)&&state.cid&&state.user){const use=storage.get('weiwan-use:'+state.cid);if(use){api('/api/conversations/'+state.cid+'/uses/'+use+'/close',{method:'POST',body:{}}).catch(()=>{});storage.delete('weiwan-use:'+state.cid);usesInDocument.delete(state.cid);}}
 if(id){if(page==='detail')state.role=id;else state.cid=id;}
 const hash=encodeRoute(page);if(location.hash===hash)await loadRoute();else location.hash=hash;
}
async function refreshUser(version){const session=await api('/api/auth/session');if(version!==epoch)return session;if(state.user&&state.user.id!==session.user?.id)clearPrivate();if(typeof recoveryCache!=='undefined')recoveryCache.restrictTo(session.user?.id||null);state.user=session.user;state.csrf=session.csrf_token;state.capabilities=session.capabilities||{};return session;}
async function loadRoute(){
 stopVoice();activityBusy=false;state.archive=null;state.archiveError=null;
 stopObserving();state.generation=null;state.stopping=false;state.streamError=null;
 pendingController?.abort();pendingController=new AbortController();const signal=pendingController.signal,version=++epoch;
 const [,rawPage,rawId,rawActivity]=location.hash.split('/'),valid=['discover','detail','messages','chat','memory','relationship','activity','me','auth'];
 state.activityId=rawPage==='activity'&&/^[a-f0-9]{32}$/.test(rawActivity||'')?rawActivity:null;
 state.page=valid.includes(rawPage)?rawPage:'discover';if(state.page==='detail')state.role=rawId||state.role;if(['chat','memory','relationship','activity'].includes(state.page))state.cid=rawId||state.cid;
 state.error=null;state.loading=true;retryMessage=pendingMessages.get(state.cid)||null;state.busy=false;closeModal();render();
 try{
  await refreshUser(version);if(version!==epoch)return;
  if(['chat','memory','relationship','activity'].includes(state.page)&&!protect())return;
  const [chars,convs]=await Promise.all([api('/api/characters',{signal}),state.user?api('/api/conversations',{signal}):Promise.resolve({items:[]})]);
  if(version!==epoch)return;state.characters=chars.items;state.conversations=convs.items;
  if(state.page==='detail'&&!state.characters.some(x=>x.id===state.role)){state.page='discover';}
  if(['chat','memory','relationship','activity'].includes(state.page)){
   const conversation=await api('/api/conversations/'+state.cid,{signal});if(version!==epoch)return;state.conversation=conversation;state.role=conversation.character_id;
   if(state.page==='chat'){
    await ensureConversationUse(signal);if(version!==epoch)return;
    state.relationship=await api(relationshipUrl(),{signal});if(version!==epoch)return;
    restoreLocalRecovery();
    const data=await api(recoveryUrl(true),{signal});if(version!==epoch)return;applyRecovery(data,true);
    state.branches=await api('/api/conversations/'+state.cid+'/branches',{signal,report:false}).catch(e=>{if(e.name==='AbortError')throw e;return null;});if(version!==epoch)return;
    if(state.capabilities.memory_write){const mem=await api('/api/conversations/'+state.cid+'/memories',{signal});if(version!==epoch)return;state.memory=mem.items;state.memoryInfo=mem;}
   }else if(state.page==='activity'){
    await ensureConversationUse(signal);if(version!==epoch)return;
    const data=await api('/api/conversations/'+state.cid+'/activities'+(state.activityId?'/'+state.activityId:''),{signal});if(version!==epoch)return;
    state.activity=state.activityId?data:null;state.activityList=state.activityId?null:data;restoreActivityPending();
   }else if(state.page==='memory'){const data=await api('/api/conversations/'+state.cid+'/memories',{signal});if(version!==epoch)return;state.memory=data.items;state.memoryInfo=data;}
   else {await ensureConversationUse(signal);if(version!==epoch)return;const [data,history,mem]=await Promise.all([api(relationshipUrl(),{signal}),api('/api/conversations/'+state.cid+'/messages',{signal}),api('/api/conversations/'+state.cid+'/memories',{signal})]);if(version!==epoch)return;state.relationship=data;state.relationSources=history.items;state.relationMemories=mem.items;state.proactive=null;api('/api/conversations/'+state.cid+'/proactive',{signal,report:false}).then(d=>{if(version===epoch){state.proactive=d;if(state.page==='relationship')render();}}).catch(()=>{});try{state.archive=await api('/api/conversations/'+state.cid+'/archive'+(state.useId?'?use_id='+state.useId:''),{signal});}catch(error){if(error.name==='AbortError')throw error;state.archiveError=error.message;}if(version!==epoch)return;try{state.relationPending=JSON.parse(storage.get(relationPendingKey())||'null')}catch{state.relationPending=null;}}
  }
  if(version!==epoch)return;state.loading=false;render();if(state.page==='chat'&&state.busy&&!state.localRecord.stop_intent)observeGeneration();
  if(state.page==='chat'&&!state.busy)requestGreeting(version);
 }catch(error){if(error.name==='AbortError'||version!==epoch)return;if(error.status===401){clearPrivate();state.user=null;protect();return}state.loading=false;state.error=error;render();}
}
async function authSubmit(form){const version=epoch,mode=state.authMode,fields=new FormData(form),payload={username:fields.get('username'),password:fields.get('password'),...(mode==='register'?{nickname:fields.get('nickname'),...(fields.get('invite_code')?{invite_code:fields.get('invite_code').trim()}:{})}:{})};const submit=form.querySelector('button');submit.disabled=true;submit.textContent='处理中…';try{const data=await api('/api/auth/'+mode,{method:'POST',body:payload});if(version!==epoch)return;clearPrivate({preserveGuestScene:true});state.user=data.user;state.csrf=data.csrf_token;form.reset();const target=returnTo;setReturn(null);if(target){state.role=target.role;state.cid=target.cid;await navigate(target.page)}else{const convs=await api('/api/conversations');state.conversations=convs.items;await navigate(convs.items.length?'messages':'discover')}}catch(error){if(version!==epoch)return;$('#auth-error').innerHTML=`<div class="error">${esc(error.message)}${error.requestId?'<br>编号：'+esc(error.requestId):''}</div>`;submit.disabled=false;submit.textContent='重试'+(mode==='register'?'注册':'登录');}finally{payload.password='';}}
async function action(name,value,el){const version=epoch;try{switch(name){
 case 'navigate':await navigate(value);break;
 case 'detail':await navigate('detail',value);break;
 case 'chat':setImmersive(null);await navigate('chat',value);break;
 case 'home-continue':{const conv=state.conversations.find(c=>c.character_id===value);if(conv){state.role=value;setImmersive(conv.id);await navigate('chat',conv.id);}break;}
 case 'chat-full':setImmersive(null);render();break;
 case 'suggest':{const cid=state.cid;el.disabled=true;el.classList.add('is-loading');const data=await api('/api/conversations/'+cid+'/suggestions',{method:'POST',body:{},report:false});if(version!==epoch||cid!==state.cid)return;state.replyOptions={cid,after:state.messages.at(-1)?.id,items:data.options};render();break;}
 case 'use-option':{const o=state.replyOptions,item=o?.items[Number(value)];if(!item)break;state.replyOptions=null;addToDraft(o.cid,item.text);break;}
 case 'close-options':state.replyOptions=null;render();break;
 case 'branches':{const cid=state.cid,data=await api('/api/conversations/'+cid+'/branches');if(version!==epoch||cid!==state.cid)return;state.branches=data;modal('剧情线',branchesHTML());break;}
 case 'branch-fork':modal('从这里另开一条线？','<p>新的一条线从这句话之后重新开始，这句话之前的内容都保留。</p><p>原来的线不会被改动，随时可以切回去；两条线之后聊出的内容、记忆和关系状态互不影响。</p>',button('另开一条线','branch-fork-confirm',value,'primary')+button('取消','close','','secondary'));break;
 case 'branch-fork-confirm':{el.disabled=true;const data=await api('/api/conversations/'+state.cid+'/branches',{method:'POST',body:{message_id:value}});if(version!==epoch)return;await afterBranchChange(data,'已开出「'+(data.items.find(b=>b.active)?.name||'新的一条线')+'」，原来的线还在');break;}
 case 'branch-switch':{el.disabled=true;const data=await api('/api/conversations/'+state.cid+'/branches/switch',{method:'POST',body:{branch_id:value||null}});if(version!==epoch)return;await afterBranchChange(data,'已切到「'+(data.items.find(b=>b.active)?.name||'主线')+'」');break;}
 case 'branch-delete':{const b=state.branches?.items.find(x=>x.id===value);if(!b)break;modal('删除「'+esc(b.name)+'」？','<p>这条线上的 '+b.messages+' 条对话会一起删除，不能恢复。其他线不受影响。</p>',button('确认删除','branch-delete-confirm',value,'primary')+button('先不删','branches','','secondary'));break;}
 case 'branch-delete-confirm':{el.disabled=true;const cid=state.cid,data=await api('/api/conversations/'+cid+'/branches/'+value,{method:'DELETE',body:{confirm:true}});if(version!==epoch||cid!==state.cid)return;state.branches=data;modal('剧情线',branchesHTML());render();toast('已删除');break;}
 case 'proactive-toggle':{el.disabled=true;const cid=state.cid,data=await api('/api/conversations/'+cid+'/proactive',{method:'POST',body:{enabled:!state.proactive?.enabled}});if(version!==epoch||cid!==state.cid)return;state.proactive=data;render();toast(data.enabled?'已开启主动消息':'已关闭主动消息');break;}
 case 'voice-input':await toggleVoiceInput(version);break;
 case 'chat-home':state.role=state.conversation?.character_id||state.role;await navigate('discover');break;
  case 'reload':await loadRoute();break;
  case 'login':setReturn({page:state.page,cid:state.cid,role:state.role});await navigate('auth');break;
 case 'guest':el.disabled=true;try{const data=await api('/api/auth/guest',{method:'POST',body:{}});if(version!==epoch)return;clearPrivate();state.user=data.user;state.csrf=data.csrf_token;setReturn(null);const conv=await api('/api/conversations',{method:'POST',body:{character_id:'linyu'}});await navigate('chat',conv.id);}catch(error){el.disabled=false;throw error;}break;
 case 'auth-mode':state.authMode=value;render();break;
 case 'start':if(!state.user){setReturn({page:'detail',role:state.role});await navigate('auth');break;}el.disabled=true;el.textContent='正在创建…';{const data=await api('/api/conversations',{method:'POST',body:{character_id:value,...(sceneDrafts[value]?{initial_scene:sceneDrafts[value]}:{})}});if(version!==epoch)return;await navigate('chat',data.id);}break;
 case 'records':modal('与'+state.conversation.character.name+'的记录','<p>记录严格归属当前角色与账号。</p>',button('记忆管理','memory')+button('关系档案','relationship','','secondary')+button('一起读短文','activity-intro','','secondary')+'<a class="secondary" href="/api/conversations/'+encodeURIComponent(state.cid)+'/export?format=md" download>导出记录（Markdown）</a><a class="secondary" href="/api/conversations/'+encodeURIComponent(state.cid)+'/export?format=json" download>导出（JSON）</a>');break;
 case 'activity-intro':state.activityId=null;closeModal();await navigate('activity');break;
 case 'activity-open':state.activityId=value;closeModal();await navigate('activity');break;
 case 'activity-start':await activityOperation('start',el);break;
 case 'activity-next':await activityOperation('next',el);break;
 case 'activity-resume':await activityOperation('resume',el);break;
 case 'activity-pause':await activityOperation('pause',el);break;
 case 'activity-finish':modal('结束这次共读？','<p>确认后将记录读完。活动记忆仍需你单独确认保存。</p>',button('确认读完','activity-finish-confirm')+button('继续看看','close','','secondary'));break;
 case 'activity-finish-confirm':closeModal();await activityOperation('finish',el);break;
 case 'activity-retry-reply':{const failed=state.activity?.messages?.find(m=>m.id===value&&m.status==='failed'),source=state.activity?.messages?.find(m=>m.id===failed?.reply_to_id);if(source)await activityOperation('message',el,source.content,failed.id);break;}
 case 'activity-retry':await activityOperation(null,el);break;
 case 'promise-complete':openPromiseConfirmation(value,'complete_promise');break;
 case 'promise-withdraw':openPromiseConfirmation(value,'withdraw_promise');break;
 case 'promise-confirm':await mutatePromise(value,el);break;
 case 'promise-retry':await mutatePromise(null,el);break;
 case 'voice-play':await playVoice(value);break;
 case 'voice-pause':pauseVoice();break;
 case 'voice-resume':await resumeVoice();break;
 case 'voice-stop':stopVoice();break;
 case 'memory':closeModal();await navigate('memory');break;
 case 'memory-tab':state.memoryTab=value;if(value==='portrait'){state.portrait=null;render();state.portrait=await api('/api/conversations/'+state.cid+'/portrait');if(version!==epoch)return;}render();break;
 case 'portrait-generate':await generatePortrait(el);break;
 case 'memory-extract':el.disabled=true;await api('/api/conversations/'+state.cid+'/memories/extract',{method:'POST',body:{}}).then(d=>{if(d.error)throw new Error('记忆提取模型尚未配置');});await loadRoute();break;
 case 'memory-refresh':await loadRoute();break;
 case 'memory-confirm':await changeMemory(value,'confirm',el);break;
 case 'memory-reject':await changeMemory(value,'reject',el);break;
 case 'memory-reindex':await changeMemory(value,'reindex',el);break;
 case 'memory-edit':{const m=state.memory.find(x=>x.id===value);if(!m)break;state.editingMemory={id:m.id,revision:m.revision};modal('修改记忆',`<form id="memory-edit-form"><label class="field">记忆内容<textarea id="memory-content" minlength="2" maxlength="400" required>${esc(m.content)}</textarea></label><label class="field">结束时间（临时边界或待核对约定需填写）<input id="memory-until" type="datetime-local" value="${m.valid_until?esc(new Date(m.valid_until*1000-new Date(m.valid_until*1000).getTimezoneOffset()*60000).toISOString().slice(0,16)):''}"></label><p>事实修正保持原归属；约定与经历的改动先待核对，不能通过改字新增已完成经历。</p><div id="memory-error"></div><button class="primary wide">保存修改</button></form>`);break;}
 case 'memory-delete':{const m=state.memory.find(x=>x.id===value);if(!m)break;modal('删除这条记忆？','<p>这条信息将不再用于后续聊天，原聊天记录仍保留。依赖这些来源的其他记忆也可能失效。</p><div id="memory-error"></div>',button('确认删除','memory-delete-confirm',value)+button('取消','close','','secondary'));break;}
 case 'memory-delete-confirm':await changeMemory(value,'delete',el);break;
 case 'memory-source':state.localRecord.view={at_latest:false,anchor_id:value,anchor_offset:20};persistLocalRecovery();closeModal();await navigate('chat');break;
 case 'relationship':closeModal();await navigate('relationship');break;
 case 'relation-choice':openRelationshipForm(value);break;
 case 'relation-save-event':await relationshipOperation({action:'save_event',event_id:value},el);break;
 case 'relation-delete-event':{const e=state.relationship.events.find(x=>x.id===value);if(!e)break;modal('删除事件与状态影响','<p>原聊天保留，这条事件不再用于后续表达，同来源的关联记忆也会失效。若它是当前关系或熟悉程度的依据，删除将分别恢复未约定或初识；暂停亲密表达。</p><div id="relation-error"></div>',button('确认删除并使依赖状态失效','relation-delete-confirm',value)+button('取消','close','','secondary'));break;}
 case 'relation-delete-confirm':{const e=state.relationship.events.find(x=>x.id===value);await relationshipOperation({action:'delete_event',event_id:value,event_revision:e.revision,result_type:'unspecified',result_familiarity:'new'},el);break;}
 case 'relation-edit-event':{const e=state.relationship.events.find(x=>x.id===value);modal('纠正事件',`<form id="relation-edit-form" data-event="${e.id}" data-revision="${e.revision}"><label class="field">事件内容<textarea id="relation-content" minlength="2" maxlength="400">${esc(e.content)}</textarea></label><p>修改先标待核对，不作为已成立经历或关系依据。需通过有效新来源重新确认。</p><div id="relation-error"></div><button class="primary wide">保存并待核对</button></form>`);break;}
 case 'relation-import':await relationshipOperation({action:'import_memory',memory_id:value,memory_revision:state.relationMemories.find(m=>m.id===value)?.revision},el);break;
 case 'relation-retry':await relationshipOperation(null,el);break;
 case 'edit-scene':modal('调整初始场景',`<label class="field">尚未发生的背景<textarea id="scene-input" maxlength="500">${esc(sceneDrafts[state.role]||state.characters.find(r=>r.id===state.role).initial_scene)}</textarea></label><p>调整背景不等于创造已经发生的经历。</p>`,button('采用这个背景','save-scene'));break;
 case 'save-scene':{const text=$('#scene-input').value.trim();if(!text){toast('请填写背景');break}sceneDrafts[state.role]=text;closeModal();render();break;}
 case 'nickname':modal('修改昵称',`<form id="nickname-form"><label class="field">昵称<input name="nickname" maxlength="16" required value="${esc(state.user.nickname)}"></label><div id="nickname-error"></div><button class="primary wide">保存昵称</button></form><p class="stage-label">此修改不会自动告诉三个角色。</p>`);break;
 case 'privacy':modal('账号与记录','<p>账号、会话和消息保存到当前开发服务的数据库；同一服务上的同一账号可以重新登录恢复。退出会使当前登录凭证失效，并清理页面中的私人内容与草稿，已保存记录保留。<br><br>密码只保存加盐哈希，登录凭证保存在 HttpOnly Cookie，前端不使用原型的 localStorage 模拟账号。本浏览器保存按账号隔离的草稿、原请求标识与阅读位置；退出、账号切换或登录失效会清理。已发送记录以服务端为准。当前仅运行本机开发服务，不代表已部署上线。</p>');break;
 case 'logout':el.disabled=true;await api('/api/auth/logout',{method:'POST',body:{}});if(version!==epoch)return;clearPrivate();state.user=null;setReturn(null);await navigate('me');toast('已退出登录');break;
 case 'close':closeModal();if(state.page==='relationship'&&state.relationPending)render();break;
 case 'retry-message':await saveMessage(retryMessage);break;
 case 'delete-message':modal('删除这条消息？','<p>删除后这条消息只显示「已删除」，不再用于后续聊天、记忆整理和召回；以它为来源的记忆会一起失效。此操作不能撤销。</p><div id="delete-error"></div>',button('删除','delete-message-confirm',value,'primary')+button('取消','close','','secondary'));break;
 case 'delete-message-confirm':el.disabled=true;await api('/api/conversations/'+state.cid+'/messages/'+value+'/delete',{method:'POST',body:{}});if(version!==epoch)return;closeModal();await refreshMessages();render();toast('已删除');break;
 case 'wrong-reply':await openWrongReply(value);break;
 case 'wrong-fix':await fixRecalledMemory(value,el);break;
 case 'wrong-tell':{closeModal();const d=$('#draft');if(d){d.value='你刚才说错了：';d.focus();d.dispatchEvent(new Event('input',{bubbles:true}));}break;}
 case 'memory-release':await changeMemory(value,'release_suppression',el);break;
 case 'memory-history':await openMemoryHistory(value);break;
 case 'memory-restore':{const [id,rev]=value.split(':');await restoreMemoryVersion(id,Number(rev),el);break;}
 case 'copy-message':{const m=state.messages.find(m=>m.id===value);if(m){try{await navigator.clipboard.writeText(m.content);toast('已复制')}catch{toast('请选择消息文本复制')}}break;}
 case 'retry-generation':await retryGeneration();break;
 case 'regenerate':await createCandidate(value);break;
 case 'stop-generation':await stopGeneration();break;
 case 'keep-candidate':await chooseCandidate('keep');break;
 case 'adopt-candidate':await chooseCandidate('adopt');break;
 case 'candidate-original':state.candidateView='old';persistLocalRecovery();render();break;
 case 'candidate-new':state.candidateView='new';persistLocalRecovery();render();break;
 case 'reconcile-generation':await reconcileGeneration();break;
 case 'latest-messages':state.localRecord.view={at_latest:true};persistLocalRecovery();await refreshMessages();render();break;
 case 'resume-saved-reply':{const source=currentUserMessage();if(source){const pending={type:'generation',content:source.content,user_message_id:source.id,generation_key:'reply_'+source.id};pendingMessages.set(state.cid,pending);persistLocalRecovery();await startReply(pending);}}break;
 case 'retry-candidate-create':await createCandidate(state.localRecord.candidate_intent?.original_reply_id,true);break;
 case 'earlier':el.disabled=true;{const oldArea=$('#chat-scroll'),oldHeight=oldArea.scrollHeight,oldTop=oldArea.scrollTop;const data=await api('/api/conversations/'+state.cid+'/messages?before='+state.nextBefore);if(version!==epoch)return;state.messages=[...data.items,...state.messages];state.nextBefore=data.next_before;render();const area=$('#chat-scroll');area.scrollTop=area.scrollHeight-oldHeight+oldTop;}break;
 }}catch(error){if(version!==epoch)return;if(error.status===401){clearPrivate();state.user=null;protect();return}toast(error.message);el.disabled=false;el.classList?.remove('is-loading');if(name==='start')el.textContent='重试开始聊天 →';}}
document.addEventListener('click',e=>{const el=e.target.closest('[data-action]');if(el&&!el.disabled)action(el.dataset.action,el.dataset.value,el);});
document.addEventListener('error',e=>{const el=e.target;if(el?.tagName==='IMG'&&el.closest('.avatar'))el.hidden=true;},true);
document.addEventListener('submit',async e=>{e.preventDefault();if(e.target.id==='auth-form')return authSubmit(e.target);if(e.target.classList?.contains('starter'))return homeStart(e.target);if(e.target.id==='message-form')return saveMessage();if(e.target.id==='activity-message-form')return sendActivityMessage(e.target);if(e.target.id==='memory-edit-form')return saveMemoryEdit(e.target);if(e.target.id==='relation-form'||e.target.id==='relation-edit-form')return submitRelationshipForm(e.target);if(e.target.id==='nickname-form'){const version=epoch,form=e.target,input=form.elements.nickname,submit=form.querySelector('button');submit.disabled=true;submit.textContent='保存中…';try{const data=await api('/api/profile',{method:'PATCH',body:{nickname:input.value}});if(version!==epoch)return;state.user.nickname=data.nickname;closeModal();render();toast('昵称已保存');}catch(error){if(version!==epoch)return;if(error.status===401){clearPrivate();state.user=null;protect();return}$('#nickname-error').innerHTML=`<div class="error">${esc(error.message)}</div>`;submit.disabled=false;submit.textContent='重试保存';}}});
document.addEventListener('keydown',e=>{if(e.isComposing)return;const sheet=$('#modal .sheet');if(!sheet)return;if(e.key==='Escape')return closeModal();if(e.key==='Tab'){const fields=[...sheet.querySelectorAll('button,input,textarea,select')].filter(x=>!x.disabled);if(e.shiftKey&&document.activeElement===fields[0]){e.preventDefault();fields.at(-1)?.focus()}else if(!e.shiftKey&&document.activeElement===fields.at(-1)){e.preventDefault();fields[0]?.focus()}}});
window.addEventListener('hashchange',loadRoute);
window.addEventListener('focus',()=>{if(composingCid||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName))return;if(!state.busy&&!activityBusy&&state.page!=='auth'&&!$('#modal .sheet'))loadRoute();});
function handleChatAuth(error){if(error.status!==401)return false;clearPrivate();state.user=null;protect();return true;}
function handleUnavailableSource(error){if(error.code!=='source_unavailable')return false;stopVoice();stopObserving();state.busy=false;state.streamError=null;state.replyRequired=false;if(state.generation)state.generation.source_available=false;retryMessage=null;pendingMessages.delete(state.cid);delete state.localRecord.candidate_intent;persistLocalRecovery();render();toast(error.message);return true;}
function stopObserving(){clearTimeout(reconcileTimer);reconcileTimer=null;streamSerial++;streamController?.abort();streamController=null;}
function isUndecided(){return state.generation?.request?.status==='awaiting_choice';}
function generationRoleEnabled(role){return (state.capabilities.chat_roles||['linyu','luye','shenyan']).includes(role);}
function currentUserMessage(){return state.latestUser||[...state.messages].reverse().find(m=>m.role==='user');}
function generationRecoverable(){const r=state.generation?.request;return state.generation?.source_available!==false&&r&&['failed','stopped'].includes(r.status)&&currentUserMessage()?.id===r.user_message_id;}
function chatHTML(c){
 const g=state.generation,r=g?.request,active=r&&['queued','generating'].includes(r.status);
 const candidate=r?.kind==='regenerate'&&['queued','generating','awaiting_choice'].includes(r.status);
 const lastUser=currentUserMessage();
 // Only the newest visible reply can be regenerated, and only if it answers the newest user message
 // (after a deletion an older reply must not become regenerable mid-conversation).
 const newestReply=[...state.messages].reverse().find(m=>m.role==='assistant'&&m.reply_to_id&&!m.deleted);
 const lastReply=newestReply&&newestReply.status==='completed'&&newestReply.reply_to_id===lastUser?.id?newestReply:undefined;
 const mode=state.capabilities.model_status==='simulated'?'本地模拟服务 · 非真实模型':state.capabilities.model_status==='ready'?'真实模型 · '+esc(state.capabilities.model_label):'真实模型未配置';
 // A first line typed on the home page before signing in becomes this conversation's draft.
 const typed=homeDraft();if(typed&&typed.role===c.character_id){if(!drafts.get(c.id))drafts.set(c.id,typed.text);storage.delete('weiwan-home-draft');setImmersive(c.id);}
 // Immersive = chatting in place on the home portrait (规范：发送后不跳页). Bubbles only; every tool lives in 完整对话.
 const immersive=immersiveCid()===c.id;
 let html;
 if(immersive){
  html=`<div class="chat-screen immersive">${portraitPath(c.character_id)?`<img class="backdrop" src="${portraitPath(c.character_id)}" alt="" decoding="async">`:''}<div class="shade"></div><header class="topbar home-top"><button class="back glass-button" aria-label="回到首页，换一个人" data-action="chat-home">${icon('chevron-down')}</button><span class="wordmark">不晚</span><button class="icon-button" data-action="chat-full">完整对话</button></header>`;
 }else{
 html=`<div class="chat-screen"><header class="chat-header"><button class="back" aria-label="返回消息" data-action="navigate" data-value="messages">${icon('chevron-left')}</button>${avatar(c.character_id)}<div class="name"><strong>${esc(c.character.name)}</strong><small>AI 角色 · 绑定 v${c.version}</small></div><button class="icon-button" data-action="records">记忆与关系</button></header><div class="foundation-note">${mode} · <a href="/evaluation">切换模型 / 回复评测 ↗</a></div>`;
 if(state.relationship?.state){const rs=state.relationship.state;html+='<div class="foundation-note">'+({unspecified:'未约定',friends:'朋友',romantic:'恋爱'}[rs.relationship_type]||'未约定')+' · '+({new:'初识',familiar:'熟悉',close:'亲近'}[rs.familiarity]||'初识')+(rs.intimacy_paused?' · 亲密表达已暂停':'')+(rs.pace?' · '+esc(rs.pace):'')+'</div>';}
 html+='<div class="activity-entry">'+button('一起读一篇短文 →','activity-intro','','text-action')+'</div>';
 html+=branchBarHTML();
 if(c.has_newer_version)html+='<div class="version">已有对话继续使用原设定。</div>';
 }
 html+='<div class="scroll chat-area" id="chat-scroll" tabindex="0" aria-label="聊天记录">';
 if(state.nextBefore)html+=button('加载更早消息','earlier','','secondary wide');
 html+=`<div class="date">初始背景 · ${esc(c.initial_scene)}</div>`;
 for(const m of state.messages){
  // Roadmap 1.14: a deleted message keeps its place but shows no text or actions.
  if(m.deleted){html+=`<article class="message deleted ${m.role==='user'?'user':''}" id="${m.id}"><div class="bubble">这条消息已删除</div><div class="meta">已删除，不再用于后续聊天与记忆${messageTime(m.created_at)}</div></article>`;continue;}
  const label=m.source_type==='activity'||(m.role==='system'&&m.content.startsWith('【共读进度记录'))?'共读记录':m.source_type==='promise_lifecycle'?'约定记录':m.role==='user'?'已保存':m.status==='streaming'?'正在回复':m.status==='pending'?'等待回复':m.status==='stopped'?'已停止 · 中断片段':m.status==='failed'?'未完成':m.reply_to_id?'已完成':m.content.startsWith('【关系操作】')?'用户关系操作':m.proactive?'主动消息':'预设开场';
  html+=`<article class="message ${m.role==='user'?'user':m.role==='system'?'system':''}" id="${m.id}"><div class="bubble">${esc(m.content)||(['pending','streaming'].includes(m.status)?'···':'（无已生成正文）')}</div><div class="meta">${label}${messageTime(m.created_at)}`;
  if(m===lastReply&&!candidate&&!active&&!(state.generation?.source_available===false&&state.generation?.request?.user_message_id===m.reply_to_id))html+=button('重新生成','regenerate',m.id,'text-action');
  if(m.content)html+=button('复制','copy-message',m.id,'text-action');
  // Roadmap 1.7 / 1.14: correct a wrong reply, or delete a message that should not stay.
  const settled=!active&&!(candidate&&m===lastReply);
  if(settled&&m.role==='assistant'&&m.reply_to_id&&m.status==='completed')html+=button('说错了','wrong-reply',m.id,'text-action');
  if(settled&&!m.source_type&&((m.role==='user'&&m.status==='saved')||(m.role==='assistant'&&m.reply_to_id&&['completed','stopped','failed'].includes(m.status))))html+=button('删除','delete-message',m.id,'text-action');
  // Roadmap 2.1: start another story line after any finished line of the character (not the newest: just keep talking).
  if(settled&&m.role==='assistant'&&m.status==='completed'&&!m.source_type&&m!==state.messages.at(-1)&&!state.hasNewer)html+=button('从这里另开一条线','branch-fork',m.id,'text-action');
  if(m.activity_id)html+=button('回到共读活动','activity-open',m.activity_id,'text-action');
  html+='</div>'+(voiceEligible(m)?'<div class="voice-controls" data-voice-message="'+esc(m.id)+'">'+voiceHTML(m.id)+'</div>':'')+'</article>';
 }
 if(candidate){
  html+='<section class="candidate"><small>候选回复 · 采用前原回复仍有效</small>';
  if(r.status==='awaiting_choice'){
   html+='<div class="record-actions">'+button('原回复','candidate-original','','secondary')+button('新候选','candidate-new','','secondary')+'</div>';
   html+=`<p class="prewrap">${esc(state.candidateView==='old'?g.original?.content:g.message?.content)}</p>`;
   html+='<div class="record-actions">'+button('采用新候选','adopt-candidate')+button('保留原回复','keep-candidate','','secondary')+'</div>';
  }else html+=`<p class="prewrap">${esc(g.message?.content)||'正在生成候选…'}</p>`+button('取消生成，保留原回复','keep-candidate','','secondary');
  html+='</section>';
 }
 if(generationRecoverable())html+='<div class="error">'+(r.status==='stopped'?'已停止，片段不会作为完整事件。':esc(g.error_message||'回复未完成，用户原输入已保留。'))+button(r.kind==='regenerate'?'重试候选':r.status==='stopped'?'重新生成':'重试回复','retry-generation','','secondary')+'</div>';
 if(retryMessage)html+='<div class="error">'+esc(retryMessage.error||'原请求结果待核对')+button('重试原来一轮','retry-message','','secondary')+'</div>';
 if(state.streamError)html+='<div class="error">'+esc(state.streamError)+button('核对服务端状态','reconcile-generation','','secondary')+'</div>';
 html+='<button id="latest-read" class="latest" data-action="latest-messages" '+(!state.hasNewer&&state.localRecord.view?.at_latest!==false?'hidden':'')+'>回到最新</button>';
 if(state.replyRequired&&!retryMessage)html+='<div class="hint">消息已保存，回复尚未开始。'+button('为原消息生成回复','resume-saved-reply','','secondary')+'</div>';
 if(state.localRecord.candidate_intent)html+='<div class="error">候选创建结果待核对。'+button('重发原候选请求','retry-candidate-create','','secondary')+'</div>';
 if(state.localRecord.stop_intent)html+='<div class="hint">停止结果待确认。'+button('重试停止','stop-generation','','secondary')+'</div>';
 const pendingMem=state.memory?.filter(m=>['candidate','conflicted'].includes(m.status)).length||0;
 html+='<div id="memory-notice">'+(pendingMem?'<div class="hint">有 '+pendingMem+' 条待确认记忆。'+button('查看记忆','memory','','text-action')+'</div>':'')+'</div>';
 if(state.cacheWarning)html+='<div class="foundation-note">浏览器未允许本地草稿保存；已保存历史仍从服务端恢复。</div>';
 if(state.stopping)html+='<div class="foundation-note">停止处理中，界面已冻结追加…</div>';
 html+='</div>';
 if(immersive)html+=`<div class="spotlight-intro"><div class="home-head"><h2 class="home-name">${esc(c.character.name)}</h2><span class="home-job">${c.character.age} · ${esc(c.character.job)}</span></div><div class="tags"><span class="pill">AI 角色</span>${(c.character.tags||[]).map(t=>`<span class="pill">${esc(t)}</span>`).join('')}</div></div>`;
 html+=`${replyOptionsHTML(c)}<form id="message-form" class="composer"><div class="composer-input"><div class="composer-row"><textarea id="draft" data-conversation="${esc(c.id)}" rows="1" enterkeyhint="enter" aria-label="聊天输入" placeholder="${candidate?'先决定候选，再继续聊天…':state.voice==='recording'?'正在听，说完点一下话筒…':state.voice==='transcribing'?'正在转成文字…':'想说什么，慢慢说。'}">${esc(drafts.get(c.id)||'')}</textarea>${composerTools(active||!!candidate)}</div><small id="input-limit" aria-live="polite" hidden></small></div>`;
 html+=active?button(state.stopping?'停止中':'■','stop-generation','','send'):('<button class="send" aria-label="发送" '+(state.busy||candidate||state.streamError?'disabled':'')+'>'+icon('arrow-up')+'</button>');
 return html+'</form>'+(immersive?nav('discover'):'')+'</div>';
}
// 路线 2.1：剧情线。原来的线一直都在；切换、另开都不新建会话。
function currentBranch(){return state.branches?.items.find(b=>b.active)||null;}
function branchBarHTML(){
 const b=state.branches;if(!b||b.items.length<2)return '';
 const now=currentBranch();
 return `<div class="branch-bar">${icon('fork')}<span>现在在「${esc(now?.name||'主线')}」${now?.parent_name?' · 从「'+esc(now.parent_name)+'」分出':''}</span>${button('切换剧情线','branches','','text-action')}</div>`;
}
function branchesHTML(){
 const b=state.branches;
 return '<p>每条线有自己的对话、记忆和关系状态，互不影响。切走以后原来的线原样保留。</p><div id="branch-error"></div><ul class="branch-list">'+b.items.map(x=>
  `<li class="depth-${Math.min(x.depth,3)}"><button type="button" class="branch" ${x.active?'aria-current="true"':'data-action="branch-switch"'} data-value="${esc(x.id||'')}">${icon(x.id?'fork':'knot')}<span><strong>${esc(x.name)}</strong><small>${x.id?'从「'+esc(x.fork_preview||'一条已删除的消息')+'」之后分出 · ':''}${x.messages} 条${x.preview?' · '+esc(x.preview):''}</small></span><em>${x.active?'正在聊':'切到这条'}</em></button>${x.deletable?button('删除这条线','branch-delete',x.id,'text-action'):''}</li>`).join('')+'</ul><p class="sheet-note">只能删除没有再分出别的线、也不是正在聊的线。最多 '+b.limit+' 条支线。</p>';
}
async function afterBranchChange(data,message){
 state.branches=data;state.replyOptions=null;state.localRecord.view={at_latest:true};persistLocalRecovery();closeModal();
 await navigate('chat',state.cid);toast(message);
}
// 路线 2.5：打开聊天时问一次服务端“要不要先说一句”。规则全在服务端；不出现、失败都不提示。
async function requestGreeting(version){
 const cid=state.cid;
 try{const data=await api('/api/conversations/'+cid+'/greeting',{method:'POST',body:{},report:false});
  if(!data.message||version!==epoch||cid!==state.cid||state.page!=='chat'||state.busy)return;
  await refreshMessages(version);if(version===epoch)render();
 }catch{}
}
// 路线 2.2 / 2.6：两个工具都只往输入框里写字，发不发由用户决定。
function composerTools(blocked){
 let html='';
 if(state.capabilities.reply_suggestions)html+=`<button type="button" class="composer-tool" data-action="suggest" aria-label="帮我想想怎么回" title="帮我想想怎么回" ${blocked||state.voice!=='idle'?'disabled':''}>${icon('spark')}</button>`;
 if(state.capabilities.voice_input)html+=`<button type="button" class="composer-tool${state.voice==='recording'?' is-recording':state.voice==='transcribing'?' is-loading':''}" data-action="voice-input" aria-label="${state.voice==='recording'?'说完了，转成文字':'语音输入'}" title="${state.voice==='recording'?'说完了，转成文字':'语音输入'}" aria-pressed="${state.voice==='recording'}" ${blocked||state.voice==='transcribing'?'disabled':''}>${icon('mic')}</button>`;
 return html?'<div class="composer-tools">'+html+'</div>':'';
}
function replyOptionsHTML(c){
 const o=state.replyOptions;if(!o||o.cid!==c.id||o.after!==state.messages.at(-1)?.id)return '';
 return `<div class="reply-options" role="group" aria-label="可以这样回"><div class="reply-options-head"><span>可以这样回 · 点了只会填进输入框，可以再改</span><button type="button" class="text-action" data-action="close-options">收起</button></div>${o.items.map((x,i)=>`<button type="button" class="reply-option" data-action="use-option" data-value="${i}"><small>${esc(x.tone)}</small><span>${esc(x.text)}</span></button>`).join('')}</div>`;
}
function addToDraft(cid,text){
 if(cid!==state.cid||state.page!=='chat')return;
 drafts.set(cid,(drafts.get(cid)||'')+text);persistLocalRecovery();render();
 const input=$('#draft');if(input){input.focus();input.setSelectionRange(input.value.length,input.value.length);}
}
let voiceStarting=false;
async function toggleVoiceInput(version){
 const cid=state.cid;
 if(state.voice==='idle'){
  if(!voiceInput.supported()){toast(window.isSecureContext?'这个浏览器不支持录音，可以直接打字':'当前链接不能使用话筒，请换成 https 链接，或直接打字');return;}
  if(voiceStarting)return;voiceStarting=true;  // 权限弹窗还没答复时，再点一次不重复申请
  try{await voiceInput.start(()=>toggleVoiceInput(epoch));}catch{toast('没有拿到话筒权限，可以在浏览器设置里允许，或直接打字');return;}finally{voiceStarting=false;}
  if(version!==epoch||cid!==state.cid){voiceInput.stop(true);return;}
  state.voice='recording';render();return;
 }
 if(state.voice!=='recording')return;
 state.voice='transcribing';render();
 try{
  const wav=await voiceInput.stop();
  if(!wav)throw new Error('没有录到声音，可以再说一次');
  const response=await fetch('/api/conversations/'+cid+'/transcriptions',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'audio/wav','X-CSRF-Token':state.csrf||''},body:wav});
  const data=await response.json();
  if(!response.ok)throw new RequestError(response.status,data);
  if(version!==epoch){state.voice='idle';return;}
  state.voice='idle';addToDraft(cid,data.text);if(cid!==state.cid)render();
 }catch(error){state.voice='idle';if(version!==epoch)return;if(error.status===401){clearPrivate();state.user=null;protect();return}render();toast(error instanceof RequestError?error.message:(error.message&&/[\u4e00-\u9fa5]/.test(error.message)?error.message:'没有识别出来，可以再说一次或直接打字'));}
}
async function refreshMessages(version=epoch){
 const data=await api(recoveryUrl(true));if(version!==epoch)return;applyRecovery(data,false);
}
function applyGeneration(data){
 const r=data.request;if(!r)return false;
 const old=state.generation?.request;
 if(old&&old.id===r.id&&(old.attempt>r.attempt||(old.attempt===r.attempt&&old.revision>r.revision)))return false;
 state.generation=data;state.busy=['queued','generating'].includes(r.status);state.streamError=null;
 if(r.kind==='reply'&&data.message){
  const i=state.messages.findIndex(m=>m.id===data.message.id);
  if(i<0){if(!state.hasNewer)state.messages.push(data.message);}else state.messages[i]={...state.messages[i],...data.message};
 }
 if(['failed','stopped'].includes(r.status)&&r.kind==='reply'&&!drafts.get(state.cid)){
  const source=state.messages.find(m=>m.id===r.user_message_id);if(source)drafts.set(state.cid,source.content);
 }
 return true;
}
function renderStreamSnapshot(data){
 const area=$('#chat-scroll'),scroll=!area||area.scrollHeight-area.scrollTop-area.clientHeight<90;
 const top=area?.scrollTop||0,input=$('#draft'),focused=document.activeElement===input,hadError=state.streamError;
 const start=input?.selectionStart,end=input?.selectionEnd,previous=state.generation?.request;
 // Stream/poll updates cannot replace another request's view. Creating a new
 // request still uses applyGeneration directly in its explicit action handler.
 if(previous&&previous.id!==data.request?.id)return false;
 if(!applyGeneration(data))return false;
 const accepted=state.generation,r=accepted.request;
 const same=!hadError&&previous?.id===r.id&&previous?.status===r.status&&previous?.attempt===r.attempt;
 const bubble=same&&r.kind==='reply'&&accepted.message?.id&&document.getElementById?.(accepted.message.id)?.querySelector('.bubble');
 const candidate=same&&r.kind==='regenerate'&&$('#app .candidate .prewrap');
 if(bubble||candidate){
  const content=candidate&&r.status==='awaiting_choice'&&state.candidateView==='old'?accepted.original?.content:accepted.message?.content;
  (bubble||candidate).textContent=content||'···';
  if(scroll&&area&&!state.hasNewer)area.scrollTop=area.scrollHeight;
  persistLocalRecovery();return true;
 }
 render();const next=$('#chat-scroll');if(next&&!scroll)next.scrollTop=top;
 if(focused&&$('#draft')){$('#draft').focus();$('#draft').setSelectionRange(start,end);}
 persistLocalRecovery();return true;
}
async function saveMessage(existing){
 const content=(existing?.content||$('#draft')?.value||'').trim();
 if([...content].length>1000){toast('最多输入1000字，内容已保留，请缩短后发送。');return;}
 if(!content||state.busy||isUndecided()||composingCid===state.cid)return;
 if(existing?.type==='generation')return startReply(existing);
 if(!existing&&generationRecoverable()&&state.generation.request.kind==='reply'){const old=currentUserMessage();if(old?.content===content)return retryGeneration();}
 if(retryMessage&&!existing){toast('请先重试原来一轮；原输入仍保留。');return;}
 const request=existing||{content,client_request_id:newRequestKey()};
 const version=epoch,cid=state.cid;
 state.localRecord.view={at_latest:true};state.busy=true;drafts.set(cid,content);pendingMessages.set(cid,{...request,error:'保存结果待核对，重试不会重复保存。'});persistLocalRecovery();render();
 try{
  const saved=await api('/api/conversations/'+cid+'/messages',{method:'POST',body:{content:request.content,client_request_id:request.client_request_id}});
  if(version!==epoch)return;
  if(saved.relationship_state)state.relationship={...(state.relationship||{}),state:saved.relationship_state};
  retryMessage=null;pendingMessages.delete(cid);
  if(generationRoleEnabled(state.role)){
   const next={type:'generation',content,user_message_id:saved.id,generation_key:'reply_'+saved.id,client_request_id:request.client_request_id};
   pendingMessages.set(cid,next);persistLocalRecovery();state.busy=false;await startReply(next);
  }else{
   if(drafts.get(cid)===content)drafts.delete(cid);persistLocalRecovery();await loadRoute();toast('消息已保存；当前角色生成能力未启用。');
  }
 }catch(error){
  if(version!==epoch)return;if(error.status===401){clearPrivate();state.user=null;protect();return;}
  retryMessage={...request,error:error.message};pendingMessages.set(cid,retryMessage);state.busy=false;persistLocalRecovery();render();
 }
}
async function startReply(pending){
 const version=epoch,cid=state.cid;state.localRecord.view={at_latest:true};state.busy=true;persistLocalRecovery();render();
 try{
  const data=await api('/api/conversations/'+cid+'/generations',{method:'POST',body:{kind:'reply',user_message_id:pending.user_message_id,client_request_id:pending.generation_key}});
  if(version!==epoch)return;
  retryMessage=null;pendingMessages.delete(cid);if(drafts.get(cid)===pending.content)drafts.delete(cid);persistLocalRecovery();
  await refreshMessages(version);if(version!==epoch)return;delete state.localRecord.candidate_intent;persistLocalRecovery();applyGeneration(data);render();if(state.busy)observeGeneration();
 }catch(error){
  if(version!==epoch)return;if(error.status===401){clearPrivate();state.user=null;protect();return;}if(handleUnavailableSource(error))return;
  state.busy=false;retryMessage={...pending,error:error.message};pendingMessages.set(cid,retryMessage);persistLocalRecovery();
  await refreshMessages(version).catch(()=>{});if(version===epoch){render();if(state.busy&&!state.localRecord.stop_intent)observeGeneration();}
 }
}
async function observeGeneration(){
 stopObserving();const serial=streamSerial,version=epoch,cid=state.cid,rid=state.generation?.request?.id;
 if(!rid)return;streamController=new AbortController();let lastSnapshot=Date.now();
 const schedule=()=>{reconcileTimer=setTimeout(async()=>{
  if(serial!==streamSerial||version!==epoch)return;
  if(Date.now()-lastSnapshot>=1500){
   try{const active=await pollGenerationStatus(serial,version,cid,rid);lastSnapshot=Date.now();if(!active)return;}
   catch(error){if(serial!==streamSerial||version!==epoch)return;if(handleChatAuth(error))return;if(Date.now()-lastSnapshot>8000){state.streamError='连接进度暂未确认，可核对服务端状态；不会自动重发。';render();}}
  }
  if(serial===streamSerial&&version===epoch)schedule();
 },2000);};schedule();
 try{
  const response=await fetch(generationReadUrl(cid,rid,'/stream'),{credentials:'same-origin',signal:streamController.signal,cache:'no-store'});
  if(!response.ok){if(response.status===401)throw Object.assign(new Error('登录已失效'),{status:401});throw new Error('无法读取生成进度');}
  if(!response.body)throw new Error('浏览器不支持流式读取');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  while(true){
   const {value,done}=await reader.read();if(serial!==streamSerial||version!==epoch)return;
   buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let split;
   while((split=buffer.indexOf('\n\n'))>=0){
    const block=buffer.slice(0,split);buffer=buffer.slice(split+2);
    if(block.startsWith('event: auth'))throw Object.assign(new Error('登录已失效'),{status:401});
    const text=block.split('\n').filter(x=>x.startsWith('data: ')).map(x=>x.slice(6)).join('\n');
    if(text){const data=JSON.parse(text);if(serial!==streamSerial||state.stopping)return;lastSnapshot=Date.now();renderStreamSnapshot(data);}
   }
   if(done)break;
  }
  if(serial!==streamSerial||version!==epoch)return;
  if(state.busy)throw new Error('连接中断，请先核对保存状态；不会重复调用模型。');
  if(['adopted','kept'].includes(state.generation?.request?.status)){
   await refreshMessages(version);if(version===epoch){render();refreshMemoryNotice(version);}
  }
 }catch(error){
  if(error.name==='AbortError'||serial!==streamSerial||version!==epoch)return;
  if(error.status===401){clearPrivate();state.user=null;protect();return;}
  state.streamError=error.message;render();
 }finally{if(serial===streamSerial){clearTimeout(reconcileTimer);reconcileTimer=null;}}
}
function generationReadUrl(cid,rid,suffix=''){return '/api/conversations/'+cid+'/generations/'+rid+suffix+(state.cid===cid&&state.useId?'?use_id='+encodeURIComponent(state.useId):'');}
async function pollGenerationStatus(serial,version,cid,rid){
 const data=await api(generationReadUrl(cid,rid));
 if(serial!==streamSerial||version!==epoch||state.cid!==cid||state.stopping)return false;
 if(!state.generation?.request||state.generation.request.id!==rid)return false;
 if(data.request.attempt<state.generation.request.attempt)return true;
 renderStreamSnapshot(data);
 const active=state.busy;
 if(!active){stopObserving();await refreshMessages(version);if(version===epoch){render();if(['adopted','kept'].includes(state.generation?.request?.status))refreshMemoryNotice(version);}}
 return active;
}
async function retryGeneration(){
 const r=state.generation?.request;if(!r||state.busy)return;
 const version=epoch;state.busy=true;render();
 try{
  const data=await api('/api/conversations/'+state.cid+'/generations/'+r.id+'/retry',{method:'POST',body:{attempt:r.attempt}});
  if(version!==epoch)return;
  const source=currentUserMessage();
  if(drafts.get(state.cid)===source?.content)drafts.delete(state.cid);state.localRecord.view={at_latest:true};persistLocalRecovery();
  await refreshMessages(version);if(version!==epoch)return;applyGeneration(data);render();if(state.busy)observeGeneration();
 }catch(error){if(version!==epoch)return;if(handleChatAuth(error)||handleUnavailableSource(error))return;state.busy=false;state.streamError=error.message;render();}
}
async function createCandidate(original,replay=false){
 stopVoice();
 const source=state.messages.find(m=>m.id===original)||{reply_to_id:state.localRecord.candidate_intent?.user_message_id},version=epoch;
 if(!source||state.busy||isUndecided())return;state.busy=true;state.candidateView='new';
 const intent=replay?state.localRecord.candidate_intent:{kind:'regenerate',user_message_id:source.reply_to_id,original_reply_id:original,client_request_id:'candidate_'+newRequestKey()};
 if(!intent){state.busy=false;return;}state.localRecord.candidate_intent=intent;persistLocalRecovery();render();
 try{
  const data=await api('/api/conversations/'+state.cid+'/generations',{method:'POST',body:intent});
  if(version!==epoch)return;delete state.localRecord.candidate_intent;persistLocalRecovery();applyGeneration(data);render();if(state.busy)observeGeneration();
 }catch(error){if(version!==epoch)return;if(handleChatAuth(error)||handleUnavailableSource(error))return;state.busy=false;state.streamError=error.message;render();}
}
async function stopGeneration(){
 const data=state.generation;if(!data||state.stopping)return;
 const version=epoch,oldIntent=state.localRecord.stop_intent;
 const visible=oldIntent?.rid===data.request.id?oldIntent.visible_chars:[...(data.message?.content||'')].length;
 const attempt=oldIntent?.rid===data.request.id?oldIntent.attempt:data.request.attempt;
 state.localRecord.stop_intent={rid:data.request.id,visible_chars:visible,attempt};persistLocalRecovery();
 state.stopping=true;stopObserving();render();
 try{
  const endpoint=data.request.kind==='regenerate'&&data.request.status==='awaiting_choice'?'choice':'stop';
  const body=endpoint==='choice'?{choice:'keep'}:{visible_chars:visible,attempt};
  const result=await api('/api/conversations/'+state.cid+'/generations/'+data.request.id+'/'+endpoint,{method:'POST',body});
  if(version!==epoch)return;state.stopping=false;delete state.localRecord.stop_intent;persistLocalRecovery();applyGeneration(result);render();
 }catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;state.stopping=false;state.streamError='停止结果待核对：'+error.message;render();}
}
async function chooseCandidate(choice){
 stopVoice();
 const r=state.generation?.request,version=epoch;if(!r)return;
 stopObserving();state.busy=true;render();
 try{
  const data=await api('/api/conversations/'+state.cid+'/generations/'+r.id+'/choice',{method:'POST',body:{choice}});
  if(version!==epoch)return;applyGeneration(data);await refreshMessages(version);if(version===epoch){render();refreshMemoryNotice(version);}
 }catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;state.streamError='选择结果待核对，请先查询最终状态。';state.busy=false;render();}
}
async function reconcileGeneration(){
 const version=epoch;
 try{const data=await api(recoveryUrl(true));if(version!==epoch)return;state.streamError=null;applyRecovery(data,false);render();
  if(state.busy&&!state.localRecord.stop_intent)observeGeneration();
 }catch(error){if(version===epoch){if(handleChatAuth(error))return;state.streamError=error.message;render();}}
}

function persistLocalRecovery(){
 if(!state.user||state.conversation?.id!==state.cid)return;
 const record={...state.localRecord,draft:drafts.get(state.cid)||'',pending:pendingMessages.get(state.cid)||null,candidateView:state.candidateView||'new'};
 state.localRecord=record;
 if(typeof recoveryCache!=='undefined'&&!recoveryCache.write(state.user.id,state.cid,record))state.cacheWarning=true;
}
function restoreLocalRecovery(){
 const saved=typeof recoveryCache!=='undefined'?recoveryCache.read(state.user.id,state.cid):{};
 state.localRecord=saved;state.restoreView=saved.view||null;state.candidateView=saved.candidateView||'new';
 if(typeof saved.draft==='string'&&!drafts.has(state.cid))drafts.set(state.cid,saved.draft);
 if(saved.pending&&!pendingMessages.has(state.cid))pendingMessages.set(state.cid,saved.pending);
 retryMessage=pendingMessages.get(state.cid)||null;
}
function recoveryUrl(useAnchor=false){
 const query=new URLSearchParams(),pending=pendingMessages.get(state.cid),record=state.localRecord;
 if(useAnchor&&record.view?.at_latest===false&&record.view.anchor_id)query.set('anchor',record.view.anchor_id);
 if(pending?.client_request_id)query.set('client_request_id',pending.client_request_id);
 if(record.candidate_intent?.client_request_id)query.set('generation_key',record.candidate_intent.client_request_id);
 if(state.useId)query.set('use_id',state.useId);
 return '/api/conversations/'+state.cid+'/recovery'+(query.toString()?'?'+query.toString():'');
}
function applyRecovery(data,restore=false){
 if(voice.messageId&&!data.items.some(m=>m.id===voice.messageId&&voiceEligible(m)&&m.content===voice.content))stopVoice();
 state.messages=data.items;state.nextBefore=data.next_before;state.hasNewer=data.has_newer;
 state.latestUser=data.last_user_message;state.replyRequired=data.reply_required;
 state.generation=data.generation?.request?data.generation:null;state.streamError=null;
 const pending=pendingMessages.get(state.cid),saved=data.submitted_message||(pending?.user_message_id===data.last_user_message?.id?data.last_user_message:null);
 const sourceGeneration=data.submitted_generation||(saved?.id===state.generation?.request?.user_message_id?state.generation:null);
 if(pending&&saved){
  if(sourceGeneration||saved.id!==data.last_user_message?.id||!generationRoleEnabled(data.character_id||state.conversation?.character_id||state.role)){
   pendingMessages.delete(state.cid);retryMessage=null;
   if(drafts.get(state.cid)===pending.content&&(sourceGeneration?.request?.status==='adopted'||saved.id!==data.last_user_message?.id||state.messages.some(m=>m.role==='assistant'&&m.reply_to_id===saved.id&&m.status==='completed')||!generationRoleEnabled(data.character_id||state.conversation?.character_id||state.role)))drafts.delete(state.cid);
  }else{
   retryMessage={...pending,type:'generation',user_message_id:saved.id,generation_key:'reply_'+saved.id,error:'消息已保存，回复尚未开始，可恢复原轮。'};
   pendingMessages.set(state.cid,retryMessage);
  }
 }
 const sourceUnavailable=state.generation?.source_available===false||data.last_user_source_available===false;
 if(sourceUnavailable){
  state.replyRequired=false;
  if(pending&&(saved?.id===data.last_user_message?.id||pending.user_message_id===data.last_user_message?.id)){pendingMessages.delete(state.cid);retryMessage=null;}
  if(state.localRecord.candidate_intent?.user_message_id===data.last_user_message?.id)delete state.localRecord.candidate_intent;
 }
 if(!pending)retryMessage=null;
 if(data.requested_generation?.request)delete state.localRecord.candidate_intent;
 if(state.localRecord.candidate_intent)state.streamError='候选创建结果尚未确认，请先核对或重发原请求。';
 const r=state.generation?.request,stop=state.localRecord.stop_intent;
 if(stop){
  if(!r||r.id!==stop.rid||r.attempt!==stop.attempt||['stopped','failed','kept'].includes(r.status)||state.generation.message?.status==='replaced'){
   delete state.localRecord.stop_intent;
  }else{
   state.streamError='上次停止尚未确认，界面保持冻结。请重试停止或核对状态。';
   if(state.generation.message){
    state.generation.message.content=[...state.generation.message.content].slice(0,stop.visible_chars).join('');
    const shown=state.messages.find(m=>m.id===state.generation.message.id);if(shown)shown.content=state.generation.message.content;
   }
  }
 }
 state.busy=!!r&&['queued','generating'].includes(r.status);
 if(r?.kind==='reply'&&['failed','stopped'].includes(r.status)&&!drafts.get(state.cid)&&data.last_user_message?.id===r.user_message_id){drafts.set(state.cid,data.last_user_message.content);}
 if(restore&&!data.anchor_found&&state.restoreView?.at_latest===false){state.restoreView=null;state.localRecord.view={at_latest:true};}
 persistLocalRecovery();
}
function conversationStateLabel(status){const label={queued:'回复等待中',generating:'回复生成中',awaiting_choice:'有待选择的候选',stopped:'回复已停止，可继续',failed:'回复未完成，可恢复'}[status];return label?'<small>'+label+'</small>':'';}
function messageTime(timestamp){
 if(typeof timestamp!=='number'||!Number.isFinite(timestamp))return '';
 const date=new Date(timestamp*1000);
 return '<time datetime="'+date.toISOString()+'">'+new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}).format(date)+'</time>';
}
function saveReadingPosition(){
 if(state.page!=='chat'||state.loading||!state.user)return;
 const area=$('#chat-scroll');if(!area)return;
 const top=area.getBoundingClientRect().top;
 const anchor=[...area.querySelectorAll('.message')].find(el=>el.getBoundingClientRect().bottom>top+8);
 state.localRecord.view={at_latest:!state.hasNewer&&area.scrollHeight-area.scrollTop-area.clientHeight<90,
  anchor_id:anchor?.id||null,anchor_offset:anchor?anchor.getBoundingClientRect().top-top:0};
 $('#latest-read')?.toggleAttribute('hidden',state.localRecord.view.at_latest);
 persistLocalRecovery();
}
function updateComposerFeedback(input){
 if(!input)return;const count=[...(typeof input.value==='string'?input.value:'').trim()].length,hint=$('#input-limit'),send=$('#message-form [aria-label="发送"]');
 if(hint){hint.hidden=count<900;hint.textContent=count>1000?'已输入 '+count+' / 1000 字，请缩短后发送，内容已保留。':count+' / 1000 字';hint.classList.toggle('danger',count>1000);}
 if(send)send.disabled=count===0||count>1000||state.busy||isUndecided()||!!state.streamError;
}
function wireChatView(){
 updateComposerFeedback($('#draft'));
 if(typeof sizeComposer==='function')sizeComposer($('#draft'));
 const area=$('#chat-scroll'),view=state.restoreView||state.localRecord.view;
 if(view?.at_latest===false){
  const anchor=document.getElementById(view.anchor_id);
  if(anchor)area.scrollTop+=anchor.getBoundingClientRect().top-area.getBoundingClientRect().top-(view.anchor_offset||0);
 }else area.scrollTop=area.scrollHeight;
 state.restoreView=null;
 const cid=state.cid,user=state.user?.id,version=epoch;
 $('#draft').addEventListener('input',e=>{
  if(version!==epoch||state.cid!==cid||state.user?.id!==user)return;
  drafts.set(cid,e.target.value);persistLocalRecovery();updateComposerFeedback(e.target);if(typeof sizeComposer==='function')sizeComposer(e.target,!state.hasNewer&&state.localRecord.view?.at_latest!==false);
 });
 area.addEventListener('scroll',saveReadingPosition,{passive:true});
}
async function ensureConversationUse(signal){
 const cid=state.cid;
 if(!usesInDocument.has(cid)){if(!documentIsReload)storage.delete('weiwan-use:'+cid);usesInDocument.add(cid);}
 const old=storage.get('weiwan-use:'+cid);let use;
 try{use=await api('/api/conversations/'+cid+'/uses',{method:'POST',body:old?{use_id:old}:{},signal});}
 catch(error){if(error.code!=='use_expired')throw error;use=await api('/api/conversations/'+cid+'/uses',{method:'POST',body:{},signal});}
 state.useId=use.id;storage.set('weiwan-use:'+cid,use.id);
}
function relationshipUrl(){return '/api/conversations/'+state.cid+'/relationships'+(state.useId?'?use_id='+state.useId:'');}
function relationPendingKey(){return 'weiwan-rel-pending:'+state.user?.id+':'+state.cid;}
function relationshipHTML(c){
 const data=state.relationship||{},s=data.state||{},labels={unspecified:'未约定',friends:'朋友',romantic:'恋爱',new:'初识',familiar:'熟悉',close:'亲近'};
 let html=header('与'+c.character.name+'的关系','chat')+'<div class="scroll pad"><section class="relation-summary"><h2>'+labels[s.relationship_type]+' · '+labels[s.familiarity]+'</h2><p>相处节奏：'+esc(s.pace||'保守自然，尊重当次选择')+'</p><p>'+(s.intimacy_paused?'亲密表达已暂停。':'具体亲密行为仍需当次明确同意。')+'</p><small>当前显示含本次临时决定；长期关系：'+labels[s.persistent_type]+'。未保存决定不会跨次沿用。</small></section>';
 html+='<div class="record-actions">'+button('确认关系','relation-choice','set_relation')+button('调整熟悉程度','relation-choice','familiarity','secondary')+button('确认分歧修复','relation-choice','repair_event','secondary')+button('节奏与话题','relation-choice','pace','secondary')+button('结束恋爱 / 退回朋友','relation-choice','revoke','secondary')+button(s.intimacy_paused?'恢复亲密表达的确认':'暂停亲密表达','relation-choice',s.intimacy_paused?'resume_intimacy':'pause','secondary')+'</div>';
 html+='<p class="notice">不保存结束事件，也不会恢复旧恋爱关系。没有消息数升级或好感度分数。</p>';
 if(state.proactive)html+='<article class="record"><small>主动消息 · '+(state.proactive.enabled?'已开启':'已关闭')+'</small><h3>隔了一阵再回来时，'+esc(c.character.name)+'先说一句</h3><p>只在你打开聊天时出现，一天最多一句，夜里 23 点到早上 8 点不出现。不会发通知，也不会在你离开时生成。</p><div class="record-actions">'+button(state.proactive.enabled?'关闭主动消息':'开启主动消息','proactive-toggle','','secondary')+'</div></article>';
 if(state.relationPending)html+='<div class="error">上次操作结果待核对，先刷新状态或重试原决定。'+button('重试原决定','relation-retry','','secondary')+'</div>';
 html+=archiveHTML();
 html+='<h3>关系事件与依据</h3>';
 const kinds={relationship:'关系确认',revocation:'授权撤回',promise:'约定',experience:'共同经历',boundary:'边界',repair:'双向了解'},statuses={proposed:'待确认',pending_review:'待核对',accepted:'已接受',completed:'已完成',invalidated:'已失效',cancelled:'已取消'};
 if(!data.events?.length)html+=empty('故事刚刚开始','只依据有效事件与明确决定延续，不因聊天次数建立关系。');
 for(const e of data.events||[]){html+='<article class="record"><small>'+esc(e.kind==='repair'&&!e.target_familiarity?'分歧与修复':kinds[e.kind]||e.kind)+' · '+esc(statuses[e.status]||e.status)+' · '+(e.long_term?'已保存':'仅本次')+'</small><h3>'+esc(e.content.replaceAll('romantic','恋爱').replaceAll('friends','朋友').replaceAll('unspecified','未约定').replaceAll('familiar','熟悉').replaceAll('close','亲近').replaceAll('new','初识'))+'</h3>'+messageTime(e.occurred_at);
 for(const m of e.sources||[])html+='<details><summary>查看来源</summary><p class="prewrap">'+esc(m.content)+'</p>'+button('回到这条聊天','memory-source',m.id,'text-action')+'</details>';
 if(['accepted','completed'].includes(e.status))html+='<div class="record-actions">'+(!e.long_term?button('保存供下次续聊','relation-save-event',e.id,'secondary'):'')+(!['relationship','revocation'].includes(e.kind)?button('纠正','relation-edit-event',e.id,'secondary'):'')+button('删除','relation-delete-event',e.id,'text-action')+'</div>';
 html+='</article>';}
 const available=(state.relationMemories||[]).filter(m=>m.status==='confirmed'&&['promise','experience','boundary'].includes(m.kind)&&!(data.events||[]).some(e=>e.source_memory_id===m.id&&['accepted','completed'].includes(e.status)));
 if(available.length)html+='<h3>已确认记忆中的事件</h3>'+available.map(m=>'<article class="record"><p>'+esc(m.content)+'</p>'+button('确认保存到关系记录','relation-import',m.id,'secondary')+'</article>').join('');
 return html+'</div>';
}
function openRelationshipForm(action){
 const s=state.relationship.state;let body='',label='明确确认';
 if(action==='set_relation')body='<label class="field">双方认可的关系<select id="relation-target" required><option value="" selected disabled>请选择关系，不默认建立</option><option value="friends">朋友</option><option value="romantic">恋爱</option></select></label><p>此确认不代表同意任何具体亲密行为。</p>';
 if(action==='revoke'){body='<p>旧恋爱授权立即失效，不依赖是否保存结束事件。熟悉程度和其他有效经历保留。</p><label class="field">结束后的关系<select id="relation-target"><option value="unspecified">未约定</option><option value="friends">退回朋友</option></select></label>';label='确认结束旧恋爱';}
 if(action==='pace')body='<label class="field">希望的相处节奏<select id="relation-pace"><option>慢一点</option><option>暂停这个话题</option><option>先听，不提供建议</option></select></label><p>暂停话题或拒绝提议不自动改变关系类型。</p>';
 if(action==='familiarity'){body='<label class="field">熟悉程度<select id="relation-target" required><option value="" selected disabled>请选择并核对依据</option><option value="familiar">熟悉</option><option value="close">亲近</option><option value="new">初识</option></select></label><p>请选择双方分享及后来承接同一具体话题的有效来源；连续问候不能作为依据。</p><div class="source-picker">'+(state.relationSources||[]).filter(m=>m.role!=='system'&&['saved','completed'].includes(m.status)).map(m=>'<label><input type="checkbox" name="relation-source" value="'+m.id+'">'+(m.role==='user'?'你：':'角色：')+esc(m.content.slice(0,180))+'</label>').join('')+'</div><label><input type="checkbox" id="relation-evidence">我确认来源体现双方了解及后续承接</label><label><input type="checkbox" id="relation-emotion">若选择亲近，我明确愿意增加情感表达</label>';}
 if(action==='repair_event')body='<label class="field">已接受修复的用户原话<textarea id="relation-content" minlength="2" maxlength="400" required></textarea></label><p>仅核对已经说清或接受道歉的事件，不自动改变关系或熟悉程度。</p><div class="source-picker">'+(state.relationSources||[]).filter(m=>m.role!=='system'&&['saved','completed'].includes(m.status)).map(m=>'<label><input type="checkbox" name="relation-source" value="'+m.id+'">'+(m.role==='user'?'你：':'角色：')+esc(m.content.slice(0,180))+'</label>').join('')+'</div><label><input id="relation-repair" type="checkbox">我确认来源显示双方已接受修复</label>';
 if(action==='pause'){body='<p>暂停亲密表达立即生效并保留限制，关系类型不自动改变。</p>';label='确认暂停亲密表达';}
 if(action==='resume_intimacy'){body='<p>仅解除暂停标识，不同意任何具体亲密行为；之后仍尊重每一次拒绝。</p>';label='明确同意解除暂停';}
 if(!['pause','resume_intimacy'].includes(action))body+='<label><input id="relation-store" type="checkbox">'+(action==='revoke'?'保存结束事件；退回朋友时也保存新的朋友关系':'保存供下次续聊')+'</label>';
 modal('关系与节奏确认','<form id="relation-form" data-action-type="'+action+'">'+body+'<p>关闭或取消不算同意。</p><div id="relation-error"></div><button class="primary wide">'+label+'</button></form>');
}
async function submitRelationshipForm(form){
 const action=form.dataset.actionType;let body={action};
 if(form.id==='relation-edit-form')body={action:'edit_event',event_id:form.dataset.event,event_revision:Number(form.dataset.revision),content:$('#relation-content').value};
 else{body.store=!!$('#relation-store')?.checked;if($('#relation-target'))body.target=$('#relation-target').value;if(action==='pace')body.pace=$('#relation-pace').value;if(action==='repair_event'){body.content=$('#relation-content').value;body.confirm_repair=!!$('#relation-repair')?.checked;body.source_ids=[...form.querySelectorAll('input[name="relation-source"]:checked')].map(x=>x.value);}if(action==='familiarity'){body.source_ids=[...form.querySelectorAll('input[name="relation-source"]:checked')].map(x=>x.value);body.confirm_evidence=!!$('#relation-evidence')?.checked;body.allow_emotional_expression=!!$('#relation-emotion')?.checked;}}
 await relationshipOperation(body,form.querySelector('button'));
}
async function relationshipOperation(body,el){
 const version=epoch;el.disabled=true;const original=el.textContent;el.textContent='处理中…';
 try{
  if(state.relationPending&&body)throw new Error('上次决定结果待核对，请重试原决定或刷新核对后再操作。');
  const request=state.relationPending||{...body,revision:state.relationship.state.revision,use_id:state.useId,client_request_id:'rel_'+newRequestKey()};
  state.relationPending=request;storage.set(relationPendingKey(),JSON.stringify(request));
  await api('/api/conversations/'+state.cid+'/relationships',{method:'POST',body:request});if(version!==epoch)return;
  storage.delete(relationPendingKey());state.relationPending=null;closeModal();await loadRoute();toast('关系决定已更新');
 }catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;if(error.status&&error.status<500){storage.delete(relationPendingKey());state.relationPending=null;}
 const field=$('#relation-error');if(field)field.innerHTML='<div class="error">'+esc(error.message)+(state.relationPending?button('重试原决定','relation-retry','','secondary'):'')+'</div>';else{toast(error.message);if(state.page==='relationship'&&state.relationPending)render();}el.disabled=false;el.textContent=original;}
}
async function refreshMemoryNotice(version=epoch,tries=0){
 if(!state.capabilities.memory_write||state.page!=='chat'||version!==epoch)return;
 try{const info=await api('/api/conversations/'+state.cid+'/memories');if(version!==epoch||state.page!=='chat')return;
 state.memory=info.items;state.memoryInfo=info;const count=info.items.filter(m=>m.status!=='confirmed').length,area=$('#memory-notice');
 if(area)area.innerHTML=count?'<div class="hint">有 '+count+' 条待确认记忆。'+button('查看记忆','memory','','text-action')+'</div>':'';
 if(tries<15&&(tries<2||info.jobs?.some(j=>j.status==='running')))setTimeout(()=>refreshMemoryNotice(version,tries+1),1500);
 }catch(error){if(version===epoch)handleChatAuth(error);}
}
function memoryHTML(c){
 const info=state.memoryInfo||{},tab=state.memoryTab||'pending',pending=state.memory.filter(m=>m.status!=='confirmed'),saved=state.memory.filter(m=>m.status==='confirmed');
 const labels={user_fact:'用户事实',boundary:'互动边界',promise:'约定',experience:'共同经历'};
 let html=header('与'+c.character.name+'的记忆','chat')+'<div class="scroll pad"><div class="auth-switch">'+button('待确认（'+pending.length+'）','memory-tab','pending',tab==='pending'?'primary':'secondary')+button('已记住（'+saved.length+'）','memory-tab','saved',tab==='saved'?'primary':'secondary')+button(esc(c.character.name)+'心中的我','memory-tab','portrait',tab==='portrait'?'primary':'secondary')+'</div>';
 if(tab==='portrait')return html+portraitHTML(c)+'</div>';
 html+='<p class="stage-label">仅属于当前账号与当前角色。'+(info.embedding_status==='not_configured'?'向量模型未配置；确认可保存，索引就绪后才能用于生成。':'')+'</p>';
 html+='<div class="record-actions">'+button('整理有效对话','memory-extract','','secondary')+button('刷新状态','memory-refresh','','secondary')+'</div>';
 if(info.jobs?.some(j=>j.status==='running'))html+='<p class="hint">正在整理候选，完成后刷新查看。</p>';
 const failedJobs=(info.jobs||[]).filter(j=>j.status==='failed');const skippedJobs=failedJobs.filter(j=>j.error_code==='memory_source_invalid');const retryableJobs=failedJobs.filter(j=>j.error_code!=='memory_source_invalid');
 if(retryableJobs.length)html+='<p class="error">部分候选整理未完成，可重试“整理有效对话”。已保存记忆不受影响。</p>';
 if(skippedJobs.length)html+='<p class="hint">'+skippedJobs.length+' 条候选因来源回复已替换或删除而跳过，无需处理。</p>';
 const records=tab==='pending'?pending:saved;
 if(!records.length)html+=empty(tab==='pending'?'没有待确认记忆':'暂时没有有效记忆','不会把普通闲聊或未确认候选当作长期事实。');
 // Roadmap 1.9: saved memories grouped into a memory box by kind; story facts kept apart.
 const box=tab==='saved'?[['关于你',x=>x.kind==='user_fact'&&x.fact_scope!=='story'],['相处的边界',x=>x.kind==='boundary'],['我们的约定',x=>x.kind==='promise'],['一起经历的',x=>x.kind==='experience'&&x.fact_scope!=='story'],['故事里',x=>x.fact_scope==='story'&&!['boundary','promise'].includes(x.kind)]]:[['',()=>true]];
 const placed=new Set();
 for(const [title,test] of box){
 const group=records.filter(x=>!placed.has(x.id)&&test(x));group.forEach(x=>placed.add(x.id));
 if(!group.length)continue;
 if(title)html+='<h3 class="box-title">'+title+'<small>'+group.length+'</small></h3>';
 for(const m of group){
  html+='<article class="record"><small>'+esc(labels[m.kind]||m.kind)+' · '+(m.source_type==='activity'?'线上共读经历':m.fact_scope==='story'?'故事内事实 · '+esc(m.story_scene):'现实用户事实')+'</small><h3>'+esc(m.content)+'</h3><small>'+messageTime(m.updated_at)+'</small>';
  if(m.conflict_content)html+='<div class="notice">原记忆：'+esc(m.conflict_content)+'<br>新候选：'+esc(m.content)+'<br>确认将替换原记忆。</div>';
  if(m.expires_note)html+='<p class="notice">'+esc(m.expires_note)+'</p>';
  if(m.recall_suppressed)html+='<p class="notice warning">因你纠正了「'+esc(m.recall_suppressed.cause_content)+'」，这条相近的记忆已暂停用于聊天。仍保留在这里，可以随时恢复。</p>';
  if(m.activity_id)html+='<div class="record-actions">'+button('回到共读活动','activity-open',m.activity_id,'text-action')+'</div>';
  for(const source of m.sources||[])html+='<details><summary>来源 · '+messageTime(source.created_at)+'</summary><p class="prewrap">'+esc(source.quote||source.content)+'</p>'+button('回到这条聊天','memory-source',source.message_id,'text-action')+'</details>';
  html+='<div class="record-actions">';
  if(m.status!=='confirmed')html+=button(m.conflict_content?'确认替换':'确认保存','memory-confirm',m.id)+button('不保存','memory-reject',m.id,'secondary');
  html+=button('修改','memory-edit',m.id,'secondary')+button('历史版本','memory-history',m.id,'secondary')+button('删除','memory-delete',m.id,'text-action');
  if(m.recall_suppressed)html+=button('恢复用于聊天','memory-release',m.id,'secondary');
  if(m.status==='confirmed'&&m.index_status!=='ready')html+='<p class="notice">'+({pending:'索引处理中，请稍后刷新',failed:esc(m.index_error_message||'索引失败，完成重试前暂停新回复'),unconfigured:'向量模型未配置'}[m.index_status]||'索引待处理')+'</p>'+button('重试索引','memory-reindex',m.id,'secondary');
  html+='</div></article>';
 }}
 return html+'</div>';
}
// Roadmap 1.9: 「Ta 心中的我」. Reading is free; generating is one explicit model call.
function portraitHTML(c){
 const p=state.portrait;
 if(!p)return '<p class="hint">正在读取…</p>';
 let html='<p class="stage-label">用'+esc(c.character.name)+'的口吻，只根据你已确认的记忆来写；对不上记忆的句子会被丢掉。删除或修改记忆后，相关的句子会立刻隐去。</p>';
 if(p.sentences.length)html+='<article class="record portrait">'+p.sentences.map(s=>'<p>'+esc(s.text)+'</p>').join('')+'<small>写于'+messageTime(p.generated_at)+'</small></article>';
 if(p.stale&&p.generated_at)html+='<p class="notice">记忆有了变化，可以重新写一次。</p>';
 if(p.eligible_memories<2)return html+empty('还没到时候','已确认的记忆还太少，多聊一些再来看。');
 return html+'<div class="record-actions">'+button(p.generated_at?'重新写一次':'让'+esc(c.character.name)+'写一写','portrait-generate')+'</div><p class="hint">每次生成会调用一次模型。</p>';
}
async function generatePortrait(el){
 const version=epoch;el.disabled=true;el.textContent='正在写…';
 try{const p=await api('/api/conversations/'+state.cid+'/portrait',{method:'POST',body:{}});if(version!==epoch)return;state.portrait=p;render();if(p.dropped)toast('有 '+p.dropped+' 句对不上已确认的记忆，已去掉');}
 catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;toast(error.message);el.disabled=false;el.textContent='再试一次';}
}
async function changeMemory(id,operation,el){
 const version=epoch,m=state.memory.find(x=>x.id===id);if(!m)return;
 el.disabled=true;const text=el.textContent;el.textContent='处理中…';
 try{await api('/api/conversations/'+state.cid+'/memories/'+id,{method:'POST',body:{action:operation,revision:m.revision,...(operation==='confirm'&&m.conflict_id?{replace_conflict:true}:{})}});if(version!==epoch)return;closeModal();await loadRoute();toast(operation==='release_suppression'?'已恢复用于聊天':operation==='reindex'?'已提交索引任务，请刷新核对结果':operation==='delete'?'已删除，后续不再使用':operation==='reject'?'已选择不保存':'记忆已更新');}
 catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;const field=$('#memory-error');if(field)field.innerHTML='<div class="error">'+esc(error.message)+'</div>';else toast(error.message);el.disabled=false;el.textContent=text;}
}
// Roadmap 1.7: in-chat correction. Lists the saved memories this reply was generated with;
// fixing one goes through the normal memory edit path, so it takes effect for the next reply.
const KIND_LABEL={user_fact:'用户事实',boundary:'互动边界',promise:'约定',experience:'共同经历'};
async function openWrongReply(mid){
 const version=epoch;modal('哪里说错了？','<p>正在查看这条回复用到了哪些记忆…</p>');
 const data=await api('/api/conversations/'+state.cid+'/messages/'+mid+'/recalled');if(version!==epoch)return;
 state.wrongItems=data.items;state.wrongReply=mid;
 let body='';
 if(data.items.length){
  body+='<p>这条回复用到了下面的记忆。哪条不对，直接改成正确的说法，之后的回复就按新的来。</p>';
  for(const m of data.items)body+='<div class="record"><small>'+esc(KIND_LABEL[m.kind]||m.kind)+'</small><label class="field">改成<textarea id="wrong-'+esc(m.id)+'" minlength="2" maxlength="400">'+esc(m.content)+'</textarea></label><div id="wrong-result-'+esc(m.id)+'"></div><div class="record-actions">'+button('改正这条','wrong-fix',m.id)+'</div></div>';
 }else body+='<p>这条回复没有用到已保存的记忆，可能是角色即兴说错了。你可以直接在对话里告诉他，或者删掉这条回复。</p>';
 modal('哪里说错了？',body+'<div id="wrong-error"></div>',button('在对话里告诉他','wrong-tell','','secondary')+button('删除这条回复','delete-message',mid,'secondary'));
}
async function fixRecalledMemory(id,el){
 const version=epoch,m=(state.wrongItems||[]).find(x=>x.id===id);if(!m)return;
 const content=$('#wrong-'+id)?.value.trim()||'';if(content===m.content){toast('内容没有变化');return}
 el.disabled=true;el.textContent='改正中…';
 try{const r=await api('/api/conversations/'+state.cid+'/memories/'+id,{method:'POST',body:{action:'edit',revision:m.revision,content}});if(version!==epoch)return;
  m.revision=r.revision;m.content=content;
  const box=$('#wrong-result-'+id);if(box)box.innerHTML='<p class="notice">'+(r.status==='confirmed'?'已改正为「'+esc(content)+'」，下一条回复起生效；相近的旧记忆也会暂停使用。':'已改为「'+esc(content)+'」，约定和经历的改动需要到记忆页确认后生效。')+'</p>';
  el.textContent='已改正';}
 catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;const f=$('#wrong-error');if(f)f.innerHTML='<div class="error">'+esc(error.message)+'</div>';el.disabled=false;el.textContent='重试改正';}
}
// Roadmap 1.14: earlier versions of a memory; restoring is an ordinary edit back to that text.
async function openMemoryHistory(id){
 const version=epoch;const data=await api('/api/conversations/'+state.cid+'/memories/'+id+'/history');if(version!==epoch)return;
 const current=state.memory.find(x=>x.id===id);
 const rows=data.items.filter(v=>v.content!==current?.content).map(v=>'<div class="record"><small>第 '+v.revision+' 版'+messageTime(v.created_at)+'</small><h3>'+esc(v.content)+'</h3><div class="record-actions">'+button('恢复这个版本','memory-restore',id+':'+v.revision,'secondary')+'</div></div>').join('');
 modal('历史版本',(rows||'<p>还没有修改过，没有更早的版本。</p>')+'<div id="memory-error"></div>');
 state.memoryHistory={id,items:data.items};
}
async function restoreMemoryVersion(id,revision,el){
 const version=epoch,m=state.memory.find(x=>x.id===id),v=state.memoryHistory?.items.find(x=>x.revision===revision);if(!m||!v)return;
 el.disabled=true;el.textContent='恢复中…';
 try{await api('/api/conversations/'+state.cid+'/memories/'+id,{method:'POST',body:{action:'edit',revision:m.revision,content:v.content}});if(version!==epoch)return;closeModal();await loadRoute();toast('已恢复');}
 catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;const f=$('#memory-error');if(f)f.innerHTML='<div class="error">'+esc(error.message)+'</div>';el.disabled=false;el.textContent='重试恢复';}
}
async function saveMemoryEdit(form){
 const version=epoch,m=state.editingMemory;if(!m)return;const submit=form.querySelector('button');submit.disabled=true;submit.textContent='保存中…';
 const until=$('#memory-until').value;
 try{await api('/api/conversations/'+state.cid+'/memories/'+m.id,{method:'POST',body:{action:'edit',revision:m.revision,content:$('#memory-content').value,valid_until:until?Math.floor(new Date(until).getTime()/1000):null}});if(version!==epoch)return;if(form.isConnected===false){toast('修改已保存，请刷新记忆列表核对。');return;}closeModal();await loadRoute();toast('修改已保存');}
 catch(error){if(version!==epoch)return;if(handleChatAuth(error))return;const field=form.isConnected===false?null:$('#memory-error');if(field)field.innerHTML='<div class="error">'+esc(error.message)+'</div>';else toast('记忆修改未完成：'+error.message);submit.disabled=false;submit.textContent='重试保存';}
}
document.addEventListener('compositionstart',e=>{if(e.target.id==='draft')composingCid=state.cid;});
document.addEventListener('compositionend',e=>{
 if(e.target.id!=='draft')return;const cid=composingCid;composingCid=null;
 if(cid===state.cid&&state.page==='chat'){drafts.set(cid,e.target.value);persistLocalRecovery();if(delayedChatRender){delayedChatRender=false;render();}}
});
window.addEventListener('appviewportchange',()=>{
 if(state.page==='chat'&&!state.loading&&!state.error&&state.localRecord.view?.at_latest!==false){const area=$('#chat-scroll');if(area)area.scrollTop=area.scrollHeight;}
 const focused=document.activeElement;if(focused&&$('#modal .sheet')?.contains(focused))focused.scrollIntoView({block:'nearest'});
});
window.addEventListener('pagehide',()=>{stopVoice();saveReadingPosition();persistLocalRecovery();});

// MVP 1.1: online shared reading. All durable progress comes from the server.
const activityDrafts=new Map(),activityPending=new Map();
let activityBusy=false;
function activityKey(){return 'weiwan-activity:'+state.user?.id+':'+state.cid+':'+(state.activityId||'start');}
function restoreActivityPending(){const key=activityKey();try{const saved=JSON.parse(storage.get(key)||'null');if(saved)activityPending.set(key,saved);}catch{}}
function activityStatus(status){return {active:'阅读中',paused:'已暂离 · 尚未完成',completed:'已读完'}[status]||'状态待核对';}
function activityHTML(c){
 const a=state.activity,pending=activityPending.get(activityKey());
 let activityHeader=header('和'+c.character.name+'一起读','chat');if(a?.status==='active')activityHeader=activityHeader.replace('data-action="navigate" data-value="chat"','data-action="activity-pause"');
 let html=activityHeader+'<div class="scroll pad activity-page">';
 if(!a){const info=state.activityList||{},article=info.article||{};
  html+='<section class="reading-intro"><div class="eyebrow">A SHARED PAGE</div><h1>一起读一篇短文</h1><p>《'+esc(article.title||'短文')+'》 · '+esc(article.total_paragraphs||'—')+' 段</p><p>读到哪一段，由你决定。可以聊聊眼前的文字，也可以随时暂离，下次接着读。</p><small>'+esc(article.source||'')+'</small></section>';
  html+='<div class="record-actions">'+button('开始共读','activity-start')+button('取消，返回聊天','navigate','chat','secondary')+'</div>';
  if(info.items?.length)html+='<section class="section"><h3>此前的共读</h3>'+info.items.map(x=>'<article class="record"><h3>'+esc(x.title)+'</h3><p>'+activityStatus(x.status)+' · 第 '+(x.current_index+1)+' / '+x.total_paragraphs+' 段</p>'+messageTime(x.updated_at)+button(x.status==='completed'?'回看活动':'回到共读','activity-open',x.id,'secondary')+'</article>').join('')+'</section>';
 }else{
  html+='<section class="reading-title"><div class="eyebrow">'+esc(c.character.name)+' · 线上共读</div><h1>'+esc(a.title)+'</h1><p>'+activityStatus(a.status)+' · 第 '+(a.current_index+1)+' / '+a.total_paragraphs+' 段</p></section>';
  html+='<div class="reading-progress" aria-label="已开放 '+(a.current_index+1)+' 段，共 '+a.total_paragraphs+' 段">'+Array.from({length:a.total_paragraphs},(_,i)=>'<span class="'+(i<=a.current_index?'opened':'')+'"></span>').join('')+'</div>';
  html+='<section class="reading-text" aria-label="已开放的正文">'+(a.visible_paragraphs||[]).map((text,i)=>'<p><small>第 '+(i+1)+' 段</small>'+esc(text)+'</p>').join('')+'</section>';
  html+='<div class="record-actions">';
  if(a.status==='active')html+=(a.current_index+1<a.total_paragraphs?button('继续下一段','activity-next'):button('我读完了，结束共读','activity-finish'))+button('暂离并返回聊天','activity-pause','','secondary');
  if(a.status==='paused')html+=button('继续这次共读','activity-resume')+button('返回聊天','navigate','chat','secondary');
  if(a.status==='completed')html+=button('返回聊天','navigate','chat','secondary');
  html+='</div>';
  if(a.memory_id)html+='<div class="notice activity-memory">活动记录与长期记忆分别保存；只有你已确认、仍然有效的记忆用于后续续聊。'+button('查看或管理活动记忆','memory','','text-action')+'</div>';
  html+='<section class="section"><h3>围绕已读内容聊聊</h3>';
  if(!a.messages?.length)html+='<p class="muted">还没有交流记录。</p>';
  for(const m of a.messages||[])html+='<article class="message '+(m.role==='user'?'user':'')+'"><div class="bubble">'+esc(m.content||'本次回复未完成')+'</div><div class="meta">'+(m.role==='user'?'你':esc(c.character.name))+' · '+({completed:'已完成',saved:'已保存',failed:'回复失败',pending:'正在回复'}[m.status]||esc(m.status))+messageTime(m.created_at)+'</div>'+(m.role==='assistant'&&m.status==='failed'&&a.status==='active'?button('重试这条回复','activity-retry-reply',m.id,'secondary'):'')+'</article>';
  html+='</section>';
  if(a.status==='active')html+='<form id="activity-message-form"><label class="field">想聊聊哪一句？<textarea id="activity-draft" maxlength="1000" required placeholder="写下你的想法…">'+esc(activityDrafts.get(a.id)||'')+'</textarea></label><button class="primary wide" '+(activityBusy||pending?'disabled':'')+'>'+ (activityBusy?'正在回应…':'发送')+'</button></form>';
  if(a.generation_available===false&&a.status==='active')html+='<p class="notice">角色回复服务暂不可用，阅读进度仍可继续。</p>';
 }
 if(pending)html+='<div class="error">上次操作结果尚待核对，请重试原请求。'+button('核对并重试原操作','activity-retry','','secondary')+'</div>';
 if(activityBusy)html+='<p class="notice" role="status">正在保存，请稍候…</p>';
 html+='</div>';
 // Disable all mutations while awaiting one result; replay retains the original key.
 if(activityBusy||pending)html=html.replace(/(<button[^>]*data-action="activity-(?:start|next|resume|pause|finish|retry-reply)"[^>]*)(>)/g,'$1 disabled$2');
 return html;
}
async function sendActivityMessage(form){
 if(activityBusy)return;const input=form.querySelector('textarea'),text=input.value.trim();
 if(!text||[...text].length>1000){toast('请输入 1—1000 字');return;}
 activityDrafts.set(state.activityId,input.value);await activityOperation('message',form.querySelector('button'),text);
}
async function activityOperation(operation,el,content,retryMessageId){
 if(activityBusy)return;
 const version=epoch,cid=state.cid,aid=state.activityId,key=activityKey();
 let request=activityPending.get(key);
 if(request&&operation){toast('请先核对上次操作结果');return;}
 if(!request){if(!operation)return;request={path:'/api/conversations/'+cid+'/activities'+(operation==='start'?'':'/'+aid)+(operation==='message'?'/messages':''),body:{client_request_id:'act_'+newRequestKey(),...(operation==='start'?{}:{revision:state.activity.revision}),...(operation==='message'?{content,use_id:state.useId,...(retryMessageId?{retry_message_id:retryMessageId}:{})}:operation==='start'?{}:{action:operation})},operation};activityPending.set(key,request);storage.set(key,JSON.stringify(request));}
 activityBusy=true;render();
 try{
  const data=await api(request.path,{method:'POST',body:request.body});
  activityPending.delete(key);storage.delete(key);
  if(version!==epoch)return;
  activityBusy=false;state.activity=data;
  if(request.operation==='message'&&activityDrafts.get(aid)?.trim()===request.body.content)activityDrafts.delete(aid);
  if(request.operation==='start'){state.activityId=data.id;await navigate('activity');}
  else if(request.operation==='pause'){await navigate('chat');toast('进度已保存，下次可以继续');}
  else render();
 }catch(error){
  if(error.status&&error.status<500&&error.status!==408){activityPending.delete(key);storage.delete(key);}
  if(version!==epoch)return;activityBusy=false;if(handleChatAuth(error))return;
  if(error.status===409&&aid){try{state.activity=await api('/api/conversations/'+cid+'/activities/'+aid);}catch{}}
  if(version!==epoch)return;render();toast(error.message);
 }
}
document.addEventListener('input',e=>{if(e.target.id==='activity-draft'&&state.activityId)activityDrafts.set(state.activityId,e.target.value);});
function archiveHTML(){
 if(state.archiveError)return '<section class="section"><h3>共同经历与约定</h3><p class="error">档案未能加载：'+esc(state.archiveError)+'</p>'+button('重新加载档案','reload','','secondary')+'</section>';
 const data=state.archive;if(!data)return '';
 let html='<section class="section"><h3>共同经历</h3>';
 if(!data.activities?.length&&!data.experiences?.length)html+='<p class="muted">还没有可回看的共同经历。</p>';
 for(const a of data.activities||[])html+='<article class="record"><small>线上共读 · '+activityStatus(a.status)+'</small><h3>'+esc(a.title)+'</h3><p>第 '+(a.current_index+1)+' / '+a.total_paragraphs+' 段</p>'+messageTime(a.updated_at)+'<div class="record-actions">'+button('回到活动','activity-open',a.id,'secondary')+(a.memory_id?button('管理相关记忆','memory','','text-action'):'')+'</div></article>';
 for(const e of (data.experiences||[]).filter(e=>e.source?.type!=='activity'))html+='<article class="record"><small>'+esc({completed:'已完成',accepted:'已确认',pending_review:'待核对',active:'参与中',paused:'已暂离',pending:'待确认',withdrawn:'已撤回'}[e.status]||'待核对')+'</small><h3>'+esc(e.content||'')+'</h3>'+messageTime(e.created_at||e.occurred_at)+(e.source?.type==='activity'?button('回到活动','activity-open',e.source.id,'text-action'):e.source?.id?button('回到来源','memory-source',e.source.id,'text-action'):'')+'</article>';
 html+='</section><section class="section"><h3>约定</h3>';
 if(!data.promises?.length)html+='<p class="muted">暂无已记录的约定。</p>';
 for(const p of data.promises||[])html+='<article class="record"><small>'+esc({pending:'待履行',completed:'已完成',withdrawn:'已撤回'}[p.status]||'待核对')+'</small><h3>'+esc(p.content)+'</h3>'+messageTime(p.created_at)+(p.source?.type==='activity'?button('回到活动','activity-open',p.source.id,'text-action'):p.source?.id?button('回到来源','memory-source',p.source.id,'text-action'):'')+(p.memory_id&&p.status==='pending'?'<div class="record-actions">'+button('确认已履行','promise-complete',p.memory_id,'secondary')+button('撤回约定','promise-withdraw',p.memory_id,'text-action')+'</div>':'')+'</article>';
 if(storage.get(archivePendingKey()))html+='<div class="error">约定操作结果尚待核对。'+button('重试原约定操作','promise-retry','','secondary')+'</div>';
 return html+'</section>';
}


function archivePendingKey(){return 'weiwan-archive:'+state.user?.id+':'+state.cid;}
function openPromiseConfirmation(mid,action){
 const p=state.archive?.promises?.find(p=>p.memory_id===mid);if(!p)return;
 if(storage.get(archivePendingKey())){toast('请先核对上次约定操作');return;}
 state.promiseChoice={action,memory_id:mid,revision:p.revision,client_request_id:'promise_'+newRequestKey(),...(action==='complete_promise'?{confirm_completed:true}:{})};
 modal(action==='complete_promise'?'确认这个约定已履行？':'撤回这个约定？','<p>'+esc(p.content)+'</p><p>'+(action==='complete_promise'?'仅在确实已经完成时确认，确认会更新相关记忆。':'撤回后不再作为待履行约定用于聊天。')+'</p>',button(action==='complete_promise'?'明确确认已履行':'确认撤回','promise-confirm',mid)+button('取消','close','','secondary'));
}
async function mutatePromise(mid,el){
 const version=epoch,key=archivePendingKey();let body;
 try{body=JSON.parse(storage.get(key)||'null')||state.promiseChoice;}catch{body=state.promiseChoice;}
 if(!body||mid&&body.memory_id!==mid)return;
 el.disabled=true;storage.set(key,JSON.stringify(body));
 try{await api('/api/conversations/'+state.cid+'/archive',{method:'POST',body});storage.delete(key);if(version!==epoch)return;state.promiseChoice=null;closeModal();await loadRoute();toast('约定已更新');}
 catch(error){if(error.status&&error.status<500)storage.delete(key);if(version!==epoch)return;if(handleChatAuth(error))return;el.disabled=false;closeModal();render();toast(error.message);}
}

// A single audio instance, created only after an explicit playback request.
const voice={serial:0,messageId:null,cid:null,content:null,status:'idle',audio:null,error:null,timer:null,wake:null,attempt:null};
function voiceEligible(m){return m.role==='assistant'&&m.status==='completed'&&!!m.reply_to_id&&!m.voice_blocked;}
function voiceHTML(mid){
 const metrics=voice.messageId===mid&&voice.metrics?'<small>'+esc(voice.metrics)+'</small>':'';
 if(voice.messageId!==mid)return button(state.capabilities.tts_configured===false?'语音暂未配置':'播放语音','voice-play',mid,'text-action');
 if(voice.status==='loading')return '<small role="status">正在准备语音…</small>'+button('取消播放','voice-stop','','text-action');
 if(voice.status==='playing')return metrics+button('暂停','voice-pause','','text-action')+button('停止','voice-stop','','text-action');
 if(voice.status==='paused')return metrics+button('继续播放','voice-resume','','text-action')+button('停止','voice-stop','','text-action');
 if(voice.status==='failed')return '<small class="danger">'+esc(voice.error||'语音播放失败')+'</small>'+button('重试语音','voice-play',mid,'text-action');
 return metrics+button('重播语音','voice-play',mid,'text-action');
}
function refreshVoiceControls(){
 if(typeof document.querySelectorAll!=='function')return;
 for(const el of document.querySelectorAll('[data-voice-message]'))el.innerHTML=voiceHTML(el.dataset.voiceMessage);
}
function stopVoice(){
 voice.serial++;clearTimeout(voice.timer);voice.timer=null;voice.wake?.();voice.wake=null;
 if(voice.audio){voice.audio.pause();voice.audio.removeAttribute('src');voice.audio.load();}
 voice.audio=null;voice.messageId=null;voice.cid=null;voice.content=null;voice.status='idle';voice.error=null;refreshVoiceControls();
}
function voiceCurrent(serial,version,mid,cid){return serial===voice.serial&&version===epoch&&state.cid===cid&&state.page==='chat'&&!document.hidden&&state.messages.some(m=>m.id===mid&&voiceEligible(m)&&m.content===voice.content);}
async function playVoice(mid){
 if(voice.messageId===mid&&voice.status==='loading')return;
 const m=state.messages.find(m=>m.id===mid&&voiceEligible(m));if(!m)return;
 const retry=voice.messageId===mid&&voice.status==='failed',attempt=voice.attempt;stopVoice();
 const serial=voice.serial,version=epoch,cid=state.cid,path='/api/conversations/'+cid+'/messages/'+mid+'/audio';
 Object.assign(voice,{messageId:mid,cid,content:m.content,status:'loading',error:null,metrics:null});refreshVoiceControls();
 try{
  let result=await api(path,{method:'POST',body:{use_id:state.useId,...(retry&&attempt?{retry:true,attempt}:{})}});voice.attempt=result.attempt;
  for(let i=0;result.status==='pending'&&i<120;i++){
   if(!voiceCurrent(serial,version,mid,cid))return;
   await new Promise(resolve=>{voice.wake=resolve;voice.timer=setTimeout(()=>{voice.wake=null;resolve();},500);});
   if(!voiceCurrent(serial,version,mid,cid))return;
   result=await api(path+'?use_id='+encodeURIComponent(state.useId||''));voice.attempt=result.attempt;
  }
  if(!voiceCurrent(serial,version,mid,cid))return;
  if(result.status!=='ready')throw new Error(result.error_message||'语音仍未就绪，请稍后重试');
  if(result.audio_url!==path+'/content?use_id='+encodeURIComponent(state.useId||''))throw new Error('语音来源与当前回复不符');
  voice.metrics=(typeof result.elapsed_seconds==='number'?'生成耗时 '+result.elapsed_seconds.toFixed(3)+' 秒 · ':'')+(typeof result.estimated_cny==='number'?'估算费用 ¥'+result.estimated_cny.toFixed(5):'费用未返回')+(result.cached?' · 缓存播放，不新增合成费用':'');
  const audio=new Audio();voice.audio=audio;audio.preload='none';audio.src=result.audio_url;
  audio.addEventListener('ended',()=>{if(voiceCurrent(serial,version,mid,cid)){voice.status='ended';refreshVoiceControls();}});
  audio.addEventListener('error',()=>{if(voiceCurrent(serial,version,mid,cid)){voice.status='failed';voice.error='音频加载失败，请重试';refreshVoiceControls();}});
  await audio.play();
  if(!voiceCurrent(serial,version,mid,cid)){audio.pause();return;}
  voice.status='playing';refreshVoiceControls();
 }catch(error){if(!voiceCurrent(serial,version,mid,cid))return;if(handleChatAuth(error))return;voice.status='failed';voice.error=error.message;refreshVoiceControls();}
}
function pauseVoice(){if(voice.audio&&voice.status==='playing'){voice.audio.pause();voice.status='paused';refreshVoiceControls();}}
async function resumeVoice(){
 if(voice.status!=='paused'||!voice.audio)return;
 const serial=voice.serial,version=epoch,mid=voice.messageId,cid=voice.cid;
 try{const result=await api('/api/conversations/'+cid+'/messages/'+mid+'/audio?use_id='+encodeURIComponent(state.useId||''));if(!voiceCurrent(serial,version,mid,cid))return;if(result.status!=='ready')throw new Error('语音已失效，请重试');const audio=voice.audio;await audio.play();if(!voiceCurrent(serial,version,mid,cid)){audio.pause();return;}voice.status='playing';refreshVoiceControls();}
 catch(error){if(serial!==voice.serial)return;voice.status='failed';voice.error='浏览器未能播放，请重试';refreshVoiceControls();}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopVoice();});

loadRoute();
