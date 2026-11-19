// Runs application functions with DOM/network stand-ins; not browser E2E.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
async function check(name,body){
 const input={value:'原始输入'},app={innerHTML:''},modal={innerHTML:''};
 const ctx={console,assert,TextDecoder,TextEncoder,Uint8Array,AbortController,setTimeout:()=>0,clearTimeout(){},
  crypto:{randomUUID:()=> '12345678-1234-1234-1234-123456789012'},
  sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},
  document:{querySelector:s=>s==='#draft'?input:s==='#app'?app:s==='#modal'?modal:null,addEventListener(){}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);ctx.input=input;
 vm.runInContext(`render=()=>{};toast=()=>{};refreshMessages=async()=>{};state.loading=false;state.user={id:'A'};state.cid='conv';state.role='linyu';state.messages=[{id:'u',role:'user',content:'原始输入'}];`,ctx);
 await vm.runInContext(`(async()=>{${body}})()`,ctx);console.log('PASS '+name);
}
(async()=>{
 await check('保存成功后生成失败，保留输入并保留原消息标识',`
 let paths=[];api=async(path,options)=>{paths.push(path);if(path.endsWith('/messages'))return {id:'u'};throw new Error('模型断网')};
 await saveMessage();assert.equal(drafts.get('conv'),'原始输入');assert.equal(retryMessage.type,'generation');assert.equal(retryMessage.user_message_id,'u');assert.equal(paths.filter(x=>x.endsWith('/messages')).length,1);`);
 await check('失败重试沿用原轮，按发送也不重复提交用户消息',`
 state.generation={request:{id:'r',kind:'reply',status:'failed',user_message_id:'u'}};let retried=false;
 retryGeneration=async()=>{retried=true};api=async()=>{throw new Error('不应保存新用户消息')};await saveMessage();assert.equal(retried,true);`);
 await check('停止立即废弃旧流，停止后的迟到低版本快照不追加',`
 state.generation={request:{id:'r',kind:'reply',status:'generating',revision:4,attempt:1},message:{id:'a',content:'可见片段',role:'assistant'}};
 state.messages.push(state.generation.message);let release;api=()=>new Promise(r=>release=r);let serial=streamSerial;
 const pending=stopGeneration();assert.equal(state.stopping,true);assert.ok(streamSerial>serial);
 release({request:{id:'r',kind:'reply',status:'stopped',revision:9,attempt:1},message:{id:'a',content:'可见片段',role:'assistant'}});await pending;
 applyGeneration({request:{id:'r',kind:'reply',status:'generating',revision:5,attempt:1},message:{id:'a',content:'迟到新内容'}});
 assert.equal(state.messages.at(-1).content,'可见片段');assert.equal(state.generation.request.status,'stopped');`);
 await check('采用结果未知先核对，不提前切换原回复',`
 state.generation={request:{id:'r',kind:'regenerate',status:'awaiting_choice'},original:{id:'old',content:'原回复'},message:{id:'new',content:'候选'}};
 state.messages.push({id:'old',role:'assistant',content:'原回复'});api=async()=>{throw new Error('结果未知')};await chooseCandidate('adopt');
 assert.equal(state.messages.at(-1).id,'old');assert.ok(state.streamError.includes('核对'));assert.equal(isUndecided(),true);`);
 await check('生成期间已编辑的下一条草稿不被失败覆盖',`
 drafts.set('conv','下一条草稿');applyGeneration({request:{id:'r',status:'failed',kind:'reply',attempt:1,revision:2,user_message_id:'u'},message:{id:'a',content:'片段'}});
 assert.equal(drafts.get('conv'),'下一条草稿');`);
 for(const view of ['old','new']){
  await check('候选'+(view==='old'?'原回复':'新候选')+'视图的明确按钮与采用/保留请求一致',`
   const view=${JSON.stringify(view)};
   const conversation={id:'conv',character_id:'linyu',character:{name:'林屿'},version:1,initial_scene:'书店'};
   for(const choice of ['adopt','keep']){
    state.messages=[{id:'u',role:'user',content:'问题'},{id:'old',role:'assistant',content:'ORIGINAL_TEXT',status:'completed',reply_to_id:'u'}];
    state.generation={request:{id:'r',kind:'regenerate',status:'awaiting_choice',attempt:1,revision:2},original:{id:'old',content:'ORIGINAL_TEXT'},message:{id:'new',content:'NEW_CANDIDATE_TEXT'}};
    await action(view==='old'?'candidate-original':'candidate-new','',{});
    const html=chatHTML(conversation);
    assert.ok(html.includes('<p class="prewrap">'+(view==='old'?'ORIGINAL_TEXT':'NEW_CANDIDATE_TEXT')+'</p>'));
    assert.ok(html.includes('采用新候选'));assert.ok(html.includes('保留原回复'));
    assert.ok(!html.includes('采用这条'));
    let submitted;
    api=async(path,options)=>{assert.ok(path.endsWith('/r/choice'));submitted=options.body.choice;return {...state.generation,request:{...state.generation.request,status:choice==='adopt'?'adopted':'kept',revision:3}}};
    await action(choice==='adopt'?'adopt-candidate':'keep-candidate','',{});
    assert.equal(submitted,choice);assert.equal(isUndecided(),false);
   }`);
 }
 await check('跨网络字节块的SSE中文和终止快照正确解析',`
 state.generation={request:{id:'r',status:'generating',kind:'reply',attempt:1,revision:1}};
 const text='event: snapshot\\ndata: '+JSON.stringify({request:{id:'r',status:'adopted',kind:'reply',attempt:1,revision:2},message:{id:'a',content:'中文🙂回复',role:'assistant'}})+'\\n\\n';
 const bytes=new TextEncoder().encode(text);let offset=0;
 fetch=async()=>({ok:true,body:{getReader:()=>({read:async()=>{if(offset>=bytes.length)return {done:true};const value=bytes.slice(offset,offset+3);offset+=3;return {value,done:false}}})}});
 await observeGeneration();assert.equal(state.generation.message.content,'中文🙂回复');assert.equal(state.busy,false);`);
})().catch(e=>{console.error(e);process.exitCode=1});
