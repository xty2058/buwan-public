// Effective-source UI recovery with actual app functions and DOM/network stand-ins.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
async function check(label,body){
 const ctx={assert,console,URLSearchParams,TextEncoder,TextDecoder,Uint8Array,AbortController,setTimeout:()=>0,clearTimeout(){},sessionStorage:{getItem:()=>null},window:{addEventListener(){}},document:{querySelector:()=>null,addEventListener(){}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 vm.runInContext(`render=()=>{};toast=()=>{};persistLocalRecovery=()=>{};state.user={id:'A'};state.cid='c';state.role='linyu';state.useId='current-use';state.localRecord={candidate_intent:{client_request_id:'old_candidate',user_message_id:'u'}};state.messages=[];drafts.set('c','新的普通草稿');pendingMessages.set('c',{content:'旧临时授权',user_message_id:'u',client_request_id:'old_key'});`,ctx);
 await vm.runInContext(`(async()=>{${body}})()`,ctx);console.log('PASS '+label);
}
(async()=>{
 await check('恢复与只读快照/流请求携带当前使用，避免沿用旧生成使用标识',`
 const url=recoveryUrl();assert.ok(url.includes('use_id=current-use'));
 assert.equal(generationReadUrl('c','r'),'/api/conversations/c/generations/r?use_id=current-use');
 assert.equal(generationReadUrl('c','r','/stream'),'/api/conversations/c/generations/r/stream?use_id=current-use');
 assert.equal(generationReadUrl('other','r'),'/api/conversations/other/generations/r');`);
 await check('跨次不可用来源恢复后无重做入口或未知请求，保留新草稿',`
 const user={id:'u',role:'user',content:'旧临时授权'},reply={id:'a',role:'assistant',reply_to_id:'u',status:'completed',content:'旧回复'};
 applyRecovery({items:[user,reply],last_user_message:user,last_user_source_available:false,submitted_message:user,generation:{request:{id:'r',kind:'reply',user_message_id:'u',status:'adopted'},source_available:false,message:reply},reply_required:false});
 assert.equal(state.replyRequired,false);assert.equal(generationRecoverable(),false);assert.equal(retryMessage,null);assert.equal(pendingMessages.has('c'),false);assert.equal(state.localRecord.candidate_intent,undefined);assert.equal(drafts.get('c'),'新的普通草稿');assert.equal(state.streamError,null);
 const html=chatHTML({id:'c',character_id:'linyu',character:{name:'林屿'},version:1,initial_scene:'书店'});
 assert.ok(!html.includes('data-action="regenerate"'));assert.ok(!html.includes('data-action="retry-generation"'));`);
 await check('未启动的失效旧来源不恢复生成入口，普通可用新来源仍能恢复',`
 const user={id:'u',role:'user',content:'旧临时授权'};
 applyRecovery({items:[user],last_user_message:user,last_user_source_available:false,submitted_message:user,generation:{request:null},reply_required:false});
 assert.equal(retryMessage,null);assert.equal(pendingMessages.has('c'),false);assert.equal(state.replyRequired,false);assert.equal(drafts.get('c'),'新的普通草稿');
 const next={id:'new-u',role:'user',content:'新的普通问题'};pendingMessages.set('c',{content:next.content,client_request_id:'new_key'});
 applyRecovery({character_id:'linyu',items:[user,next],last_user_message:next,last_user_source_available:true,submitted_message:next,generation:{request:null},reply_required:true});
 assert.equal(state.replyRequired,true);assert.equal(retryMessage.type,'generation');assert.equal(retryMessage.user_message_id,'new-u');assert.equal(drafts.get('c'),'新的普通草稿');`);
})().catch(error=>{console.error(error);process.exitCode=1});
