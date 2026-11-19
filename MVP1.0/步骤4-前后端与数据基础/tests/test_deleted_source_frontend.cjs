// Real application functions with VM/DOM stand-ins, not browser clicks.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
(async()=>{
 for(const operation of ['createCandidate','retryGeneration','startReply']){
  const ctx={console,assert,TextDecoder,TextEncoder,Uint8Array,AbortController,setTimeout:()=>0,clearTimeout(){},crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789012'},sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},document:{querySelector(){return null},addEventListener(){}}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  await vm.runInContext(`(async()=>{
   render=()=>{};toast=message=>{assert.ok(message.includes('新的内容'))};persistLocalRecovery=()=>{};
   state.user={id:'A'};state.cid='conv';state.busy=false;state.messages=[{id:'u',role:'user',content:'旧来源'},{id:'a',role:'assistant',reply_to_id:'u',status:'completed',content:'有效历史'}];
   state.generation={request:{id:'r',kind:'reply',user_message_id:'u',status:'failed',attempt:1},message:state.messages[1]};
   state.localRecord={candidate_intent:{original_reply_id:'a'}};drafts.set('conv','新的内容草稿');pendingMessages.set('conv',{content:'旧来源'});retryMessage={content:'旧来源'};
   api=async()=>{throw Object.assign(new Error('请发送新的内容继续聊天'),{status:409,code:'source_unavailable'})};
   ${operation==='createCandidate'?'await createCandidate("a")':operation==='retryGeneration'?'await retryGeneration()':'await startReply({user_message_id:"u",content:"旧来源",generation_key:"old_key"})'};
   assert.equal(state.busy,false);assert.equal(state.streamError,null);assert.equal(state.generation.source_available,false);
   assert.equal(state.localRecord.candidate_intent,undefined);assert.equal(retryMessage,null);assert.equal(pendingMessages.has('conv'),false);assert.equal(drafts.get('conv'),'新的内容草稿');assert.equal(generationRecoverable(),false);
   const html=chatHTML({id:'conv',character_id:'linyu',character:{name:'林屿'},version:1,initial_scene:'书店'});assert.ok(!html.includes('data-action="regenerate"'));
  })()`,ctx);
  console.log('PASS '+operation+'已知来源失效不保留未知请求或禁用新输入，草稿保留');
 }
})().catch(e=>{console.error(e);process.exitCode=1});
