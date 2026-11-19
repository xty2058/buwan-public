// Actual application functions and DOM writes in VM. Network order/DOM are fixtures, not browser E2E.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
function context(){
 const bubble={textContent:'第五版正文'},candidate={textContent:'第五版候选'};
 const area={scrollTop:123,scrollHeight:2000,clientHeight:400};
 const input={value:'下一条草稿',selectionStart:2,selectionEnd:4,focus(){},setSelectionRange(){}};
 const ctx={console,assert,TextEncoder,TextDecoder,URLSearchParams,AbortController,Uint8Array,setTimeout:()=>0,clearTimeout(){},window:{addEventListener(){}},sessionStorage:{getItem:()=>null},document:{activeElement:input,querySelector:s=>s==='#chat-scroll'?area:s==='#draft'?input:s==='#app .candidate .prewrap'?candidate:null,getElementById:id=>id==='a'?{querySelector:()=>bubble}:null,addEventListener(){}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);Object.assign(ctx,{bubble,candidate,area,input});
 vm.runInContext(`
 let renders=0,persisted=0,apiCalls=[];
 render=()=>{renders++};persistLocalRecovery=()=>{persisted++};refreshMessages=async()=>{};refreshMemoryNotice=()=>{};
 function snapshot(revision,content='第五版正文',attempt=1,status='generating',kind='reply',id='r'){
  return {request:{id,attempt,revision,status,kind,user_message_id:'u'},message:{id:kind==='reply'?'a':'candidate',role:'assistant',content,status:'streaming'},original:{id:'original',content:'原回复正文'}};
 }
 function begin(kind='reply',status='generating'){
  state.page='chat';state.cid='c';state.role='linyu';state.user={id:'A'};state.hasNewer=false;state.streamError=null;
  state.generation=snapshot(5,kind==='reply'?'第五版正文':'第五版候选',1,status,kind);state.busy=status==='generating';
  state.messages=[{id:'u',role:'user',content:'原始问题'},...(kind==='reply'?[state.generation.message]:[])];
  drafts.set('c','下一条草稿');state.localRecord={view:{at_latest:false}};
 }
 function unchanged(){assert.equal(area.scrollTop,123);assert.equal(input.value,'下一条草稿');assert.equal(drafts.get('c'),'下一条草稿');assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,4);}
 begin();`,ctx);return ctx;
}
async function check(label,body){await vm.runInContext(`(async()=>{${body}})()`,context());console.log('PASS '+label)}
(async()=>{
 await check('同轮同状态低revision拒绝后气泡和内部状态均不倒退',`
 const before=JSON.stringify(state);assert.equal(renderStreamSnapshot(snapshot(4,'旧片段')),false);
 assert.equal(JSON.stringify(state),before);assert.equal(bubble.textContent,'第五版正文');assert.equal(state.messages.at(-1).content,'第五版正文');assert.equal(renders,0);assert.equal(persisted,0);unchanged();`);
 await check('GET较新版先到，SSE旧片段随后到达不能覆盖可见文本',`
 api=async(p,o)=>{apiCalls.push([p,o?.method]);return snapshot(6,'核对得到第六版')};
 assert.equal(await pollGenerationStatus(streamSerial,epoch,'c','r'),true);
 assert.equal(renderStreamSnapshot(snapshot(4,'迟到SSE')),false);
 assert.equal(bubble.textContent,'核对得到第六版');assert.equal(state.generation.request.revision,6);
 assert.equal(apiCalls.length,1);assert.equal(apiCalls[0][1],undefined);assert.ok(apiCalls[0][0].endsWith('/generations/r'));unchanged();`);
 await check('SSE较新版先到，迟到旧GET核对不能覆盖可见文本',`
 assert.equal(renderStreamSnapshot(snapshot(8,'SSE第八版')),true);
 api=async(p,o)=>{apiCalls.push([p,o?.method]);return snapshot(7,'迟到GET')};
 assert.equal(await pollGenerationStatus(streamSerial,epoch,'c','r'),true);
 assert.equal(bubble.textContent,'SSE第八版');assert.equal(state.generation.message.content,'SSE第八版');assert.equal(apiCalls.length,1);assert.equal(apiCalls[0][1],undefined);unchanged();`);
 await check('旧attempt即使revision更高也不能覆盖气泡，新revision与同版本合法快照仍显示',`
 state.generation=snapshot(5,'第五版正文',2);state.messages=[state.generation.message];
 assert.equal(renderStreamSnapshot(snapshot(99,'旧轮次',1)),false);assert.equal(bubble.textContent,'第五版正文');
 assert.equal(renderStreamSnapshot(snapshot(6,'合法新片段',2)),true);assert.equal(bubble.textContent,'合法新片段');
 assert.equal(renderStreamSnapshot(snapshot(6,'同版本合法片段',2)),true);assert.equal(bubble.textContent,'同版本合法片段');
 assert.equal(state.messages.at(-1).content,bubble.textContent);unchanged();`);
 await check('候选气泡拒绝旧片段，切到原回复视图仍显示原回复',`
 begin('regenerate');assert.equal(renderStreamSnapshot(snapshot(4,'旧候选',1,'generating','regenerate')),false);
 assert.equal(candidate.textContent,'第五版候选');assert.equal(renderStreamSnapshot(snapshot(6,'第六版候选',1,'generating','regenerate')),true);assert.equal(candidate.textContent,'第六版候选');
 begin('regenerate','awaiting_choice');state.candidateView='old';candidate.textContent='原回复正文';
 assert.equal(renderStreamSnapshot(snapshot(5,'新候选正文',1,'awaiting_choice','regenerate')),true);assert.equal(candidate.textContent,'原回复正文');
 assert.equal(renderStreamSnapshot(snapshot(4,'旧候选正文',1,'awaiting_choice','regenerate')),false);assert.equal(candidate.textContent,'原回复正文');
 state.candidateView='new';assert.equal(renderStreamSnapshot(snapshot(6,'新视图候选',1,'awaiting_choice','regenerate')),true);assert.equal(candidate.textContent,'新视图候选');unchanged();`);
 await check('停止及候选保留后的旧流不恢复追加，停止输入保持原样',`
 state.generation=snapshot(9,'停止片段',1,'stopped');state.messages=[state.generation.message];bubble.textContent='停止片段';state.busy=false;
 assert.equal(renderStreamSnapshot(snapshot(8,'迟到普通流')),false);assert.equal(bubble.textContent,'停止片段');assert.equal(state.busy,false);
 state.generation=snapshot(10,'取消候选',1,'kept','regenerate');candidate.textContent='已保留原回复';
 assert.equal(renderStreamSnapshot(snapshot(9,'迟到候选',1,'generating','regenerate')),false);assert.equal(candidate.textContent,'已保留原回复');assert.equal(renders,0);unchanged();`);
 await check('另一个请求的迟到快照不能替换当前请求视图',`
 const before=JSON.stringify(state);assert.equal(renderStreamSnapshot(snapshot(100,'旧请求',1,'generating','reply','old-request')),false);
 assert.equal(JSON.stringify(state),before);assert.equal(bubble.textContent,'第五版正文');assert.equal(renders,0);unchanged();`);
 await check('旧serial或旧角色订阅返回后不渲染，也不重发生成',`
 for(const boundary of ['serial','role']){
  begin();let release,fetches=0;fetch=async()=>{fetches++;return {ok:true,body:{getReader:()=>({read:()=>new Promise(resolve=>{release=resolve})})}}};
  const pending=observeGeneration();await Promise.resolve();assert.equal(typeof release,'function');
  if(boundary==='serial')stopObserving();else{state.cid='other-role-conversation';state.role='luye';epoch++;}
  release({value:new TextEncoder().encode('event: snapshot\\ndata: '+JSON.stringify(snapshot(99,'旧订阅正文'))+'\\n\\n'),done:false});
  await pending;assert.equal(bubble.textContent,'第五版正文');assert.equal(fetches,1);assert.equal(state.generation.request.revision,5);
 }
 assert.equal(apiCalls.length,0);assert.equal(renders,0);assert.equal(persisted,0);unchanged();`);
 await check('合法终态快照交给完整重绘，终态后的迟到旧片段不再改DOM',`
 assert.equal(renderStreamSnapshot(snapshot(6,'完整回复',1,'adopted')),true);assert.equal(renders,1);assert.equal(state.busy,false);assert.equal(state.messages.at(-1).content,'完整回复');
 assert.equal(renderStreamSnapshot(snapshot(5,'旧进行中片段')),false);assert.equal(renders,1);assert.equal(state.generation.request.status,'adopted');unchanged();`);
})().catch(error=>{console.error(error);process.exitCode=1});
