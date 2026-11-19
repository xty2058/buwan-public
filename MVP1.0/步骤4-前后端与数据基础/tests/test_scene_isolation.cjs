// Original frontend functions in isolated VM with DOM/API stand-ins, not browser E2E.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../frontend/app.js','utf8').replace(/loadRoute\(\);\s*$/,'');
async function check(label,body){
 const app={innerHTML:''},modal={innerHTML:''};
 const ctx={assert,console,setTimeout:()=>0,clearTimeout(){},sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},document:{querySelector(s){return s==='#app'?app:s==='#modal'?modal:null},addEventListener(){}},FormData:class{get(key){return {username:'test',password:'Test-only-password-2026!',nickname:'测试'}[key]}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);ctx.app=app;
 vm.runInContext(`renderInspector=()=>{};toast=()=>{};navigate=async(page)=>{state.page=page};state.loading=false;state.role='linyu';state.characters=[{id:'linyu',name:'林屿',age:25,job:'店主',tags:[],bio:'介绍',initial_scene:'公开默认背景',opening:'公开开场',place:'书店',version:1}];`,ctx);
 await vm.runInContext(`(async()=>{${body}})()`,ctx);console.log('PASS '+label);
}
const setupA=`state.user={id:'A',nickname:'A'};sceneDrafts.linyu='REVIEW_A_PRIVATE_SCENE';drafts.set('old-conv','PRIVATE_MESSAGE');`;
const checkB=`state.page='detail';render();assert.ok(!app.innerHTML.includes('REVIEW_A_PRIVATE_SCENE'));let submitted;api=async(path,options)=>{submitted=options.body;return {id:'new-conv'}};await action('start','linyu',{});assert.ok(!JSON.stringify(submitted).includes('REVIEW_A_PRIVATE_SCENE'));assert.equal(submitted.initial_scene,undefined);assert.equal(drafts.size,0);`;
(async()=>{
 await check('S04-01 A退出后B详情及创建请求均无A场景',`${setupA}api=async()=>({});await action('logout','',{});state.user={id:'B',nickname:'B'};${checkB}`);
 await check('S04-01 服务端身份A变B时清理场景与消息草稿',`${setupA}api=async()=>({user:{id:'B',nickname:'B'},csrf_token:'test'});await refreshUser(epoch);${checkB}`);
 await check('S04-01 401失效清理，随后B登录不继承A场景',`${setupA}api=async()=>{throw Object.assign(new Error('expired'),{status:401})};await action('start','linyu',{});assert.equal(state.user,null);state.user={id:'B',nickname:'B'};${checkB}`);
 await check('S04-01 认证提交切换账号也清理已登录用户场景',`${setupA}api=async()=>({user:{id:'B',nickname:'B'},csrf_token:'test',items:[]});await authSubmit({querySelector(){return {}},reset(){}});${checkB}`);
 await check('S04-01 游客调整场景经登录返回原角色并实际提交',`state.user=null;sceneDrafts.linyu='GUEST_INTENDED_SCENE';setReturn({page:'detail',role:'linyu'});api=async()=>({user:{id:'A',nickname:'A'},csrf_token:'test'});await authSubmit({querySelector(){return {}},reset(){}});assert.equal(state.page,'detail');render();assert.ok(app.innerHTML.includes('GUEST_INTENDED_SCENE'));let submitted;api=async(path,options)=>{submitted=options.body;return {id:'new-conv'}};await action('start','linyu',{});assert.equal(submitted.initial_scene,'GUEST_INTENDED_SCENE');`);
 await check('S04-01 游客路径不能复用上个账号退出后的私人场景',`${setupA}clearPrivate();state.user=null;setReturn({page:'detail',role:'linyu'});api=async()=>({user:{id:'B',nickname:'B'},csrf_token:'test'});await authSubmit({querySelector(){return {}},reset(){}});${checkB}`);
})().catch(error=>{console.error(error);process.exitCode=1});
