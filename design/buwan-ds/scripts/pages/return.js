/* S3 隔天回归：接着聊（聚焦输入）、人设锚点展开、导出存档 @principle P1 P5 @scene S3
   对话流的发送 / 流式回复复用 chat.js。 */
(function () {
  'use strict';
  const input = document.getElementById('msg');

  document.addEventListener('click', e => {
    if (e.target.closest('[data-bw-continue]') && input) {
      input.focus();
      input.scrollIntoView({ block: 'nearest' });
    }
    if (e.target.closest('[data-bw-export]')) {
      window.bw && window.bw.toast({ text: '存档已导出：沈砚-2026-11-17.json', type: 'success' });
    }
  });

  const anchors = document.querySelector('[data-bw-anchors]');
  const label = document.querySelector('[data-bw-anchors-label]');
  if (anchors && label) {
    anchors.addEventListener('toggle', () => {
      label.textContent = anchors.open ? '收起人设锚点' : '查看人设锚点';
    });
  }
})();
