/* 组件规范页演示：纠正面板提交 → 原位留下修订痕迹 + 记忆类 Toast @principle P3 @scene S4 */
(function () {
  'use strict';
  document.addEventListener('submit', e => {
    const form = e.target.closest('[data-bw-correction-form]');
    if (!form) return;
    e.preventDefault();
    const field = form.querySelector('[data-bw-correction-field]');
    const input = field.querySelector('input');
    const value = input.value.trim();
    if (!value) {
      field.classList.add('is-error');
      input.setAttribute('aria-invalid', 'true');
      input.focus();
      return;
    }
    field.classList.remove('is-error');
    input.removeAttribute('aria-invalid');
    const item = document.getElementById('demo-corrected-item');
    if (item) {
      item.querySelector('.bw-memory-item__text').textContent = value;
      item.querySelector('.bw-memory-item__meta').textContent = '已更正 · 刚刚 · 之后按这个来';
    }
    window.bw.closeModal(form.closest('dialog').id);
    window.bw.toast({ type: 'memory', text: `已记住：${value}。之后按这个来` });
  });
})();
