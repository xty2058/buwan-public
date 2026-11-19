// Deferred request + dismissed modal: exercise real saveMemoryEdit error paths.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
async function check(name,body){
 const fields={'#memory-until':{value:''},'#memory-content':{value:'现在更喜欢红茶'},'#memory-error':{innerHTML:''}};
 const ctx={console,assert,fields,TextDecoder,TextEncoder,Uint8Array,AbortController,setTimeout:()=>0,clearTimeout(){},sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},document:{querySelector:s=>fields[s]||null,addEventListener(){}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 await vm.runInContext(`(async()=>{let toasts=[];toast=text=>toasts.push(text);state.user={id:'qa'};state.cid='conv';state.editingMemory={id:'memory',revision:2};const submit={disabled:false,textContent:'保存修改'},form={querySelector:()=>submit};let rejectRequest,resolveRequest;api=()=>new Promise((resolve,reject)=>{rejectRequest=reject;resolveRequest=resolve});const pending=saveMemoryEdit(form);assert.equal(submit.disabled,true);${body}})()`,ctx);
 console.log('PASS '+name);
}
(async()=>{
 await check('保存中关闭记忆弹层后失败不会产生未处理异常',`fields['#memory-error']=null;rejectRequest(new Error('网络中断'));await pending;assert.equal(submit.disabled,false);assert.ok(toasts.some(x=>x.includes('网络中断')));assert.ok(!toasts.includes('修改已保存'));`);
 await check('记忆编辑失败保留输入并恢复重试按钮',`rejectRequest(new Error('网络中断'));await pending;assert.equal(fields['#memory-content'].value,'现在更喜欢红茶');assert.ok(fields['#memory-error'].innerHTML.includes('网络中断'));assert.equal(submit.disabled,false);assert.equal(submit.textContent,'重试保存');assert.equal(toasts.length,0);`);
 await check('离开会话后的迟到记忆失败不改写新页面',`epoch++;fields['#memory-error']=null;rejectRequest(new Error('旧请求失败'));await pending;assert.equal(toasts.length,0);`);
 await check('旧编辑保存成功后不关闭后来打开的新表单',`form.isConnected=false;let closes=0,reloads=0;closeModal=()=>closes++;loadRoute=async()=>reloads++;fields['#memory-content']={value:'新表单尚未提交'};resolveRequest({});await pending;assert.equal(closes,0);assert.equal(reloads,0);assert.equal(fields['#memory-content'].value,'新表单尚未提交');assert.ok(toasts.some(x=>x.includes('修改已保存')));`);
 await check('关闭旧表单后不把失败写入后来打开的新表单',`form.isConnected=false;fields['#memory-error']={innerHTML:'新表单内容'};rejectRequest(new Error('旧请求失败'));await pending;assert.equal(fields['#memory-error'].innerHTML,'新表单内容');assert.ok(toasts.some(x=>x.includes('旧请求失败')));`);
})().catch(error=>{console.error(error);process.exitCode=1});
