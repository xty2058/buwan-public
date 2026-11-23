// 语音输入（路线 2.6）：录一段话，转成 16 kHz 单声道 16 位 WAV，交给服务端识别。
// 浏览器各自的录音格式不同（Chrome 是 webm，iPhone 是 mp4），统一在这里转成 WAV，
// 服务端只需要认一种格式。识别结果只填进输入框，由用户决定发不发。
const voiceInput=(()=>{
 const RATE=16000,MAX_SECONDS=30;
 // 多声道取平均后，按整段取平均降到 16 kHz；对人声足够，不需要额外依赖。
 function downsample(channels,rate){
  const length=channels[0].length,step=rate/RATE,out=new Float32Array(Math.floor(length/step));
  for(let i=0;i<out.length;i++){
   const from=Math.floor(i*step),to=Math.max(from+1,Math.min(length,Math.floor((i+1)*step)));let sum=0;
   for(let j=from;j<to;j++)for(const c of channels)sum+=c[j];
   out[i]=sum/((to-from)*channels.length);
  }
  return out;
 }
 function encodeWav(samples){
  const size=Math.min(samples.length,MAX_SECONDS*RATE),view=new DataView(new ArrayBuffer(44+size*2));
  const text=(at,s)=>{for(let i=0;i<s.length;i++)view.setUint8(at+i,s.charCodeAt(i));};
  text(0,'RIFF');view.setUint32(4,36+size*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);
  view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,RATE,true);view.setUint32(28,RATE*2,true);
  view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,size*2,true);
  for(let i=0;i<size;i++){const s=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,s<0?s*0x8000:s*0x7FFF,true);}
  return view.buffer;
 }
 const supported=()=>typeof navigator!=='undefined'&&!!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined'&&!!(window.AudioContext||window.webkitAudioContext);
 let session=null;
 // 开始录音；到 30 秒自动停。onStop 在录音结束（手动或自动）时被调用一次。
 async function start(onStop){
  if(session)return;
  const stream=await navigator.mediaDevices.getUserMedia({audio:true}),recorder=new MediaRecorder(stream),chunks=[];
  recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
  const done=new Promise(resolve=>{recorder.onstop=()=>resolve(new Blob(chunks,{type:recorder.mimeType}));});
  session={recorder,stream,done,timer:setTimeout(()=>onStop&&onStop(),MAX_SECONDS*1000)};
  recorder.start();
 }
 // 停止并返回 WAV；cancel 为真时只释放麦克风。
 async function stop(cancel=false){
  const current=session;if(!current)return null;session=null;clearTimeout(current.timer);
  if(current.recorder.state!=='inactive')current.recorder.stop();
  const blob=await current.done;current.stream.getTracks().forEach(t=>t.stop());
  if(cancel||!blob.size)return null;
  const context=new (window.AudioContext||window.webkitAudioContext)();
  try{
   const raw=await blob.arrayBuffer();
   // 回调写法兼容旧版 Safari。
   const audio=await new Promise((resolve,reject)=>{const p=context.decodeAudioData(raw,resolve,reject);if(p?.catch)p.catch(reject);});
   const channels=[];for(let i=0;i<audio.numberOfChannels;i++)channels.push(audio.getChannelData(i));
   return encodeWav(downsample(channels,audio.sampleRate));
  }finally{context.close?.();}
 }
 return {RATE,MAX_SECONDS,downsample,encodeWav,supported,start,stop,recording:()=>!!session};
})();
if(typeof module!=='undefined')module.exports=voiceInput;
