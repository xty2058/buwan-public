// Exercise real app functions without secure-context randomUUID. VM is not a physical phone.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
async function check(name,cryptoValue,body){
 const input={value:'手机测试内容'},app={innerHTML:''},modal={innerHTML:''};
 const ctx={console,assert,TextDecoder,TextEncoder,Uint8Array,AbortController,setTimeout:()=>0,clearTimeout(){},sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},document:{querySelector:s=>s==='#draft'?input:s==='#app'?app:s==='#modal'?modal:null,addEventListener(){}}};
 if(cryptoValue!==undefined)ctx.crypto=cryptoValue;
 vm.createContext(ctx);vm.runInContext(source,ctx);
 vm.runInContext(`render=()=>{};toast=()=>{};persistLocalRecovery=()=>{};refreshMessages=async()=>{};loadRoute=async()=>{};observeGeneration=async()=>{};state.user={id:'qa'};state.cid='conv';state.role='linyu';state.loading=false;state.localRecord={};state.messages=[{id:'u',role:'user',content:'手机测试内容'},{id:'old',role:'assistant',content:'原回复',reply_to_id:'u',status:'completed'}];`,ctx);
 await vm.runInContext(`(async()=>{${body}})()`,ctx);console.log('PASS '+name);
}
const send=`let paths=[];api=async(path,options)=>{paths.push({path,body:options.body});if(path.endsWith('/messages'))return {id:'saved'};return {request:{id:'r',kind:'reply',status:'adopted',revision:1,attempt:1,user_message_id:'saved'},message:{id:'reply',role:'assistant',status:'completed',content:'收到'}}};await saveMessage();assert.equal(paths.length,2);assert.ok(paths[0].path.endsWith('/messages'));assert.match(paths[0].body.client_request_id,/^[A-Za-z0-9_-]{8,80}$/);assert.equal(paths[1].body.user_message_id,'saved');assert.equal(state.busy,false);assert.equal(state.generation.request.status,'adopted');`;
(async()=>{
 let n=0;
 await check('局域网仅getRandomValues时发送实际进入保存与生成', {getRandomValues(bytes){for(let i=0;i<bytes.length;i++)bytes[i]=(n++)%256;return bytes}},send);
 await check('完全无crypto对象时发送仍进入保存与生成',undefined,send);
 await check('退化环境连续生成标识合法且不重复',undefined,`const keys=Array.from({length:3000},()=>newRequestKey());assert.equal(new Set(keys).size,3000);for(const key of keys){assert.match(key,/^[A-Za-z0-9_-]{8,80}$/);assert.ok(('candidate_'+key).length<=80);}`);
 await check('无randomUUID时保存失败重试沿用原标识并保留输入',{},`const keys=[];api=async(path,options)=>{if(path.endsWith('/messages')){keys.push(options.body.client_request_id);if(keys.length===1)throw new Error('断网');return {id:'saved'}}return {request:{id:'r',kind:'reply',status:'adopted',attempt:1,revision:1},message:{id:'a',role:'assistant',content:'收到'}}};await saveMessage();assert.equal(drafts.get('conv'),'手机测试内容');const original=retryMessage;await saveMessage(original);assert.equal(keys.length,2);assert.equal(keys[0],keys[1]);`);
 await check('候选创建未知后重试不更换原标识',{},`const keys=[];api=async(path,options)=>{keys.push(options.body.client_request_id);if(keys.length===1)throw new Error('未知网络结果');return {request:{id:'r',kind:'regenerate',status:'awaiting_choice',attempt:1,revision:1,user_message_id:'u'},message:{id:'new',role:'assistant',content:'候选'},original:{id:'old',content:'原回复'}}};await createCandidate('old');assert.ok(state.localRecord.candidate_intent);await createCandidate('old',true);assert.equal(keys.length,2);assert.equal(keys[0],keys[1]);assert.equal(state.generation.request.status,'awaiting_choice');`);
 await check('关系决定在HTTP环境可提交且未知结果重试沿用原标识',undefined,`state.relationship={state:{revision:0}};state.useId='use';const keys=[];api=async(path,options)=>{keys.push(options.body.client_request_id);if(keys.length===1)throw Object.assign(new Error('暂不可用'),{status:503});return {}};const el={disabled:false,textContent:'确认'};await relationshipOperation({action:'pace',pace:'慢一点',store:false},el);assert.ok(state.relationPending);await relationshipOperation(null,el);assert.equal(keys.length,2);assert.equal(keys[0],keys[1]);assert.equal(state.relationPending,null);`);
})().catch(error=>{console.error(error);process.exitCode=1});
