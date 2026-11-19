// Original UI/cache functions; isolated storage and DOM stand-ins, not browser E2E.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const appSource=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
const cacheSource=fs.readFileSync(__dirname+'/../frontend/recovery-cache.js','utf8');
function store(){const values=new Map();return {get length(){return values.size},key:i=>[...values.keys()][i],getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
function context(local=store()){
 const input={value:'草稿',addEventListener(){}};
 const ctx={assert,console,localStorage:local,URLSearchParams,AbortController,TextDecoder,TextEncoder,Uint8Array,
  crypto:{randomUUID:()=> 'test-request-12345678'},setTimeout:()=>0,clearTimeout(){},
  sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},
  document:{activeElement:null,querySelector:s=>s==='#draft'?input:s==='#modal'?{innerHTML:''}:null,addEventListener(){},getElementById(){return null}}};
 vm.createContext(ctx);vm.runInContext(cacheSource,ctx);vm.runInContext(appSource,ctx);
 vm.runInContext(`render=()=>{};renderInspector=()=>{};toast=()=>{};state.user={id:'A'};state.cid='conv-a';state.conversation={id:'conv-a'};state.page='chat';state.loading=false;`,ctx);
 return ctx;
}
async function check(name,body){await body();console.log('PASS '+name);}
const completed=`({conversation_id:'conv-a',items:[{id:'u',role:'user',content:'原消息',ordinal:1},{id:'a',role:'assistant',reply_to_id:'u',content:'有效回复',status:'completed',ordinal:2}],last_user_message:{id:'u',content:'原消息'},generation:{request:{id:'g',status:'adopted',kind:'reply',attempt:1,revision:8,user_message_id:'u'},message:{id:'a',content:'有效回复'}},has_more:false,has_newer:false,reply_required:false})`;
(async()=>{
 await check('刷新新运行环境恢复草稿、原请求和阅读锚点',async()=>{
  const shared=store(),first=context(shared);
  vm.runInContext(`drafts.set('conv-a','未发送的私有草稿');pendingMessages.set('conv-a',{content:'原消息',client_request_id:'stable_request_123'});state.localRecord.view={at_latest:false,anchor_id:'anchor',anchor_offset:25};persistLocalRecovery();`,first);
  const restored=context(shared);vm.runInContext(`restoreLocalRecovery();assert.equal(drafts.get('conv-a'),'未发送的私有草稿');assert.equal(retryMessage.client_request_id,'stable_request_123');assert.equal(state.restoreView.anchor_id,'anchor');`,restored);
 });
 await check('切换账号不恢复上一账号草稿和请求',async()=>{
  const shared=store(),a=context(shared);vm.runInContext(`drafts.set('conv-a','ACCOUNT_A_SECRET');persistLocalRecovery();`,a);
  const b=context(shared);vm.runInContext(`recoveryCache.restrictTo('B');state.user={id:'B'};state.cid='conv-b';state.conversation={id:'conv-b'};restoreLocalRecovery();assert.equal(drafts.size,0);assert.equal(pendingMessages.size,0);assert.equal(recoveryCache.read('A','conv-a').draft,undefined);`,b);
 });
 await check('保存结果已确认但尚无回复时恢复原轮，不重复保存用户消息',async()=>{
  const c=context();await vm.runInContext(`(async()=>{pendingMessages.set('conv-a',{content:'原消息',client_request_id:'original_request_123'});const data=${completed};data.generation={request:null};data.reply_required=true;data.submitted_message=data.last_user_message;data.submitted_generation=null;applyRecovery(data,true);assert.equal(retryMessage.type,'generation');let submitted;startReply=async x=>submitted=x;await saveMessage(retryMessage);assert.equal(submitted.user_message_id,'u');assert.equal(submitted.generation_key,'reply_u');})()`,c);
 });
 await check('服务端已完成时清除旧待重试请求且不自动调用模型',async()=>{
  const c=context();vm.runInContext(`let calls=0;fetch=()=>{calls++;throw new Error('不应调用')};drafts.set('conv-a','原消息');pendingMessages.set('conv-a',{content:'原消息',client_request_id:'old_request'});const data=${completed};data.submitted_message=data.last_user_message;data.submitted_generation=data.generation;applyRecovery(data,true);assert.equal(retryMessage,null);assert.equal(drafts.has('conv-a'),false);assert.equal(calls,0);`,c);
 });
 await check('停止结果未知时保持冻结和原停止轮次',async()=>{
  const c=context();vm.runInContext(`state.localRecord.stop_intent={rid:'g',attempt:1,visible_chars:2};const data=${completed};data.generation.request.status='generating';data.generation.message.content='可见不得追加';data.items[1].content='可见不得追加';applyRecovery(data,true);assert.equal(state.generation.message.content,'可见');assert.equal(state.messages[1].content,'可见');assert.ok(state.streamError.includes('冻结'));assert.equal(state.localRecord.stop_intent.attempt,1);`,c);
 });
 await check('旧停止意图不能套用到新重试结果',async()=>{
  const c=context();vm.runInContext(`state.localRecord.stop_intent={rid:'g',attempt:1,visible_chars:0};const data=${completed};data.generation.request.attempt=2;applyRecovery(data,true);assert.equal(state.localRecord.stop_intent,undefined);assert.equal(state.messages[1].content,'有效回复');`,c);
 });
 await check('候选创建结果未知时重发同一个幂等标识',async()=>{
  const c=context();await vm.runInContext(`(async()=>{state.messages=[{id:'original',reply_to_id:'u'}];const intent={kind:'regenerate',user_message_id:'u',original_reply_id:'original',client_request_id:'candidate_stable_123'};state.localRecord.candidate_intent=intent;let captured;api=async(p,o)=>{captured=o.body;return {request:{id:'g',kind:'regenerate',status:'awaiting_choice',attempt:1,revision:2}}};await createCandidate('original',true);assert.equal(captured.client_request_id,'candidate_stable_123');assert.equal(state.localRecord.candidate_intent,undefined);})()`,c);
 });
 await check('候选最终选择从服务端恢复，不擅自采用',async()=>{
  const c=context();vm.runInContext(`const data=${completed};data.generation.request.status='awaiting_choice';data.generation.request.kind='regenerate';data.generation.message.content='尚未采用候选';applyRecovery(data,true);assert.equal(state.messages[1].content,'有效回复');assert.equal(isUndecided(),true);assert.equal(state.busy,false);`,c);
 });
 await check('其他角色仅保存消息时恢复不错误进入生成重试',async()=>{
  const c=context();vm.runInContext(`state.capabilities.chat_roles=['linyu'];state.role='luye';drafts.set('conv-a','原消息');pendingMessages.set('conv-a',{content:'原消息',client_request_id:'other_role_saved'});const data=${completed};data.character_id='luye';data.generation={request:null};data.reply_required=false;data.submitted_message=data.last_user_message;applyRecovery(data,true);assert.equal(retryMessage,null);assert.equal(drafts.has('conv-a'),false);`,c);
 });
 await check('阅读旧页时重试最新失败输入仍指向原轮',async()=>{
  const c=context();await vm.runInContext(`(async()=>{state.latestUser={id:'latest-user',content:'草稿'};state.messages=[{id:'old',role:'user',content:'历史'}];state.generation={request:{id:'r',kind:'reply',status:'failed',user_message_id:'latest-user'}};let retried=false;retryGeneration=async()=>{retried=true};await saveMessage();assert.equal(retried,true);})()`,c);
 });
 await check('本地存储不可用时说明限制，不影响已保存历史恢复',async()=>{
  const broken={get length(){throw new Error('denied')},getItem(){throw new Error('denied')},setItem(){throw new Error('denied')},removeItem(){}};
  const c=context(broken);vm.runInContext(`drafts.set('conv-a','暂存');persistLocalRecovery();assert.equal(state.cacheWarning,true);applyRecovery(${completed},true);assert.equal(state.messages[1].content,'有效回复');`,c);
 });
 await check('时间采用真实保存时间，空时间不伪造',async()=>{
  const c=context();vm.runInContext(`assert.equal(messageTime(undefined),'');assert.ok(messageTime(1794587400).includes('datetime='));`,c);
 });
 await check('阅读位置按锚点恢复，旧页面输入事件不能写入新账号',async()=>{
  const c=context();vm.runInContext(`let listener;const area={scrollTop:0,scrollHeight:2000,clientHeight:400,getBoundingClientRect:()=>({top:100}),addEventListener(){}};const input={addEventListener:(n,fn)=>{listener=fn}};document.querySelector=s=>s==='#chat-scroll'?area:s==='#draft'?input:null;document.getElementById=()=>({getBoundingClientRect:()=>({top:600})});state.restoreView={at_latest:false,anchor_id:'anchor',anchor_offset:40};wireChatView();assert.equal(area.scrollTop,460);state.user={id:'B'};listener({target:{value:'迟到A草稿'}});assert.equal(drafts.size,0);`,c);
 });
})().catch(error=>{console.error(error);process.exitCode=1});
