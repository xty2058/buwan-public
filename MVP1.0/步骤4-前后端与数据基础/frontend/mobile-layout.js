'use strict';
// Visual viewport is a layout signal, never evidence of a physical keyboard/device test.
function viewportMetrics(layoutHeight, visualHeight, offsetTop=0){
 const finite=n=>typeof n==='number'&&Number.isFinite(n)&&n>0;
 return {height:Math.round(finite(visualHeight)?visualHeight:finite(layoutHeight)?layoutHeight:600),top:Math.max(0,Math.round(Number.isFinite(offsetTop)?offsetTop:0))};
}
function sizeComposer(input,stick=false){
 if(!input)return;
 const area=document.querySelector('#chat-scroll');
 const metrics=viewportMetrics(window.innerHeight,window.visualViewport?.height);
 input.style.height='auto';input.style.height=Math.min(Math.max(45,input.scrollHeight),Math.max(64,Math.min(144,Math.floor(metrics.height*.28))))+'px';
 const form=document.querySelector('#message-form');
 if(form)document.documentElement.style.setProperty('--composer-height',Math.ceil(form.getBoundingClientRect().height)+'px');
 if(stick&&area)area.scrollTop=area.scrollHeight;
}
(function installMobileLayout(){
 let pending=false;
 const update=()=>{
  pending=false;const vp=window.visualViewport,m=viewportMetrics(window.innerHeight,vp?.height,vp?.offsetTop||0);
  document.documentElement.style.setProperty('--visual-height',m.height+'px');document.documentElement.style.setProperty('--visual-top',m.top+'px');
  sizeComposer(document.querySelector('#draft'));
  window.dispatchEvent(new Event('appviewportchange'));
 };
 const schedule=()=>{if(!pending){pending=true;window.requestAnimationFrame(update);}};
 window.addEventListener('resize',schedule);window.addEventListener('orientationchange',schedule);
 window.visualViewport?.addEventListener('resize',schedule);window.visualViewport?.addEventListener('scroll',schedule);
 update();
})();
